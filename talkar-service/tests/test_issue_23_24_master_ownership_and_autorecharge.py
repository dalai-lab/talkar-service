import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.customers import request_tier_upgrade, TierUpgradeRequest
from services.billing_service import check_and_trigger_auto_recharge


@pytest.mark.asyncio
async def test_blocker_23_sub_org_rejected_from_request_tier_upgrade():
    """
    Test Blocker 23:
    Sub-organizations (billing_org_id is not None) cannot modify agency-wide billing plans.
    They are rejected with HTTP 403.
    """
    sub_org = Customer(
        id=2,
        dograh_org_id=200,
        billing_org_id=1,
        status="active"
    )

    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    mock_db.execute = AsyncMock(return_value=res_cust)

    with patch("routers.customers.verify_customer_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await request_tier_upgrade(
                org_id=200,
                data=TierUpgradeRequest(requested_tier="pro"),
                db=mock_db
            )

    assert exc_info.value.status_code == 403
    assert "Sub-organizations cannot change agency-wide billing plans" in exc_info.value.detail


@pytest.mark.asyncio
async def test_blocker_24_auto_recharge_permits_zero_balance_suspended_master():
    """
    Test Blocker 24:
    Auto-recharge is permitted for masters suspended due to zero_balance,
    reactivating the master and child sub-orgs upon successful charge.
    """
    master = Customer(
        id=1,
        dograh_org_id=100,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub = Customer(
        id=2,
        dograh_org_id=200,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"}
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
            res.scalars.return_value.all.return_value = [sub]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = Subscription(customer_id=1, plan="starter")
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_charge = {"status": "captured", "id": "pay_autocharge_123", "order_id": "order_auto_123"}

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.redis_client.is_auto_recharge_in_cooldown", AsyncMock(return_value=False)), \
         patch("services.redis_client.acquire_auto_recharge_lock", AsyncMock(return_value="lock_tok")), \
         patch("services.redis_client.release_auto_recharge_lock", AsyncMock()), \
         patch("services.redis_client.clear_auto_recharge_failures", AsyncMock()), \
         patch("services.razorpay_client.create_topup_order", AsyncMock(return_value={"id": "order_auto_123"})), \
         patch("services.razorpay_client.charge_saved_card", AsyncMock(return_value=mock_charge)), \
         patch("services.billing_service.credit_wallet", AsyncMock(return_value=wallet)), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov:

        await check_and_trigger_auto_recharge(mock_db, customer_id=1)

    # Master and sub reactivated to active
    assert master.status == "active"
    assert sub.status == "active"

    # Both reprovisioned
    prov_calls = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_calls
    assert 2 in prov_calls


@pytest.mark.asyncio
async def test_blocker_24_auto_recharge_blocks_fraud_or_churned_suspended_master():
    """
    Test Blocker 24:
    Auto-recharge blocks charging when master is suspended for policy/fraud violations
    or when account is churned.
    """
    fraud_master = Customer(
        id=1,
        status="suspended",
        onboarding_form={"suspension_reason": "fraud"}
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
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = fraud_master
    mock_db.execute = AsyncMock(return_value=res_cust)

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.razorpay_client.charge_saved_card", AsyncMock()) as mock_charge:

        await check_and_trigger_auto_recharge(mock_db, customer_id=1)

    # Must NOT attempt to charge card
    assert not mock_charge.called
