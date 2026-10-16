import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.billing import create_upgrade_order, UpgradeOrderRequest


@pytest.mark.asyncio
async def test_fix_23_tier_upgrade_shortfall_creates_payment_order():
    """
    Test Fix 23 (Issue 21):
    When a customer requests a tier upgrade (e.g. to 'pro' requiring 800,000 paise)
    and wallet balance is 500,000 paise (shortfall of 300,000 paise),
    create_upgrade_order automatically generates a Razorpay order for 300,000 paise
    and returns status='payment_required' instead of raising HTTP 400.
    """
    customer = Customer(
        id=10,
        dograh_org_id=100,
        contact_email="owner@corp.com",
        company_name="Corp Master",
        billing_org_id=None,
        status="active"
    )
    sub = Subscription(
        customer_id=10,
        plan="starter",
        per_minute_rate_paise=600,
        status="active"
    )
    wallet = Wallet(
        id=10,
        customer_id=10,
        balance_paise=500000  # 500,000 paise = ₹5,000. Pro tier requires 800,000 paise = ₹8,000.
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from customers" in q_str:
            res.scalar_one_or_none.return_value = customer
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = sub
        return res

    mock_db.execute.side_effect = mock_execute

    req = UpgradeOrderRequest(dograh_org_id=100, requested_tier="pro")

    mock_order = {"id": "order_upg_shortfall_123", "amount": 300000, "currency": "INR"}

    with patch("routers.billing.verify_customer_access", AsyncMock()), \
         patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))), \
         patch("services.razorpay_client.order.create", MagicMock(return_value=mock_order)) as mock_create_order:

        result = await create_upgrade_order(data=req, db=mock_db)

    assert result["status"] == "payment_required"
    assert result["razorpay_order_id"] == "order_upg_shortfall_123"
    assert result["amount_paise"] == 300000
    assert result["requested_tier"] == "pro"
    assert result["shortfall_inr"] == 3000

    # Verify order was created with proper notes and shortfall amount
    mock_create_order.assert_called_once()
    called_order_data = mock_create_order.call_args[1]["data"]
    assert called_order_data["amount"] == 300000
    assert called_order_data["currency"] == "INR"
    assert called_order_data["notes"]["purpose"] == "tier_upgrade"
    assert called_order_data["notes"]["requested_tier"] == "pro"
    assert called_order_data["notes"]["dograh_org_id"] == "100"


@pytest.mark.asyncio
async def test_fix_23_tier_upgrade_with_sufficient_balance_upgrades_directly():
    """
    When wallet balance meets the activation deposit,
    create_upgrade_order immediately upgrades from wallet.
    """
    customer = Customer(
        id=15,
        dograh_org_id=150,
        contact_email="rich@corp.com",
        company_name="Rich Corp",
        billing_org_id=None,
        status="active"
    )
    sub = Subscription(
        customer_id=15,
        plan="starter",
        per_minute_rate_paise=600,
        status="active"
    )
    wallet = Wallet(
        id=15,
        customer_id=15,
        balance_paise=900000  # ₹9,000 >= ₹8,000 Pro threshold
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = []
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = customer
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = sub
        return res

    mock_db.execute.side_effect = mock_execute

    req = UpgradeOrderRequest(dograh_org_id=150, requested_tier="pro")

    with patch("routers.billing.verify_customer_access", AsyncMock()), \
         patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 15))), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_tier_upgraded", AsyncMock()):

        result = await create_upgrade_order(data=req, db=mock_db)

    assert result["status"] == "upgraded_from_wallet"
    assert result["requested_tier"] == "pro"
    assert sub.plan == "pro"
