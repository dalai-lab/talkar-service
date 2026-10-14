import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog
from services import billing_service
from routers.billing import check_quota, DograhQuotaRequest
from routers.customers import (
    get_suborg_spending_limits,
    update_suborg_spending_limits,
    SubOrgSpendingLimitsRequest,
)


@pytest.mark.asyncio
async def test_sync_wallet_block_policy_isolates_rogue_suborg_and_prevents_starvation():
    """
    Verify Issue 5: When Sub-Org 1 exceeds its monthly spending cap,
    sync_wallet_block_policy blocks ONLY Sub-Org 1. Master and Sub-Org 2 remain restored!
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub1 = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
        onboarding_form={"monthly_spending_cap_paise": 100000},  # ₹1,000 cap
    )
    sub2 = Customer(
        id=3,
        dograh_org_id=30,
        status="active",
        billing_org_id=1,
        onboarding_form={"monthly_spending_cap_paise": 500000},  # ₹5,000 cap
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)  # ₹5,000 in master wallet
    sub = Subscription(id=1, customer_id=1, plan="starter")

    mock_db = MagicMock()

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_sub_orgs = MagicMock()
    res_sub_orgs.scalars.return_value.all.return_value = [sub1, sub2]

    # sub1 has spent 120000 paise (exceeded ₹1000 cap), sub2 has spent 20000 paise (within cap)
    mock_db.execute = AsyncMock(side_effect=[
        res_master,
        res_sub,
        res_sub_orgs,
    ])

    async def mock_month_spend(db, cust_id):
        if cust_id == 2:
            return 120000  # exceeded 100000 cap
        elif cust_id == 3:
            return 20000   # within 500000 cap
        return 0

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.billing_service.get_customer_month_spend", side_effect=mock_month_spend), \
         patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block, \
         patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore:

        await billing_service.sync_wallet_block_policy(mock_db, customer_id=1)

    # Sub-Org 1 (org 20) must be blocked because it exceeded cap
    assert mock_block.await_count == 1
    mock_block.assert_awaited_once_with(20)

    # Master (org 10) and Sub-Org 2 (org 30) must be restored!
    assert mock_restore.await_count == 2
    restored_orgs = [call.args[0] for call in mock_restore.await_args_list]
    assert 10 in restored_orgs
    assert 30 in restored_orgs
    assert 20 not in restored_orgs


@pytest.mark.asyncio
async def test_sync_wallet_block_policy_applies_individual_concurrency_cap():
    """
    Verify Issue 5: When Sub-Org 1 has concurrent_call_limit=2 configured,
    Dograh CONCURRENT_CALL_LIMIT is set to 2 for Sub-Org 1, preserving master fleet pool.
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub1 = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
        onboarding_form={"concurrent_call_limit": 2},  # Capped at 2 concurrent calls
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    sub = Subscription(id=1, customer_id=1, plan="starter")  # Starter tier limit = 2 or higher

    mock_db = MagicMock()

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_sub_orgs = MagicMock()
    res_sub_orgs.scalars.return_value.all.return_value = [sub1]

    mock_db.execute = AsyncMock(side_effect=[
        res_master,
        res_sub,
        res_sub_orgs,
    ])

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore:

        await billing_service.sync_wallet_block_policy(mock_db, customer_id=1)

    # Both master and sub1 restored, with sub1 having concurrency limit = 2
    assert mock_restore.await_count == 2
    sub1_call = [call for call in mock_restore.await_args_list if call.args[0] == 20][0]
    assert sub1_call.args[2] == 2  # limit=2


@pytest.mark.asyncio
async def test_check_quota_rejects_suborg_exceeding_spending_cap():
    """
    Verify Issue 5: check_quota returns has_quota=False with 'spending_cap_exceeded'
    when a sub-org exceeds its monthly rupee cap.
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub1 = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
        onboarding_form={"monthly_spending_cap_paise": 100000},  # ₹1,000 cap
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)

    mock_db = MagicMock()

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub1

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute = AsyncMock(side_effect=[
        res_sub,     # lookup customer by org 20
        res_master,  # lookup master customer
    ])

    quota_req = DograhQuotaRequest(
        organization_id=20,
        mode="outbound",
        active_calls=1,
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.billing_service.get_customer_month_spend", AsyncMock(return_value=125000)):

        result = await check_quota(data=quota_req, db=mock_db, _token="test-token")

    assert result["has_quota"] is False
    assert result["reason"] == "spending_cap_exceeded"
    assert result["month_spend_paise"] == 125000
    assert result["monthly_spending_cap_paise"] == 100000


@pytest.mark.asyncio
async def test_check_quota_rejects_suborg_exceeding_individual_concurrency_limit():
    """
    Verify Issue 5: check_quota returns has_quota=False with 'sub_org_concurrency_limit_exceeded'
    when a sub-org exceeds its assigned concurrent_call_limit.
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub1 = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
        onboarding_form={"concurrent_call_limit": 2},  # Limit = 2
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=5000000)  # Plenty of funds
    sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)

    mock_db = MagicMock()

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub1

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    res_subscription = MagicMock()
    res_subscription.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[
        res_sub,          # lookup customer by org 20
        res_master,       # lookup master customer
        res_subscription, # lookup subscription
    ])

    quota_req = DograhQuotaRequest(
        organization_id=20,
        mode="outbound",
        active_calls=3,   # 3 active calls > limit 2
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.billing_service.get_customer_month_spend", AsyncMock(return_value=1000)):

        result = await check_quota(data=quota_req, db=mock_db, _token="test-token")

    assert result["has_quota"] is False
    assert result["reason"] == "sub_org_concurrency_limit_exceeded"
    assert result["active_calls"] == 3
    assert result["concurrent_call_limit"] == 2


@pytest.mark.asyncio
async def test_suborg_spending_limits_privileged_access():
    """
    Verify Issue 5:
    - Master owner CAN configure sub-org spending limits.
    - Sub-org owner CANNOT configure sub-org spending limits (403 Forbidden).
    - Single-org account cannot configure sub-org spending limits (400 Bad Request).
    """
    master = Customer(id=1, dograh_org_id=10, contact_email="agency@master.com", billing_org_id=None)
    sub1 = Customer(id=2, dograh_org_id=20, contact_email="client@suborg.com", billing_org_id=1)

    mock_db = MagicMock()

    # 1. Attempt update by Sub-Org tenant -> 403 Forbidden
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub1
    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute = AsyncMock(side_effect=[res_sub, res_master])

    req = SubOrgSpendingLimitsRequest(monthly_spending_cap_paise=200000, concurrent_call_limit=3)

    with patch("routers.customers.resolve_authenticated_email", return_value="client@suborg.com"):
        with pytest.raises(HTTPException) as exc_info:
            await update_suborg_spending_limits(
                dograh_org_id=20,
                data=req,
                db=mock_db,
                x_talkar_email="client@suborg.com",
                authorization="Bearer test-user-token"
            )
    assert exc_info.value.status_code == 403
    assert "Master Organization owner" in exc_info.value.detail

    # 2. Attempt update on Master / Single-org account -> 400 Bad Request
    res_single = MagicMock()
    res_single.scalar_one_or_none.return_value = master
    mock_db.execute = AsyncMock(return_value=res_single)

    with patch("routers.customers.resolve_authenticated_email", return_value="agency@master.com"):
        with pytest.raises(HTTPException) as exc_info:
            await update_suborg_spending_limits(
                dograh_org_id=10,
                data=req,
                db=mock_db,
                x_talkar_email="agency@master.com",
                authorization="Bearer test-user-token"
            )
    assert exc_info.value.status_code == 400
    assert "only be configured for sub-organizations" in exc_info.value.detail

    # 3. Successful update by Master Organization owner
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub1
    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute = AsyncMock(side_effect=[res_sub, res_master])
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    with patch("routers.customers.resolve_authenticated_email", return_value="agency@master.com"), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.billing_service.get_customer_month_spend", AsyncMock(return_value=50000)):

        resp = await update_suborg_spending_limits(
            dograh_org_id=20,
            data=req,
            db=mock_db,
            x_talkar_email="agency@master.com",
            authorization="Bearer test-user-token"
        )

    assert resp["status"] == "ok"
    assert resp["monthly_spending_cap_paise"] == 200000
    assert resp["concurrent_call_limit"] == 3
    assert resp["current_month_spend_paise"] == 50000
    assert resp["is_blocked_by_cap"] is False
    mock_sync.assert_awaited_once_with(mock_db, 2)


@pytest.mark.asyncio
async def test_get_suborg_spending_limits_endpoint():
    """Verify GET /customers/by-org/{dograh_org_id}/spending-limits returns limits and spend status."""
    sub1 = Customer(
        id=2,
        dograh_org_id=20,
        contact_email="client@suborg.com",
        billing_org_id=1,
        onboarding_form={"monthly_spending_cap_paise": 150000, "concurrent_call_limit": 4},
    )

    mock_db = MagicMock()
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub1
    mock_db.execute = AsyncMock(return_value=res_sub)

    with patch("routers.customers.verify_customer_access", AsyncMock()), \
         patch("services.billing_service.get_customer_month_spend", AsyncMock(return_value=160000)):

        resp = await get_suborg_spending_limits(
            dograh_org_id=20,
            db=mock_db,
            x_talkar_email="client@suborg.com",
            authorization="Bearer token"
        )

    assert resp["dograh_org_id"] == 20
    assert resp["monthly_spending_cap_paise"] == 150000
    assert resp["concurrent_call_limit"] == 4
    assert resp["current_month_spend_paise"] == 160000
    assert resp["is_blocked_by_cap"] is True
