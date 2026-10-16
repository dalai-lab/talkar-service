import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, WalletTransaction, CallLog, Agent, TalkarAdmin, Subscription, Notification, PhoneNumber, PhoneNumberRequest, SupportRequest, Invoice, AutomationAuditLog
from routers.admin import delete_customer_account, _execute_customer_purge


@pytest.mark.asyncio
async def test_unprovisioned_customer_deletion():
    """
    Test Case 1: Unprovisioned customer (dograh_org_id is None).
    Verify Talkar DB rows are wiped and Voice Agent DB wipe is skipped cleanly.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    cust = Customer(
        id=42,
        company_name="Unprovisioned Corp",
        dograh_org_id=None,
        dograh_user_id=None,
        status="pending_approval",
        billing_org_id=None,
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "select" in q_str and "from customers" in q_str:
            res.scalar_one_or_none.return_value = cust
            res.scalars.return_value.all.return_value = []
        else:
            res.rowcount = 1
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    res = await delete_customer_account(
        customer_id=42,
        cascade_suborgs=False,
        db=mock_db,
        current_admin=admin
    )

    assert res["success"] is True
    assert res["deleted_entities"]["voice_agent_db"] == "skipped — unprovisioned customer"
    assert res["deleted_entities"]["talkar_db"]["customers"] == 1
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_provisioned_customer_deletion():
    """
    Test Case 2: Provisioned customer with Dograh organization and Talkar resources.
    Verify all 22 Voice Agent steps and Talkar steps execute and purge resources across both DBs.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    cust = Customer(
        id=55,
        company_name="Provisioned Corp",
        dograh_org_id=101,
        dograh_user_id=88,
        status="active",
        billing_org_id=None,
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "select" in q_str and "from customers" in q_str:
            res.scalar_one_or_none.return_value = cust
            res.scalars.return_value.all.return_value = []
        else:
            res.rowcount = 1
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_ddb = MagicMock()
    async def mock_ddb_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "select count(wr.id)" in q_str:
            res.scalar.return_value = 0  # 0 active calls
        elif "select user_id from organization_users" in q_str:
            res.all.return_value = [(88,)]
        elif "select count(*) from organization_users where user_id" in q_str:
            res.scalar.return_value = 0  # user only in this org
        else:
            res.rowcount = 2
            res.scalar.return_value = None
            res.all.return_value = []
        return res

    mock_ddb.execute = AsyncMock(side_effect=mock_ddb_execute)
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
         patch("routers.admin._evict_customer_redis_keys", AsyncMock()) as mock_redis:
        res = await delete_customer_account(
            customer_id=55,
            cascade_suborgs=False,
            db=mock_db,
            current_admin=admin
        )

    assert res["success"] is True
    assert mock_redis.called
    assert res["deleted_entities"]["dograh_user_purged"] is True
    assert res["deleted_entities"]["voice_agent_db"]["organizations"] == 2
    assert res["deleted_entities"]["voice_agent_db"]["users_purged"] == 1
    assert res["deleted_entities"]["talkar_db"]["customers"] == 1
    assert res["deleted_entities"]["talkar_db"]["agents"] == 1
    assert res["deleted_entities"]["talkar_db"]["call_logs"] == 1
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_active_calls_guard_blocks_deletion_409():
    """
    Test Case 3: In-flight active calls exist for the customer's Dograh organization.
    Verify endpoint raises HTTP 409 Conflict and 0 records are deleted.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    cust = Customer(
        id=10,
        company_name="Active Calls Ltd",
        dograh_org_id=100,
        dograh_user_id=88,
        status="active",
        billing_org_id=None,
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from customers" in q_str:
            res.scalar_one_or_none.return_value = cust
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_ddb = MagicMock()
    mock_ddb.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=3)))
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb):
        with pytest.raises(HTTPException) as exc_info:
            await delete_customer_account(
                customer_id=10,
                cascade_suborgs=False,
                db=mock_db,
                current_admin=admin
            )

    assert exc_info.value.status_code == 409
    assert "active calls currently in progress" in exc_info.value.detail


@pytest.mark.asyncio
async def test_master_customer_with_suborgs_blocks_400():
    """
    Test Case 3: Customer is a Master Account with child sub-orgs and cascade_suborgs is False.
    Verify endpoint raises HTTP 400 Bad Request.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    master = Customer(
        id=1,
        company_name="Master Agency",
        dograh_org_id=100,
        dograh_user_id=10,
        status="active",
        billing_org_id=None,
    )
    sub1 = Customer(
        id=2,
        company_name="Sub Agency 1",
        billing_org_id=1,
    )

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = [sub1]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with pytest.raises(HTTPException) as exc_info:
        await delete_customer_account(
            customer_id=1,
            cascade_suborgs=False,
            db=mock_db,
            current_admin=admin
        )

    assert exc_info.value.status_code == 400
    assert "master billing account with 1 active sub-organizations" in exc_info.value.detail


@pytest.mark.asyncio
async def test_master_customer_cascade_suborgs_deletes_all():
    """
    Test Case 4: Master customer with child sub-orgs and cascade_suborgs is True.
    Verify child sub-organizations are deleted first, then master customer.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    master = Customer(
        id=1,
        company_name="Master Agency",
        dograh_org_id=100,
        dograh_user_id=10,
        status="active",
        billing_org_id=None,
    )
    sub1 = Customer(
        id=2,
        company_name="Sub Agency 1",
        dograh_org_id=200,
        dograh_user_id=20,
        status="active",
        billing_org_id=1,
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = [sub1]
        elif "where customers.id == 1" in q_str or "customers.id = 1" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "where customers.id == 2" in q_str or "customers.id = 2" in q_str:
            res.scalar_one_or_none.return_value = sub1
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
            res.scalars.return_value.all.return_value = [sub1]
        else:
            res.rowcount = 1
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_ddb = MagicMock()
    # Mock active calls check = 0
    mock_ddb.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0), rowcount=1, all=MagicMock(return_value=[])))
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
         patch("routers.admin._evict_customer_redis_keys", AsyncMock()):
        res = await delete_customer_account(
            customer_id=1,
            cascade_suborgs=True,
            db=mock_db,
            current_admin=admin
        )

    assert res["success"] is True
    assert res["cascade_suborgs"] is True
    assert res["deleted_suborgs_count"] == 1
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_suborg_deletion_preserves_master_wallet_transactions():
    """
    Test Case 5: When deleting a child sub-organization, verify sub_org_id is set to NULL
    on wallet_transactions rather than deleting them, preserving master ledger integrity.
    """
    sub = Customer(
        id=2,
        company_name="Child Sub Org",
        dograh_org_id=200,
        dograh_user_id=20,
        status="active",
        billing_org_id=1,  # Sub-org pointing to master
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    executed_queries = []

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        executed_queries.append(q_str)
        res = MagicMock()
        if "from customers" in q_str:
            res.scalar_one_or_none.return_value = sub
        else:
            res.rowcount = 1
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_ddb = MagicMock()
    mock_ddb.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0), rowcount=1, all=MagicMock(return_value=[])))
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
         patch("routers.admin._evict_customer_redis_keys", AsyncMock()):
        stats = await _execute_customer_purge(target_id=2, db=mock_db)

    assert stats["is_sub_org"] is True
    assert stats["talkar_db"]["wallet_transactions_unlinked"] == 1
    assert stats["talkar_db"]["wallet_transactions"] == 0

    # Verify UPDATE was executed on wallet_transactions instead of DELETE
    update_executed = any("update wallet_transactions" in q and "sub_org_id" in q for q in executed_queries)
    assert update_executed is True


@pytest.mark.asyncio
async def test_multi_org_user_preservation():
    """
    Test Case 6: User belongs to Organization A and Organization B.
    When Organization A is deleted, verify user is NOT purged because they still have org memberships.
    """
    cust = Customer(
        id=5,
        company_name="Agency Alpha",
        dograh_org_id=105,
        dograh_user_id=99,
        status="active",
        billing_org_id=None,
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        res = MagicMock()
        if "from customers" in str(query).lower():
            res.scalar_one_or_none.return_value = cust
        else:
            res.rowcount = 1
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_ddb = MagicMock()
    user_queries = []

    async def mock_ddb_execute(query, params=None, *args, **kwargs):
        q_str = str(query).lower()
        user_queries.append((q_str, params))
        res = MagicMock()
        if "select user_id from organization_users" in q_str:
            res.all.return_value = [(99,)]
        elif "select count(*) from organization_users where user_id" in q_str:
            # User still belongs to 1 other organization!
            res.scalar.return_value = 1
        else:
            res.rowcount = 1
            res.scalar.return_value = None
            res.all.return_value = []
        return res

    mock_ddb.execute = AsyncMock(side_effect=mock_ddb_execute)
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
         patch("routers.admin._evict_customer_redis_keys", AsyncMock()):
        stats = await _execute_customer_purge(target_id=5, db=mock_db)

    # User 99 should NOT have been purged
    assert stats["dograh_user_purged"] is False
    assert stats["voice_agent_db"]["users_purged"] == 0

    # Verify no DELETE FROM users was executed for user 99
    user_deleted = any("delete from users" in q[0] for q in user_queries)
    assert user_deleted is False


@pytest.mark.asyncio
async def test_single_org_user_reclaimed():
    """
    Test Case 7: User belongs ONLY to Organization A.
    When Organization A is deleted, user should be purged from users & user_configurations,
    freeing their email for clean re-registration.
    """
    cust = Customer(
        id=6,
        company_name="Single User Co",
        dograh_org_id=106,
        dograh_user_id=77,
        status="active",
        billing_org_id=None,
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        res = MagicMock()
        if "from customers" in str(query).lower():
            res.scalar_one_or_none.return_value = cust
        else:
            res.rowcount = 1
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_ddb = MagicMock()
    user_queries = []

    async def mock_ddb_execute(query, params=None, *args, **kwargs):
        q_str = str(query).lower()
        user_queries.append((q_str, params))
        res = MagicMock()
        if "select user_id from organization_users" in q_str:
            res.all.return_value = [(77,)]
        elif "select count(*) from organization_users where user_id" in q_str:
            # User belongs to 0 other organizations after membership removal
            res.scalar.return_value = 0
        else:
            res.rowcount = 1
            res.scalar.return_value = None
            res.all.return_value = []
        return res

    mock_ddb.execute = AsyncMock(side_effect=mock_ddb_execute)
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
         patch("routers.admin._evict_customer_redis_keys", AsyncMock()):
        stats = await _execute_customer_purge(target_id=6, db=mock_db)

    # User 77 should have been purged cleanly
    assert stats["dograh_user_purged"] is True
    assert stats["voice_agent_db"]["users_purged"] == 1

    # Verify DELETE FROM users and DELETE FROM user_configurations were executed
    user_deleted = any("delete from users" in q[0] for q in user_queries)
    config_deleted = any("delete from user_configurations" in q[0] for q in user_queries)
    assert user_deleted is True
    assert config_deleted is True
