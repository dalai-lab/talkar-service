import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet
from routers.billing import (
    create_upgrade_order,
    UpgradeOrderRequest,
    confirm_topup,
    ConfirmTopupRequest,
    razorpay_webhook,
    deduct_for_run,
    DograhDeductRequest,
)
from config import TIER_CONFIG


@pytest.mark.asyncio
async def test_sub_org_create_upgrade_order_synchronizes_master_and_all_sub_orgs():
    """Verify Issue 30: Upgrading from sub-org updates master subscription and all sub-orgs."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, onboarding_form={})
    sub_org_1 = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})
    sub_org_2 = Customer(id=3, dograh_org_id=30, status="active", billing_org_id=1, onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub_sub_1 = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)
    sub_sub_2 = Subscription(id=3, customer_id=3, plan="starter", per_minute_rate_paise=600)

    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)
    subs_by_customer = {1: master_sub, 2: sub_sub_1, 3: sub_sub_2}

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org_1
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            try:
                target_cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = subs_by_customer.get(target_cid, master_sub)
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org_1, sub_org_2]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = UpgradeOrderRequest(dograh_org_id=20, requested_tier="growth")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block, \
         patch("services.notification_service.notify_customer_tier_upgraded", AsyncMock()):

        res = await create_upgrade_order(req, db=mock_db)

    assert res["status"] == "upgraded_from_wallet"
    assert res["requested_tier"] == "growth"

    # Master subscription and onboarding form must be updated to growth
    assert master_sub.plan == "growth"
    assert master_sub.per_minute_rate_paise == TIER_CONFIG["growth"]["per_minute_rate_paise"]
    assert master.onboarding_form.get("approved_tier") == "growth"

    # All sub-orgs must also be updated
    assert sub_sub_1.plan == "growth"
    assert sub_sub_2.plan == "growth"
    assert sub_org_1.onboarding_form.get("approved_tier") == "growth"
    assert sub_org_2.onboarding_form.get("approved_tier") == "growth"

    # Provisioning must cascade to master and active sub-orgs
    prov_calls = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_calls, "Master customer must be reprovisioned"
    assert 2 in prov_calls, "Sub org 1 must be reprovisioned"
    assert 3 in prov_calls, "Sub org 2 must be reprovisioned"

    # Wallet block policy must be synced for master
    mock_sync_block.assert_awaited_once_with(mock_db, 1)


@pytest.mark.asyncio
async def test_sub_org_confirm_topup_with_requested_tier_synchronizes_master_and_all_sub_orgs():
    """Verify Issue 30: confirm_topup with requested_tier from sub-org updates master and all sub-orgs."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, onboarding_form={})
    sub_org_1 = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})
    sub_org_2 = Customer(id=3, dograh_org_id=30, status="active", billing_org_id=1, onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub_sub_1 = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)
    sub_sub_2 = Subscription(id=3, customer_id=3, plan="starter", per_minute_rate_paise=600)

    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1500000)
    subs_by_customer = {1: master_sub, 2: sub_sub_1, 3: sub_sub_2}

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org_1
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            try:
                target_cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = subs_by_customer.get(target_cid, master_sub)
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org_1, sub_org_2]
        elif "wallet_transactions" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = ConfirmTopupRequest(
        dograh_org_id=20,
        razorpay_order_id="order_sub_upg_456",
        razorpay_payment_id="pay_sub_upg_456",
        razorpay_signature="mock_signature",
        amount_paise=1000000,
        requested_tier="pro"
    )

    with patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block, \
         patch("services.notification_service.notify_customer_topup_successful", AsyncMock()):

        res = await confirm_topup(req, db=mock_db)

    assert res["status"] == "ok"
    assert master_sub.plan == "pro"
    assert master_sub.per_minute_rate_paise == TIER_CONFIG["pro"]["per_minute_rate_paise"]
    assert master.onboarding_form.get("approved_tier") == "pro"

    assert sub_sub_1.plan == "pro"
    assert sub_sub_2.plan == "pro"
    assert sub_org_1.onboarding_form.get("approved_tier") == "pro"
    assert sub_org_2.onboarding_form.get("approved_tier") == "pro"

    prov_calls = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_calls, "Master must be reprovisioned"
    assert 2 in prov_calls, "Sub org 1 must be reprovisioned"
    assert 3 in prov_calls, "Sub org 2 must be reprovisioned"


@pytest.mark.asyncio
async def test_sub_org_missing_subscription_created_during_upgrade():
    """Verify that if master or sub-org subscriptions do not exist, they are created during upgrade."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, onboarding_form={})
    sub_org = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)

    mock_db = MagicMock()
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None  # No prior subscription
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = UpgradeOrderRequest(dograh_org_id=20, requested_tier="growth")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_tier_upgraded", AsyncMock()):

        res = await create_upgrade_order(req, db=mock_db)

    assert res["status"] == "upgraded_from_wallet"
    # Check that subscriptions were created and added to session for both master and sub-org
    created_subs = [obj for obj in added_objects if isinstance(obj, Subscription)]
    cids = {s.customer_id: s for s in created_subs}
    assert 1 in cids
    assert cids[1].plan == "growth"
    assert cids[1].per_minute_rate_paise == TIER_CONFIG["growth"]["per_minute_rate_paise"]

    assert 2 in cids
    assert cids[2].plan == "growth"
    assert cids[2].per_minute_rate_paise == TIER_CONFIG["growth"]["per_minute_rate_paise"]


@pytest.mark.asyncio
async def test_sub_org_webhook_upgrade_updates_master_and_fleet():
    """Verify Razorpay webhook topup with requested_tier initiated by sub-org updates master and fleet."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, onboarding_form={})
    sub_org = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub_sub = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1500000)

    subs = {1: master_sub, 2: sub_sub}

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        ret = None
        if "WHERE customers.setup_fee_order_id" in q_str:
            ret = None
        elif "WHERE customers.id" in q_str:
            try:
                cid = int(query.whereclause.right.value)
                ret = sub_org if cid == 2 else master
            except Exception as e:
                ret = sub_org
        elif "WHERE subscriptions.customer_id" in q_str:
            try:
                target_cid = int(query.whereclause.right.value)
                ret = subs.get(target_cid, master_sub)
            except Exception as e:
                ret = master_sub
        elif "WHERE customers.billing_org_id" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org]
            return mock_res
        elif "wallet_transactions" in q_str:
            ret = None
        elif "WHERE wallets.customer_id" in q_str:
            ret = master_wallet

        mock_res.scalar_one_or_none.return_value = ret
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    mock_request = MagicMock()
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_hook_123",
                    "order_id": "order_hook_123",
                    "amount": 1000000,
                    "notes": {
                        "customer_id": "2",  # sub-org customer ID in notes
                        "requested_tier": "pro",
                    }
                }
            }
        }
    }
    import json
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
    mock_request.headers = {"X-Razorpay-Signature": "valid_signature"}

    with patch("services.razorpay_client.verify_webhook_signature", return_value=True), \
         patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):

        res = await razorpay_webhook(mock_request, db=mock_db)

    assert res["status"] == "ok"
    # Master plan and sub plan are both pro
    assert master_sub.plan == "pro"
    assert master.onboarding_form.get("approved_tier") == "pro"
    assert sub_sub.plan == "pro"
    assert sub_org.onboarding_form.get("approved_tier") == "pro"

    # Provisioning run for master and sub-org
    prov_calls = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_calls
    assert 2 in prov_calls
    mock_restore.assert_awaited()


@pytest.mark.asyncio
async def test_sub_org_upgraded_tier_affects_call_deduction_rate():
    """Verify that upgraded tier updates the per-minute billing rate for subsequent call deductions."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub_org = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={"approved_tier": "pro"})
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)

    mock_db = MagicMock()
    added_txns = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_txns.append(obj))

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        elif "call_logs.dograh_run_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "agents" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "RETURNING" in q_str:
            master_wallet.balance_paise = 498200
            mock_res.scalar_one_or_none.return_value = master_wallet
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    # Duration: 60s (1 minute). On Pro tier, 1800 paise (₹18.00). On Starter, 600 paise (₹6.00).
    req = DograhDeductRequest(
        organization_id=20,
        workflow_run_id=1001,
        duration_seconds=60,
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):

        res = await deduct_for_run(req, db=mock_db, _token="valid")

    assert res["status"] == "ok"
    assert res["cost_paise"] == 1800, "Call deduction must reflect the upgraded Pro rate (1800 paise), not old Starter rate (600 paise)"
    assert res["new_balance_paise"] == 498200
