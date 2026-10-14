import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet
from routers.admin import (
    suspend_customer,
    unsuspend_customer,
    update_customer,
    SuspendCustomerRequest,
    CustomerUpdateRequest,
)


@pytest.mark.asyncio
async def test_suspend_master_account_cascades_call_blocks_to_all_suborgs():
    """
    Verify Issue 11:
    When suspending a master account via POST /admin/customers/{id}/suspend,
    calls MUST be blocked on the master's Dograh org AND cascaded to all sub-orgs
    in Dograh DB, preventing enforcement bypass.
    """
    master = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None, onboarding_form={})
    sub_org_1 = Customer(id=2, dograh_org_id=200, status="active", billing_org_id=1, onboarding_form={})
    sub_org_2 = Customer(id=3, dograh_org_id=300, status="agent_building", billing_org_id=1, onboarding_form={})

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org_1, sub_org_2]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_suspended", AsyncMock()):

        req = SuspendCustomerRequest(reason="TOS violation", custom_message="Your account is suspended")
        admin_user = MagicMock()
        res = await suspend_customer(1, req, db=mock_db, current_admin=admin_user)

    assert res["status"] == "suspended"
    assert master.status == "suspended"
    assert master.onboarding_form["suspension_reason"] == "TOS violation"

    # Verify Dograh block_org_calls was called for master AND all sub-orgs
    blocked_org_ids = [call.args[0] for call in mock_block.call_args_list]
    assert 100 in blocked_org_ids, "Master org must be blocked in Dograh"
    assert 200 in blocked_org_ids, "Sub-org 1 must be blocked in Dograh"
    assert 300 in blocked_org_ids, "Sub-org 2 must be blocked in Dograh"

    # Verify sync_wallet_block_policy was invoked
    mock_sync.assert_awaited_with(mock_db, 1)


@pytest.mark.asyncio
async def test_unsuspend_suborg_resolves_master_subscription_and_preserves_group_id():
    """
    Verify Issue 11:
    When unsuspending a sub-org via POST /admin/customers/{id}/unsuspend:
    1. It must resolve the master's Subscription (e.g. Pro), not default to Starter (2 calls).
    2. It must pass group_id=master_dograh_id so the sub-org remains linked to master's concurrency pool.
    """
    master = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None, onboarding_form={})
    sub_org = Customer(id=2, dograh_org_id=200, status="suspended", billing_org_id=1, onboarding_form={"suspension_reason": "billing"})

    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=950)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master if cid == 1 else sub_org
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_org
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync:

        admin_user = MagicMock()
        res = await unsuspend_customer(2, db=mock_db, current_admin=admin_user)

    assert res["status"] == "active"
    assert sub_org.status == "active"
    assert "suspension_reason" not in sub_org.onboarding_form

    # Verify restore_org_calls was called with master tier and group_id
    mock_restore.assert_awaited_once()
    org_id = mock_restore.call_args.args[0]
    tier = mock_restore.call_args.args[1]
    limit = mock_restore.call_args.args[2]
    kwargs = mock_restore.call_args.kwargs

    assert org_id == 200
    assert tier == "pro", "Sub-org must inherit master's 'pro' plan tier"
    assert limit == 10, "Sub-org must inherit master's 10-call concurrency limit"
    assert kwargs.get("group_id") == 100, "Sub-org must retain master group_id=100 (concurrency pool linkage)"
    assert kwargs.get("is_custom_cap") is False, "Uncustomized sub-org must have is_custom_cap=False"

    # Verify sync_wallet_block_policy was invoked with master_id
    mock_sync.assert_awaited_with(mock_db, 1)


@pytest.mark.asyncio
async def test_unsuspend_suborg_with_custom_cap_preserves_custom_cap_and_group_id():
    """
    Verify Issue 11:
    When unsuspending a sub-org that has an explicit custom cap (e.g. 3 calls),
    it restores with limit=3, is_custom_cap=True, and group_id=100.
    """
    master = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None, onboarding_form={})
    sub_org = Customer(
        id=2,
        dograh_org_id=200,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"concurrent_call_limit": 3}
    )

    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=950)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master if cid == 1 else sub_org
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_org
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):

        admin_user = MagicMock()
        res = await unsuspend_customer(2, db=mock_db, current_admin=admin_user)

    assert res["status"] == "active"
    mock_restore.assert_awaited_once()
    org_id = mock_restore.call_args.args[0]
    tier = mock_restore.call_args.args[1]
    limit = mock_restore.call_args.args[2]
    kwargs = mock_restore.call_args.kwargs

    assert org_id == 200
    assert tier == "pro"
    assert limit == 3, "Sub-org must respect its configured custom cap of 3"
    assert kwargs.get("group_id") == 100
    assert kwargs.get("is_custom_cap") is True


@pytest.mark.asyncio
async def test_update_customer_patch_status_suspends_and_unsuspends_with_group_id():
    """
    Verify PATCH /admin/customers/{id} correctly handles sub-org and master unsuspend/suspend:
    - Unsuspending a sub-org uses master's tier, limits, and group_id
    - Suspending a master cascades blocks to all sub-orgs
    """
    master = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None, onboarding_form={})
    sub_org = Customer(id=2, dograh_org_id=200, status="suspended", billing_org_id=1, onboarding_form={})
    master_sub = Subscription(id=1, customer_id=1, plan="elite", per_minute_rate_paise=1200)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master if cid == 1 else sub_org
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_org
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    # 1. Unsuspend sub-org via PATCH
    with patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):

        res = await update_customer(2, CustomerUpdateRequest(status="active"), db=mock_db, current_admin=MagicMock())
        assert res["status"] == "success"

        mock_restore.assert_awaited_once()
        org_id = mock_restore.call_args.args[0]
        tier = mock_restore.call_args.args[1]
        limit = mock_restore.call_args.args[2]
        kwargs = mock_restore.call_args.kwargs

        assert org_id == 200
        assert tier == "elite"
        assert limit == 50  # Elite tier limit
        assert kwargs.get("group_id") == 100

    # 2. Suspend master via PATCH
    with patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync:

        res = await update_customer(1, CustomerUpdateRequest(status="suspended"), db=mock_db, current_admin=MagicMock())
        assert res["status"] == "success"

        blocked_ids = [call.args[0] for call in mock_block.call_args_list]
        assert 100 in blocked_ids, "Master org must be blocked"
        assert 200 in blocked_ids, "Sub-org must be blocked when master is suspended"
        mock_sync.assert_awaited_with(mock_db, 1)
