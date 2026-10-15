import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.customers import set_billing_account_type, SetBillingAccountTypeRequest


@pytest.mark.asyncio
async def test_blocker_11_shared_linking_syncs_concurrency_group_and_invalidates_redis():
    """
    Test Blocker 11:
    When linking to a shared wallet, update CONCURRENCY_GROUP_ID in Dograh to master_dograh_id
    and invalidate Redis key org_concurrency_group:{id}.
    """
    sub_org = Customer(
        id=2,
        contact_email="owner@test.com",
        dograh_org_id=200,
        dograh_user_id=50,
        billing_org_id=None,
        status="pending_deposit"
    )
    master = Customer(
        id=1,
        contact_email="owner@test.com",
        dograh_org_id=100,
        dograh_user_id=50,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    res_sub = MagicMock()
    res_sub.scalars.return_value.first.return_value = sub_org

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute.side_effect = [res_sub, res_master]

    with patch("routers.customers.verify_customer_access", AsyncMock()), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)), \
         patch("services.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert, \
         patch("services.redis_client.invalidate_concurrency_group", AsyncMock()) as mock_inval:

        resp = await set_billing_account_type(
            dograh_org_id=200,
            data=SetBillingAccountTypeRequest(account_type="shared"),
            db=mock_db,
            x_talkar_email="owner@test.com"
        )

    assert resp["status"] == "ok"
    assert resp["account_type"] == "shared"
    assert sub_org.billing_org_id == 1

    # Verify Dograh was updated with master_dograh_id = 100
    mock_upsert.assert_awaited_once_with(200, "CONCURRENCY_GROUP_ID", {"value": 100})
    # Verify Redis key for org 200 was invalidated
    mock_inval.assert_awaited_once_with(200)


@pytest.mark.asyncio
async def test_blocker_11_and_12_detach_to_independent_blocks_and_transitions_status():
    """
    Test Blocker 11 & 12:
    When switching an active workspace to independent:
    - Customer transitions to pending_deposit
    - Calls block_org_calls on Dograh
    - Resets CONCURRENCY_GROUP_ID to its own dograh_org_id
    - Invalidates Redis key org_concurrency_group:{id}
    - Returns redirect_url instructing the customer to fund their independent wallet
    """
    sub_org = Customer(
        id=2,
        contact_email="owner@test.com",
        dograh_org_id=200,
        dograh_user_id=50,
        billing_org_id=1,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    # Query 1: lookup customer
    res_sub = MagicMock()
    res_sub.scalars.return_value.first.return_value = sub_org

    # Query 2: wallet check (none exists -> created)
    res_wallet = MagicMock()
    res_wallet.scalar_one_or_none.return_value = None

    # Query 3: subscription check (none exists -> created)
    res_subscr = MagicMock()
    res_subscr.scalar_one_or_none.return_value = None

    mock_db.execute.side_effect = [res_sub, res_wallet, res_subscr]

    with patch("routers.customers.verify_customer_access", AsyncMock()), \
         patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block, \
         patch("services.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert, \
         patch("services.redis_client.invalidate_concurrency_group", AsyncMock()) as mock_inval:

        resp = await set_billing_account_type(
            dograh_org_id=200,
            data=SetBillingAccountTypeRequest(account_type="independent"),
            db=mock_db,
            x_talkar_email="owner@test.com"
        )

    # Blocker 12 assertions
    assert sub_org.billing_org_id is None
    assert sub_org.status == "pending_deposit"
    mock_block.assert_awaited_once_with(200)
    assert resp["redirect_url"] == "/wallet?deposit_required=true"
    assert resp["customer_status"] == "pending_deposit"

    # Blocker 11 assertions: reset concurrency group to own org ID
    mock_upsert.assert_awaited_once_with(200, "CONCURRENCY_GROUP_ID", {"value": 200})
    mock_inval.assert_awaited_once_with(200)
