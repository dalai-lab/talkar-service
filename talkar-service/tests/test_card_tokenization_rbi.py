import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet
from routers.billing import (
    AddCardSessionRequest,
    ConfirmAddCardRequest,
    SaveCardRequest,
    create_add_card_session,
    confirm_add_card,
    save_card,
)
import services.razorpay_client as razorpay_client


@pytest.mark.asyncio
async def test_create_add_card_session_creates_mandate_order_and_customer():
    """create_add_card_session creates Razorpay customer and mandate order with token config."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(
        id=10,
        dograh_org_id=100,
        status="active",
        contact_name="Test User",
        contact_email="test@talkar.in",
        contact_phone="9876543210",
    )
    wallet = Wallet(id=1, customer_id=10, balance_paise=50000, razorpay_customer_id=None)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    mock_client = MagicMock()
    mock_client.customer.create.return_value = {"id": "cust_rzp_123"}
    mock_client.order.create.return_value = {"id": "order_mandate_456"}

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))), \
         patch.object(razorpay_client, "client", mock_client):
        req = AddCardSessionRequest(dograh_org_id=100)
        res = await create_add_card_session(data=req, db=mock_db)

    assert res["razorpay_order_id"] == "order_mandate_456"
    assert res["razorpay_customer_id"] == "cust_rzp_123"
    assert wallet.razorpay_customer_id == "cust_rzp_123"

    # Verify order.create payload
    order_args = mock_client.order.create.call_args[0][0]
    assert order_args["customer_id"] == "cust_rzp_123"
    assert order_args["amount"] == 200
    assert order_args["currency"] == "INR"
    assert order_args["notes"]["type"] == "card_registration"


@pytest.mark.asyncio
async def test_confirm_add_card_saves_token_id():
    """confirm_add_card extracts token_id and saves it to wallet."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active")
    wallet = Wallet(id=1, customer_id=10, balance_paise=50000, razorpay_customer_id="cust_rzp_123")

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    mock_client = MagicMock()
    mock_client.payment.fetch.return_value = {
        "id": "pay_987654",
        "token_id": "token_mandate_valid_999",
        "status": "captured",
    }

    req = ConfirmAddCardRequest(
        dograh_org_id=100,
        razorpay_payment_id="pay_987654",
        razorpay_order_id="order_123",
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))), \
         patch("services.billing_service.credit_wallet", AsyncMock()), \
         patch("services.redis_client.clear_auto_recharge_failures", AsyncMock()), \
         patch.object(razorpay_client, "client", mock_client), \
         patch("config.settings.RAZORPAY_KEY_SECRET", "mock_secret"):
        res = await confirm_add_card(data=req, db=mock_db)

    assert res["status"] == "card_saved"
    assert res["payment_method_id"] == "token_mandate_valid_999"
    assert wallet.razorpay_payment_method_id == "token_mandate_valid_999"


@pytest.mark.asyncio
async def test_confirm_add_card_rejects_when_no_token_generated():
    """confirm_add_card raises HTTPException(400) and NEVER saves pay_xxx ID when token is missing."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active")
    wallet = Wallet(id=1, customer_id=10, balance_paise=50000, razorpay_customer_id="cust_rzp_123", razorpay_payment_method_id=None)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    mock_client = MagicMock()
    # Simulates regular one-off payment without e-mandate registration
    mock_client.payment.fetch.return_value = {
        "id": "pay_oneoff_12345",
        "token_id": None,
        "token": None,
        "status": "captured",
    }
    mock_client.token.all.return_value = {"items": []}

    req = ConfirmAddCardRequest(
        dograh_org_id=100,
        razorpay_payment_id="pay_oneoff_12345",
        razorpay_order_id="order_123",
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))), \
         patch.object(razorpay_client, "client", mock_client), \
         patch("config.settings.RAZORPAY_KEY_SECRET", "mock_secret"):
        with pytest.raises(HTTPException) as exc_info:
            await confirm_add_card(data=req, db=mock_db)

    assert exc_info.value.status_code == 400
    assert "No recurring card token generated" in exc_info.value.detail
    # Must NOT save the pay_xxx ID
    assert wallet.razorpay_payment_method_id is None


@pytest.mark.asyncio
async def test_save_card_rejects_payment_id():
    """save_card raises HTTPException(400) when a pay_xxx string is passed."""
    mock_db = MagicMock()
    customer = Customer(id=10, dograh_org_id=100, status="active")
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    req = SaveCardRequest(dograh_org_id=100, razorpay_payment_method_id="pay_invalid_12345")

    with patch("routers.billing.verify_customer_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await save_card(data=req, db=mock_db)

    assert exc_info.value.status_code == 400
    assert "Invalid token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_charge_saved_card_rejects_pay_id():
    """charge_saved_card raises ValueError if payment_method_id is a pay_xxx string."""
    with pytest.raises(ValueError, match="Invalid recurring token 'pay_test123'"):
        await razorpay_client.charge_saved_card(
            customer_id="cust_123",
            payment_method_id="pay_test123",
            amount_paise=500000,
        )


@pytest.mark.asyncio
async def test_charge_saved_card_uses_create_recurring():
    """charge_saved_card calls client.payment.createRecurring with proper parameters."""
    mock_client = MagicMock()
    mock_client.payment.createRecurring.return_value = {"id": "pay_recurring_123", "status": "captured"}

    with patch.object(razorpay_client, "client", mock_client):
        res = await razorpay_client.charge_saved_card(
            customer_id="cust_123",
            payment_method_id="token_valid_456",
            amount_paise=500000,
            email="client@talkar.in",
            contact="9876543210",
        )

    assert res["id"] == "pay_recurring_123"
    mock_client.payment.createRecurring.assert_called_once()
    payload = mock_client.payment.createRecurring.call_args[0][0]
    assert payload["customer_id"] == "cust_123"
    assert payload["token"] == "token_valid_456"
    assert payload["amount"] == 500000
    assert payload["email"] == "client@talkar.in"
    assert payload["contact"] == "9876543210"


@pytest.mark.asyncio
async def test_charge_saved_card_includes_notes_customer_id():
    """charge_saved_card includes notes.customer_id and order_id in createRecurring payload."""
    mock_client = MagicMock()
    mock_client.payment.createRecurring.return_value = {
        "id": "pay_recurring_456",
        "order_id": "order_auto_789",
        "status": "captured",
    }

    with patch.object(razorpay_client, "client", mock_client):
        res = await razorpay_client.charge_saved_card(
            customer_id="cust_rzp_999",
            payment_method_id="token_mandate_888",
            amount_paise=100000,
            email="owner@talkar.in",
            contact="9123456780",
            order_id="order_auto_789",
            talkar_customer_id=42,
            notes={"type": "auto_recharge"},
        )

    assert res["id"] == "pay_recurring_456"
    mock_client.payment.createRecurring.assert_called_once()
    payload = mock_client.payment.createRecurring.call_args[0][0]
    assert payload["order_id"] == "order_auto_789"
    assert "notes" in payload
    assert payload["notes"]["customer_id"] == "42"
    assert payload["notes"]["type"] == "auto_recharge"


@pytest.mark.asyncio
async def test_webhook_recovers_dropped_auto_recharge_via_notes():
    """If synchronous HTTP call dropped, webhook maps payment back to customer via notes.customer_id."""
    import json
    from routers.billing import razorpay_webhook

    mock_db = AsyncMock()
    # Transaction does not exist in DB (synchronous call dropped)
    res_lock = MagicMock()
    res_txn = MagicMock()
    res_txn.scalar_one_or_none.return_value = None

    # Check setup fee: None
    res_setup = MagicMock()
    res_setup.scalar_one_or_none.return_value = None

    # Check customer for top-up
    customer = Customer(id=42, status="active")
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer

    wallet = Wallet(id=1, customer_id=42, balance_paise=50000)
    res_wallet = MagicMock()
    res_wallet.scalar_one_or_none.return_value = wallet

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(side_effect=[res_lock, res_txn, res_setup, res_cust, res_wallet, res_sub])

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_recovered_123",
                    "order_id": "order_auto_789",
                    "amount": 100000,
                    "notes": {"customer_id": "42", "type": "auto_recharge"},
                }
            }
        },
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with (
        patch("services.razorpay_client.verify_webhook_signature", return_value=True),
        patch("services.billing_service.credit_wallet", AsyncMock()) as mock_credit,
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
    ):
        res = await razorpay_webhook(mock_request, db=mock_db)
        mock_credit.assert_awaited_once_with(
            mock_db,
            42,
            100000,
            razorpay_order_id="order_auto_789",
            razorpay_payment_id="pay_recovered_123",
        )

    assert res["status"] == "ok"


@pytest.mark.asyncio
async def test_auto_recharge_proactively_disables_and_clears_pay_token():
    """check_and_trigger_auto_recharge clears invalid legacy pay_ tokens without attempting gateway charge."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    wallet = Wallet(
        id=1,
        customer_id=10,
        balance_paise=50000,
        auto_recharge_threshold_paise=100000,
        auto_recharge_amount_paise=500000,
        auto_recharge_enabled=True,
        razorpay_customer_id="cust_rzp_123",
        razorpay_payment_method_id="pay_legacy_should_not_charge",
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.notification_service.notify_customer_auto_recharge_failed", AsyncMock()) as mock_notify,
        patch("services.razorpay_client.charge_saved_card") as mock_charge,
    ):
        from services.billing_service import check_and_trigger_auto_recharge
        await check_and_trigger_auto_recharge(mock_db, 10)

        # Proactively cleared and disabled
        assert wallet.auto_recharge_enabled is False
        assert wallet.razorpay_payment_method_id is None
        mock_db.commit.assert_awaited()
        # Gateway was never hammered with invalid pay_ token
        mock_charge.assert_not_called()
        mock_notify.assert_awaited_once_with(
            10,
            error_detail="Invalid payment token: An RBI recurring mandate token (token_xxx) is required.",
            disabled=True,
        )


@pytest.mark.asyncio
async def test_patch_wallet_auto_recharge_rejects_pay_token():
    """update_auto_recharge raises 400 when attempting to enable auto-recharge with a pay_ token."""
    from routers.billing import update_auto_recharge, AutoRechargeSettings

    mock_db = MagicMock()
    customer = Customer(id=10, dograh_org_id=100, status="active")
    wallet = Wallet(
        id=1,
        customer_id=10,
        razorpay_payment_method_id="pay_invalid_token",
        auto_recharge_enabled=False,
    )

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    req = AutoRechargeSettings(
        enabled=True,
        threshold_paise=100000,
        amount_paise=500000,
    )

    with (
        patch("routers.billing.verify_customer_access", AsyncMock()),
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await update_auto_recharge(100, req, db=mock_db)

    assert exc_info.value.status_code == 400
    assert "RBI-compliant mandate token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_handle_auto_recharge_failure_disables_and_notifies_on_three_strikes():
    """_handle_auto_recharge_failure disables auto-recharge and dispatches disabled notifications on 3rd failure."""
    from services.billing_service import _handle_auto_recharge_failure

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    wallet = Wallet(
        id=1,
        customer_id=10,
        auto_recharge_enabled=True,
    )

    with (
        patch("services.redis_client.record_auto_recharge_failure", AsyncMock(return_value=(3, 3600))),
        patch("services.notification_service.notify_customer_auto_recharge_failed", AsyncMock()) as mock_cust_notify,
        patch("services.notification_service.notify_admin_auto_recharge_failed", AsyncMock()) as mock_admin_notify,
    ):
        await _handle_auto_recharge_failure(mock_db, wallet, 10, "Bank rejected e-mandate")

        assert wallet.auto_recharge_enabled is False
        mock_db.commit.assert_awaited_once()
        mock_cust_notify.assert_awaited_once_with(10, error_detail="Bank rejected e-mandate", disabled=True)
        mock_admin_notify.assert_awaited_once_with(10, error_detail="Bank rejected e-mandate", disabled=True)


