import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, WalletTransaction, AutomationApiKey, TalkarAdmin
from routers.admin import AdminDeductRequest, admin_deduct_customer
from routers.automation import DeductRequest, deduct_customer
from config import CALL_BLOCK_THRESHOLD_PAISE


@pytest.mark.asyncio
async def test_admin_deduct_customer_syncs_wallet_block_policy():
    """Verify admin_deduct_customer synchronizes wallet block policy with Dograh."""
    mock_db = AsyncMock()
    admin = TalkarAdmin(id=1, email="admin@talkar.in", name="Admin", role="admin")

    customer = Customer(id=5, dograh_org_id=50, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=5, balance_paise=100000)
    wallet_post = Wallet(id=1, customer_id=5, balance_paise=70000)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_update_res])

    req = AdminDeductRequest(amount_paise=30000, reason="Fee adjustment")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 5))), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block, \
         patch("services.notification_service.notify_customer_service_paused", AsyncMock()) as mock_notify_paused:
        res = await admin_deduct_customer(5, req, db=mock_db, current_admin=admin)

    assert res["status"] == "success"
    assert res["new_balance_paise"] == 70000
    mock_sync_block.assert_awaited_once_with(mock_db, 5)
    # Balance 70,000 paise > 50,000 paise (threshold), so no paused notification
    mock_notify_paused.assert_not_called()


@pytest.mark.asyncio
async def test_admin_deduct_customer_alerts_paused_when_below_threshold():
    """Verify admin_deduct_customer sends service paused alert and syncs block policy when balance <= threshold."""
    mock_db = AsyncMock()
    admin = TalkarAdmin(id=1, email="admin@talkar.in", name="Admin", role="admin")

    customer = Customer(id=5, dograh_org_id=50, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=5, balance_paise=60000)
    wallet_post = Wallet(id=1, customer_id=5, balance_paise=20000)  # Below 50,000 paise (₹500)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_update_res])

    req = AdminDeductRequest(amount_paise=40000, reason="Heavy penalty")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 5))), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block, \
         patch("services.notification_service.notify_customer_service_paused", AsyncMock()) as mock_notify_paused:
        res = await admin_deduct_customer(5, req, db=mock_db, current_admin=admin)

    assert res["status"] == "success"
    assert res["new_balance_paise"] == 20000
    mock_notify_paused.assert_awaited_once_with(5)
    mock_sync_block.assert_awaited_once_with(mock_db, 5)


@pytest.mark.asyncio
async def test_automation_deduct_customer_syncs_wallet_block_policy():
    """Verify deduct_customer synchronizes wallet block policy with Dograh."""
    mock_db = AsyncMock()
    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=200000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=150000)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_update_res])

    req = DeductRequest(amount_rupees=500, reason="Service charge")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash", name="test", scopes=["deduct"])

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 10))), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block, \
         patch("services.notification_service.notify_customer_service_paused", AsyncMock()) as mock_notify_paused:
        res = await deduct_customer(100, req, db=mock_db, api_key=mock_api_key)

    assert res["status"] == "success"
    assert res["new_balance_rupees"] == 1500.0
    mock_sync_block.assert_awaited_once_with(mock_db, 10)
    mock_notify_paused.assert_not_called()


@pytest.mark.asyncio
async def test_automation_deduct_customer_alerts_paused_when_below_threshold():
    """Verify deduct_customer sends service paused alert and syncs block policy when balance <= threshold."""
    mock_db = AsyncMock()
    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=80000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=30000)  # Below 50,000 paise (₹500)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_update_res])

    req = DeductRequest(amount_rupees=500, reason="High usage deduction")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash", name="test", scopes=["deduct"])

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 10))), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block, \
         patch("services.notification_service.notify_customer_service_paused", AsyncMock()) as mock_notify_paused:
        res = await deduct_customer(100, req, db=mock_db, api_key=mock_api_key)

    assert res["status"] == "success"
    assert res["new_balance_rupees"] == 300.0
    mock_notify_paused.assert_awaited_once_with(10)
    mock_sync_block.assert_awaited_once_with(mock_db, 10)
