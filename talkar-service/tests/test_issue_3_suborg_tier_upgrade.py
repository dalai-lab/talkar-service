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
)
from config import TIER_CONFIG


@pytest.mark.asyncio
async def test_sub_org_create_upgrade_order_updates_master_and_all_sub_orgs():
    """Verify Issue 3: Upgrading tier from a sub-org workspace updates master and all sister sub-orgs."""
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
         patch("services.notification_service.notify_customer_tier_upgraded", AsyncMock()):

        res = await create_upgrade_order(req, db=mock_db)

    assert res["status"] == "upgraded_from_wallet"
    assert res["requested_tier"] == "growth"

    # Master subscription and onboarding form must be updated
    assert master_sub.plan == "growth"
    assert master_sub.per_minute_rate_paise == TIER_CONFIG["growth"]["per_minute_rate_paise"]
    assert master.onboarding_form.get("approved_tier") == "growth"

    # All sub-orgs must be updated
    assert sub_sub_1.plan == "growth"
    assert sub_sub_2.plan == "growth"
    assert sub_org_1.onboarding_form.get("approved_tier") == "growth"
    assert sub_org_2.onboarding_form.get("approved_tier") == "growth"

    # Provisioning must be triggered for master and active sub-orgs
    prov_calls = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_calls, "Master customer must be reprovisioned"
    assert 2 in prov_calls, "Sub org 1 must be reprovisioned"
    assert 3 in prov_calls, "Sub org 2 must be reprovisioned"


@pytest.mark.asyncio
async def test_sub_org_create_upgrade_order_rejects_custom_master_plan():
    """Verify Issue 3: Sub-org cannot self-upgrade if master is on custom plan."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub_org = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1)
    master_sub = Subscription(id=1, customer_id=1, plan="custom", per_minute_rate_paise=1500)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    req = UpgradeOrderRequest(dograh_org_id=20, requested_tier="growth")

    with pytest.raises(HTTPException) as exc_info:
        await create_upgrade_order(req, db=mock_db)

    assert exc_info.value.status_code == 400
    assert "custom-configured" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_confirm_topup_with_requested_tier_updates_master_and_sub_orgs():
    """Verify Issue 3: confirm_topup with requested_tier updates master plan and all sub-orgs."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, onboarding_form={})
    sub_org = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub_sub = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1200000)

    subs_by_customer = {1: master_sub, 2: sub_sub}

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            try:
                target_cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = subs_by_customer.get(target_cid, master_sub)
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org]
        elif "wallet_transactions" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = ConfirmTopupRequest(
        dograh_org_id=20,
        razorpay_order_id="order_up_123",
        razorpay_payment_id="pay_up_123",
        razorpay_signature="mock_sig",
        amount_paise=800000,
        requested_tier="growth"
    )

    with patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_topup_successful", AsyncMock()):

        res = await confirm_topup(req, db=mock_db)

    assert res["status"] == "ok"
    assert master_sub.plan == "growth"
    assert sub_sub.plan == "growth"
    assert master.onboarding_form.get("approved_tier") == "growth"
    assert sub_org.onboarding_form.get("approved_tier") == "growth"

    prov_calls = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_calls, "Master must be reprovisioned"
    assert 2 in prov_calls, "Sub org must be reprovisioned"


@pytest.mark.asyncio
async def test_single_org_create_upgrade_order_backward_compatibility():
    """Verify single-org (billing_org_id is None) continues to upgrade correctly with zero regressions."""
    single_cust = Customer(id=5, dograh_org_id=50, status="active", billing_org_id=None, onboarding_form={})
    single_sub = Subscription(id=5, customer_id=5, plan="starter", per_minute_rate_paise=600)
    wallet = Wallet(id=5, customer_id=5, balance_paise=1000000)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = single_cust
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = single_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = UpgradeOrderRequest(dograh_org_id=50, requested_tier="growth")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 5))), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.notification_service.notify_customer_tier_upgraded", AsyncMock()):

        res = await create_upgrade_order(req, db=mock_db)

    assert res["status"] == "upgraded_from_wallet"
    assert single_sub.plan == "growth"
    assert single_cust.onboarding_form.get("approved_tier") == "growth"
    prov_calls = [call.args[0] for call in mock_prov.call_args_list]
    assert 5 in prov_calls
