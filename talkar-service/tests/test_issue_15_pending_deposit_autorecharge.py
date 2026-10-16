import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, call

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from services.billing_service import check_and_trigger_auto_recharge


@pytest.mark.asyncio
async def test_fix_19_pending_deposit_master_reactivates_on_successful_autorecharge():
    """
    Test Fix 19 (Issue 15):
    A master organization in 'pending_deposit' status is permitted to trigger auto-recharge.
    Upon successful card charge where balance >= activation_threshold,
    the master and any child sub-orgs in 'pending_deposit' or 'suspended' (zero_balance)
    are reactivated to 'active' and reprovisioned.
    """
    master = Customer(
        id=10,
        dograh_org_id=1000,
        status="pending_deposit",
        billing_org_id=None,
        onboarding_form={"approved_tier": "starter"}
    )
    sub_pending = Customer(
        id=20,
        dograh_org_id=2000,
        status="pending_deposit",
        billing_org_id=10,
        onboarding_form={}
    )
    sub_suspended = Customer(
        id=30,
        dograh_org_id=3000,
        status="suspended",
        billing_org_id=10,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub_fraud = Customer(
        id=40,
        dograh_org_id=4000,
        status="suspended",
        billing_org_id=10,
        onboarding_form={"suspension_reason": "policy_violation"}
    )
    wallet = Wallet(
        id=10,
        customer_id=10,
        balance_paise=0,
        auto_recharge_enabled=True,
        auto_recharge_threshold_paise=150000,
        auto_recharge_amount_paise=600000,
        razorpay_customer_id="cust_pend_123",
        razorpay_payment_method_id="token_valid_pend_123"
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = [sub_pending, sub_suspended, sub_fraud]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = Subscription(customer_id=10, plan="starter")
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_charge = {"status": "captured", "id": "pay_autocharge_pend_15", "order_id": "order_auto_pend_15"}

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))), \
         patch("services.redis_client.is_auto_recharge_in_cooldown", AsyncMock(return_value=False)), \
         patch("services.redis_client.acquire_auto_recharge_lock", AsyncMock(return_value="lock_tok")), \
         patch("services.redis_client.release_auto_recharge_lock", AsyncMock()), \
         patch("services.redis_client.clear_auto_recharge_failures", AsyncMock()), \
         patch("services.razorpay_client.create_topup_order", AsyncMock(return_value={"id": "order_auto_pend_15"})), \
         patch("services.razorpay_client.charge_saved_card", AsyncMock(return_value=mock_charge)), \
         patch("services.billing_service.credit_wallet", AsyncMock(return_value=wallet)), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov:

        await check_and_trigger_auto_recharge(mock_db, customer_id=10)

    # Master and eligible sub-orgs reactivated
    assert master.status == "active"
    assert sub_pending.status == "active"
    assert sub_suspended.status == "active"
    # Policy-violation suspended sub-org remains suspended
    assert sub_fraud.status == "suspended"

    # Reprovisioning triggered for master and reactivated sub-orgs
    prov_calls = [c.args[0] for c in mock_prov.call_args_list]
    assert 10 in prov_calls
    assert 20 in prov_calls
    assert 30 in prov_calls
    assert 40 not in prov_calls

    # Wallet block policy synced
    assert mock_sync.called


@pytest.mark.asyncio
async def test_fix_19_pending_deposit_master_remains_pending_if_balance_below_threshold():
    """
    If the auto-recharge credited amount is below activation threshold,
    the pending_deposit master remains in 'pending_deposit'.
    """
    master = Customer(
        id=11,
        dograh_org_id=1100,
        status="pending_deposit",
        billing_org_id=None,
        onboarding_form={"approved_tier": "starter"}
    )
    wallet = Wallet(
        id=11,
        customer_id=11,
        balance_paise=0,
        auto_recharge_enabled=True,
        auto_recharge_threshold_paise=150000,
        auto_recharge_amount_paise=100000,  # Below starter activation threshold (600,000 paise)
        razorpay_customer_id="cust_pend_low",
        razorpay_payment_method_id="token_valid_pend_low"
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = Subscription(customer_id=11, plan="starter")
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_charge = {"status": "captured", "id": "pay_autocharge_pend_low", "order_id": "order_auto_pend_low"}

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 11))), \
         patch("services.redis_client.is_auto_recharge_in_cooldown", AsyncMock(return_value=False)), \
         patch("services.redis_client.acquire_auto_recharge_lock", AsyncMock(return_value="lock_tok")), \
         patch("services.redis_client.release_auto_recharge_lock", AsyncMock()), \
         patch("services.redis_client.clear_auto_recharge_failures", AsyncMock()), \
         patch("services.razorpay_client.create_topup_order", AsyncMock(return_value={"id": "order_auto_pend_low"})), \
         patch("services.razorpay_client.charge_saved_card", AsyncMock(return_value=mock_charge)), \
         patch("services.billing_service.credit_wallet", AsyncMock(return_value=wallet)), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov:

        await check_and_trigger_auto_recharge(mock_db, customer_id=11)

    # Master remains pending_deposit because 100,000 < 600,000
    assert master.status == "pending_deposit"
    assert not mock_prov.called
