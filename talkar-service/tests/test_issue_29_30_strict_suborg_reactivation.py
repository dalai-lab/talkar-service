import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.billing import confirm_topup, ConfirmTopupRequest
from services.billing_service import check_and_trigger_auto_recharge


@pytest.mark.asyncio
async def test_blocker_30_strictly_validates_suborg_reactivation_on_topup():
    """
    Test Blocker 30:
    In routers/billing.py confirm_topup, strictly require s_form.get("suspension_reason") == "zero_balance"
    before auto-activating sub-orgs on master top-ups.
    Sub-orgs with null, empty, or policy/fraud suspension reasons must remain suspended.
    """
    master = Customer(
        id=1,
        dograh_org_id=100,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    # Valid zero_balance sub-org
    sub_zero_balance = Customer(
        id=2,
        dograh_org_id=200,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    # Sub-org with null reason (onboarding_form={})
    sub_null_reason = Customer(
        id=3,
        dograh_org_id=300,
        status="suspended",
        billing_org_id=1,
        onboarding_form={}
    )
    # Sub-org with fraud reason
    sub_fraud_reason = Customer(
        id=4,
        dograh_org_id=400,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "fraud"}
    )
    # Sub-org with empty string reason
    sub_empty_reason = Customer(
        id=5,
        dograh_org_id=500,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": ""}
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = [
                sub_zero_balance,
                sub_null_reason,
                sub_fraud_reason,
                sub_empty_reason
            ]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
            res.scalars.return_value.first.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
        elif "from wallet_transactions" in q_str:
            res.scalar_one_or_none.return_value = None
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = Subscription(customer_id=1, plan="starter")
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("routers.billing.billing_service.credit_wallet", AsyncMock(return_value=wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_topup_successful", AsyncMock()):

        req_data = ConfirmTopupRequest(
            dograh_org_id=100,
            razorpay_payment_id="pay_topup_30",
            razorpay_order_id="order_topup_30",
            razorpay_signature="sig_30",
            amount_paise=500000
        )
        res = await confirm_topup(data=req_data, db=mock_db)

    assert res["status"] == "ok"
    assert master.status == "active"
    # ONLY zero_balance sub-org is reactivated
    assert sub_zero_balance.status == "active"
    assert sub_null_reason.status == "suspended"
    assert sub_fraud_reason.status == "suspended"
    assert sub_empty_reason.status == "suspended"

    # Only master and sub_zero_balance were reprovisioned
    prov_ids = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_ids
    assert 2 in prov_ids
    assert 3 not in prov_ids
    assert 4 not in prov_ids
    assert 5 not in prov_ids


@pytest.mark.asyncio
async def test_blocker_30_strictly_validates_suborg_reactivation_on_autorecharge():
    """
    Test Blocker 30:
    In services/billing_service.py check_and_trigger_auto_recharge, strictly require
    s_form.get("suspension_reason") == "zero_balance" before auto-activating sub-orgs.
    """
    master = Customer(
        id=1,
        dograh_org_id=100,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub_zero_balance = Customer(
        id=2,
        dograh_org_id=200,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub_rogue_null = Customer(
        id=3,
        dograh_org_id=300,
        status="suspended",
        billing_org_id=1,
        onboarding_form={}
    )
    sub_fraud = Customer(
        id=4,
        dograh_org_id=400,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "policy_violation"}
    )
    wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=1000,
        auto_recharge_enabled=True,
        auto_recharge_threshold_paise=50000,
        auto_recharge_amount_paise=500000,
        razorpay_customer_id="cust_123",
        razorpay_payment_method_id="token_valid_123"
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = [sub_zero_balance, sub_rogue_null, sub_fraud]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = Subscription(customer_id=1, plan="starter")
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_charge = {"status": "captured", "id": "pay_autocharge_30", "order_id": "order_auto_30"}

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.redis_client.is_auto_recharge_in_cooldown", AsyncMock(return_value=False)), \
         patch("services.redis_client.acquire_auto_recharge_lock", AsyncMock(return_value="lock_tok")), \
         patch("services.redis_client.release_auto_recharge_lock", AsyncMock()), \
         patch("services.redis_client.clear_auto_recharge_failures", AsyncMock()), \
         patch("services.razorpay_client.create_topup_order", AsyncMock(return_value={"id": "order_auto_30"})), \
         patch("services.razorpay_client.charge_saved_card", AsyncMock(return_value=mock_charge)), \
         patch("services.billing_service.credit_wallet", AsyncMock(return_value=wallet)), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov:

        await check_and_trigger_auto_recharge(mock_db, customer_id=1)

    assert master.status == "active"
    assert sub_zero_balance.status == "active"
    assert sub_rogue_null.status == "suspended"
    assert sub_fraud.status == "suspended"

    prov_ids = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_ids
    assert 2 in prov_ids
    assert 3 not in prov_ids
    assert 4 not in prov_ids
