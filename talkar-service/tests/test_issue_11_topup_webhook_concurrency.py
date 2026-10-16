import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription, WalletTransaction
from routers.billing import ConfirmTopupRequest, confirm_topup


@pytest.mark.asyncio
async def test_fix_16_concurrent_webhook_race_activates_pending_deposit_customer():
    """
    Fix 16 / Issue 11:
    When a concurrent webhook captures the payment first, credit_wallet raises duplicate key exception.
    confirm_topup must NOT abort early; it must fetch the updated wallet and proceed to activate
    the pending_deposit customer to active and restore telephony calls.
    """
    customer = Customer(
        id=1,
        dograh_org_id=100,
        contact_email="owner@business.com",
        status="pending_deposit",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    # Wallet credited by webhook has 1,000,000 paise (₹10,000)
    credited_wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)
    subscription = Subscription(id=1, customer_id=1, plan="starter", status="active")

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "from customers" in q_str.lower() and "customers.dograh_org_id" in q_str:
            mock_res.scalar_one_or_none.return_value = customer
        elif "from wallet_transactions" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = None
        elif "from wallets" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = credited_wallet
        elif "from subscriptions" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = subscription
        elif "from customers" in q_str.lower() and "customers.id = :id_1" in q_str:
            mock_res.scalar_one_or_none.return_value = customer
        elif "from customers" in q_str.lower():
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    req = ConfirmTopupRequest(
        dograh_org_id=100,
        razorpay_order_id="order_race_789",
        razorpay_payment_id="pay_race_789",
        razorpay_signature="mock_signature",
        amount_paise=1000000
    )

    duplicate_error = Exception('duplicate key value violates unique constraint "idx_wallet_transactions_razorpay"')

    with patch("services.billing_service.credit_wallet", AsyncMock(side_effect=duplicate_error)), \
         patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.notification_service.send_email", AsyncMock()):

        resp = await confirm_topup(data=req, db=mock_db)

    # Status must be activated despite duplicate webhook race
    assert customer.status == "active"
    assert "suspension_reason" not in (customer.onboarding_form or {})
    assert resp["new_balance_paise"] == 1000000
    mock_restore.assert_awaited_once_with(100, "starter", 2)
    mock_sync.assert_awaited_once_with(mock_db, 1)


@pytest.mark.asyncio
async def test_fix_16_concurrent_webhook_race_applies_requested_tier_upgrade():
    """
    Fix 16 / Issue 11:
    When a top-up includes requested_tier, but credit_wallet fails due to duplicate key constraint,
    the tier upgrade and provisioning must still be executed.
    """
    customer = Customer(
        id=2,
        dograh_org_id=200,
        contact_email="pro@startup.com",
        status="active",
        billing_org_id=None
    )
    credited_wallet = Wallet(id=2, customer_id=2, balance_paise=2500000)
    subscription = Subscription(id=2, customer_id=2, plan="starter", status="active", per_minute_rate_paise=600)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "from customers" in q_str.lower() and "customers.dograh_org_id" in q_str:
            mock_res.scalar_one_or_none.return_value = customer
        elif "from wallet_transactions" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = None
        elif "from wallets" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = credited_wallet
        elif "from subscriptions" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = subscription
        elif "from customers" in q_str.lower() and "customers.id = :id_1" in q_str:
            mock_res.scalar_one_or_none.return_value = customer
        elif "from customers" in q_str.lower() and "billing_org_id" in q_str:
            mock_res.scalars.return_value.all.return_value = []
        elif "from customers" in q_str.lower():
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    req = ConfirmTopupRequest(
        dograh_org_id=200,
        razorpay_order_id="order_tier_upgrade_race",
        razorpay_payment_id="pay_tier_upgrade_race",
        razorpay_signature="mock_signature",
        amount_paise=2500000,
        requested_tier="pro"
    )

    duplicate_error = Exception('duplicate key value violates unique constraint "idx_wallet_transactions_razorpay"')

    with patch("services.billing_service.credit_wallet", AsyncMock(side_effect=duplicate_error)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync:

        resp = await confirm_topup(data=req, db=mock_db)

    # Subscription must have upgraded to pro
    assert subscription.plan == "pro"
    assert subscription.per_minute_rate_paise == 1800
    mock_prov.assert_awaited_once_with(2, "pro", mock_db)
    assert resp["new_balance_paise"] == 2500000
