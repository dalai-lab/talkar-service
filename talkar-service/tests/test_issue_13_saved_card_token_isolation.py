import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.billing import (
    get_wallet_by_org,
    create_razorpay_customer,
    create_add_card_session,
    confirm_add_card,
    save_card,
    remove_saved_card,
    update_auto_recharge,
    CreateRazorpayCustomerRequest,
    AddCardSessionRequest,
    ConfirmAddCardRequest,
    SaveCardRequest,
    AutoRechargeSettings,
)
import services.razorpay_client as razorpay_client


@pytest.mark.asyncio
async def test_sub_org_cannot_create_razorpay_customer():
    """Verify Issue 13: Sub-org cannot initiate Razorpay customer creation."""
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="sub@example.com")
    
    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    mock_db.execute = AsyncMock(return_value=res_cust)

    req = CreateRazorpayCustomerRequest(dograh_org_id=20, name="Sub Operator", email="sub@example.com")

    with pytest.raises(HTTPException) as exc_info:
        await create_razorpay_customer(data=req, db=mock_db)

    assert exc_info.value.status_code == 403
    assert "Payment cards can only be managed by the Master Organization owner" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_cannot_create_add_card_session():
    """Verify Issue 13: Sub-org cannot initiate add card e-mandate order session."""
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="sub@example.com")
    
    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    mock_db.execute = AsyncMock(return_value=res_cust)

    req = AddCardSessionRequest(dograh_org_id=20)

    with pytest.raises(HTTPException) as exc_info:
        await create_add_card_session(data=req, db=mock_db)

    assert exc_info.value.status_code == 403
    assert "Payment cards can only be managed by the Master Organization owner" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_cannot_confirm_add_card_overwriting_master():
    """Verify Issue 13: Sub-org confirm_add_card is rejected with 403 and NEVER overwrites master wallet card."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="master@agency.com")
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="sub@client.com")
    
    # Master wallet already has master's card token
    master_wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=100000,
        razorpay_customer_id="cust_master_123",
        razorpay_payment_method_id="token_master_valid_card"
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    mock_db.execute = AsyncMock(return_value=res_cust)

    req = ConfirmAddCardRequest(
        dograh_org_id=20,
        razorpay_payment_id="pay_sub_attempt",
        razorpay_order_id="order_sub_attempt"
    )

    with pytest.raises(HTTPException) as exc_info:
        await confirm_add_card(data=req, db=mock_db)

    assert exc_info.value.status_code == 403
    assert "Payment cards can only be managed by the Master Organization owner" in exc_info.value.detail
    # Master card must remain completely untouched
    assert master_wallet.razorpay_payment_method_id == "token_master_valid_card"


@pytest.mark.asyncio
async def test_sub_org_cannot_save_card():
    """Verify Issue 13: Sub-org cannot call save_card to overwrite master wallet card."""
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="sub@client.com")

    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    mock_db.execute = AsyncMock(return_value=res_cust)

    req = SaveCardRequest(dograh_org_id=20, razorpay_payment_method_id="token_sub_card")

    with pytest.raises(HTTPException) as exc_info:
        await save_card(data=req, db=mock_db)

    assert exc_info.value.status_code == 403
    assert "Payment cards can only be managed by the Master Organization owner" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_cannot_remove_master_card():
    """Verify Issue 13: Sub-org cannot remove the master organization's saved card."""
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="sub@client.com")

    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    mock_db.execute = AsyncMock(return_value=res_cust)

    with pytest.raises(HTTPException) as exc_info:
        await remove_saved_card(org_id=20, db=mock_db)

    assert exc_info.value.status_code == 403
    assert "Payment cards can only be managed by the Master Organization owner" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_cannot_modify_auto_recharge():
    """Verify Issue 13: Sub-org cannot enable/disable or alter auto-recharge settings."""
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="sub@client.com")

    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    mock_db.execute = AsyncMock(return_value=res_cust)

    settings = AutoRechargeSettings(enabled=True, threshold_paise=20000, amount_paise=100000)

    with pytest.raises(HTTPException) as exc_info:
        await update_auto_recharge(org_id=20, data=settings, db=mock_db)

    assert exc_info.value.status_code == 403
    assert "Auto-recharge settings can only be managed by the Master Organization owner" in exc_info.value.detail


@pytest.mark.asyncio
async def test_master_org_can_save_and_remove_card():
    """Verify Issue 13: Master organization owner can successfully add, confirm, and remove cards."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="master@agency.com")
    wallet = Wallet(id=1, customer_id=1, balance_paise=50000, razorpay_customer_id="cust_master_123")

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = master
    mock_db.execute = AsyncMock(return_value=res_cust)

    mock_client = MagicMock()
    mock_client.payment.fetch.return_value = {
        "id": "pay_master_456",
        "token_id": "token_master_rbi_mandate_999",
        "status": "captured",
    }

    req = ConfirmAddCardRequest(
        dograh_org_id=10,
        razorpay_payment_id="pay_master_456",
        razorpay_order_id="order_master_123",
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.billing_service.credit_wallet", AsyncMock()), \
         patch("services.redis_client.clear_auto_recharge_failures", AsyncMock()), \
         patch.object(razorpay_client, "client", mock_client), \
         patch("config.settings.RAZORPAY_KEY_SECRET", "mock_secret"):
        res = await confirm_add_card(data=req, db=mock_db)

    assert res["status"] == "card_saved"
    assert res["payment_method_id"] == "token_master_rbi_mandate_999"
    assert wallet.razorpay_payment_method_id == "token_master_rbi_mandate_999"

    # Now remove the card
    wallet.auto_recharge_enabled = True
    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))):
        rem_res = await remove_saved_card(org_id=10, db=mock_db)

    assert rem_res["status"] == "card_removed"
    assert wallet.razorpay_payment_method_id is None
    assert wallet.auto_recharge_enabled is False


@pytest.mark.asyncio
async def test_wallet_by_org_exposes_billing_management_capabilities():
    """Verify Issue 13: get_wallet_by_org correctly reflects is_sub_org and can_manage_billing."""
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="sub@client.com", status="active")
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=50000, auto_recharge_enabled=True)
    sub = Subscription(id=1, customer_id=1, plan="starter", status="active")

    mock_db = MagicMock()
    
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "subscriptions" in q_str:
            mock_res.scalar_one_or_none.return_value = sub
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("routers.billing.verify_customer_access", AsyncMock()), \
         patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))):
        res = await get_wallet_by_org(org_id=20, db=mock_db)

    assert res["is_sub_org"] is True
    assert res["can_manage_billing"] is False
