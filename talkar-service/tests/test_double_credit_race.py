import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, WalletTransaction
from routers.billing import ConfirmTopupRequest, confirm_topup, razorpay_webhook
from services import billing_service


@pytest.mark.asyncio
async def test_credit_wallet_fast_path_idempotency():
    """If razorpay_order_id was already credited, credit_wallet must not credit again."""
    mock_db = AsyncMock()

    # Mock customer and wallet
    mock_wallet = Wallet(id=1, customer_id=10, balance_paise=5000)
    mock_customer = Customer(id=10, billing_org_id=None)

    # get_billing_wallet returns wallet and master_id
    with patch(
        "services.billing_service.get_billing_wallet",
        AsyncMock(return_value=(mock_wallet, 10)),
    ):
        # existing_txn returns an existing WalletTransaction
        mock_txn = WalletTransaction(id=99, razorpay_order_id="order_duplicate_123")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_txn
        mock_db.execute = AsyncMock(return_value=mock_result)

        res_wallet = await billing_service.credit_wallet(
            mock_db,
            customer_id=10,
            amount_paise=5000,
            razorpay_order_id="order_duplicate_123",
        )

        assert res_wallet.balance_paise == 5000
        # db.add must not have been called because duplicate was skipped
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_credit_wallet_concurrent_unique_constraint_rollback():
    """If a concurrent race condition attempts duplicate credit and triggers unique constraint,
    credit_wallet rolls back the session and prevents double crediting."""
    mock_db = AsyncMock()

    mock_wallet = Wallet(id=1, customer_id=10, balance_paise=5000)

    with patch(
        "services.billing_service.get_billing_wallet",
        AsyncMock(return_value=(mock_wallet, 10)),
    ):
        # 1st query: check existing_txn -> None (race condition snapshot)
        mock_check_res = MagicMock()
        mock_check_res.scalar_one_or_none.return_value = None

        # 2nd query: update(Wallet) -> returning wallet
        mock_update_res = MagicMock()
        updated_wallet = Wallet(id=1, customer_id=10, balance_paise=10000)
        mock_update_res.scalar_one_or_none.return_value = updated_wallet

        # 3rd query: after rollback, fetch existing wallet from DB
        mock_refreshed_res = MagicMock()
        mock_refreshed_res.scalar_one_or_none.return_value = Wallet(
            id=1, customer_id=10, balance_paise=5000
        )

        mock_db.execute = AsyncMock(
            side_effect=[mock_check_res, mock_update_res, mock_refreshed_res]
        )

        # commit() raises IntegrityError with idx_wallet_transactions_razorpay
        mock_db.commit = AsyncMock(
            side_effect=IntegrityError(
                "insert statement",
                {},
                Exception(
                    'duplicate key value violates unique constraint "idx_wallet_transactions_razorpay"'
                ),
            )
        )

        # Execute credit_wallet
        res_wallet = await billing_service.credit_wallet(
            mock_db,
            customer_id=10,
            amount_paise=5000,
            razorpay_order_id="order_race_456",
        )

        # Session must have been rolled back
        mock_db.rollback.assert_awaited_once()
        # Returned wallet must be the refreshed single-credit wallet
        assert res_wallet.balance_paise == 5000


@pytest.mark.asyncio
async def test_confirm_topup_idempotency_returns_already_processed():
    """confirm-topup returns already_processed when razorpay_order_id already exists."""
    mock_db = AsyncMock()
    customer = Customer(id=10, dograh_org_id=100)
    existing_txn = WalletTransaction(id=1, razorpay_order_id="order_done_1")
    wallet = Wallet(id=1, customer_id=10, balance_paise=7500)

    # 1. Customer lookup
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer

    # 2. Advisory lock lookup (returns dummy result or None)
    res_lock = MagicMock()

    # 3. Existing txn lookup
    res_txn = MagicMock()
    res_txn.scalar_one_or_none.return_value = existing_txn

    # 4. Wallet lookup
    res_w = MagicMock()
    res_w.scalar_one_or_none.return_value = wallet

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_lock, res_txn, res_w])

    req = ConfirmTopupRequest(
        razorpay_payment_id="pay_1",
        razorpay_order_id="order_done_1",
        razorpay_signature="mock_signature",
        dograh_org_id=100,
        amount_paise=2500,
    )

    with patch("services.billing_service.credit_wallet") as mock_credit:
        res = await confirm_topup(req, db=mock_db)
        mock_credit.assert_not_called()

    assert res["status"] == "already_processed"
    assert res["new_balance_paise"] == 7500


@pytest.mark.asyncio
async def test_webhook_idempotency_ignores_duplicate():
    """Webhook ignores duplicate top-up when order_id already has a transaction."""
    import json
    mock_db = AsyncMock()
    existing_txn = WalletTransaction(id=1, razorpay_order_id="order_webhook_1")

    res_lock = MagicMock()
    res_txn = MagicMock()
    res_txn.scalar_one_or_none.return_value = existing_txn

    mock_db.execute = AsyncMock(side_effect=[res_lock, res_txn])

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "order_id": "order_webhook_1",
                    "amount": 5000,
                    "notes": {"customer_id": "10"}
                }
            }
        }
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with (
        patch("services.razorpay_client.verify_webhook_signature", return_value=True),
        patch("services.billing_service.credit_wallet") as mock_credit,
    ):
        res = await razorpay_webhook(mock_request, db=mock_db)
        mock_credit.assert_not_called()

    assert res["status"] == "duplicate top-up, ignored"


@pytest.mark.asyncio
async def test_webhook_idempotency_detects_duplicate_when_db_has_payment_id():
    """When auto-recharge stored payment_id (pay_ABC123), webhook with order_id and payment_id must detect duplicate."""
    import json
    mock_db = AsyncMock()
    # Transaction was stored with pay_ABC123
    existing_txn = WalletTransaction(id=1, razorpay_order_id="pay_ABC123")

    res_lock = MagicMock()
    res_txn = MagicMock()
    res_txn.scalar_one_or_none.return_value = existing_txn

    mock_db.execute = AsyncMock(side_effect=[res_lock, res_txn])

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_ABC123",
                    "order_id": "order_XYZ789",
                    "amount": 5000,
                    "notes": {"customer_id": "10"}
                }
            }
        }
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with (
        patch("services.razorpay_client.verify_webhook_signature", return_value=True),
        patch("services.billing_service.credit_wallet") as mock_credit,
    ):
        res = await razorpay_webhook(mock_request, db=mock_db)
        mock_credit.assert_not_called()

    assert res["status"] == "duplicate top-up, ignored"


@pytest.mark.asyncio
async def test_confirm_topup_detects_duplicate_when_db_has_payment_id():
    """confirm-topup returns already_processed when db stored payment_id rather than order_id."""
    mock_db = AsyncMock()
    customer = Customer(id=10, dograh_org_id=100)
    existing_txn = WalletTransaction(id=1, razorpay_order_id="pay_saved_card_999")
    wallet = Wallet(id=1, customer_id=10, balance_paise=12000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer

    res_lock = MagicMock()
    res_txn = MagicMock()
    res_txn.scalar_one_or_none.return_value = existing_txn

    res_w = MagicMock()
    res_w.scalar_one_or_none.return_value = wallet

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_lock, res_txn, res_w])

    req = ConfirmTopupRequest(
        razorpay_payment_id="pay_saved_card_999",
        razorpay_order_id="order_mismatch_888",
        razorpay_signature="mock_signature",
        dograh_org_id=100,
        amount_paise=2500,
    )

    with patch("services.billing_service.credit_wallet") as mock_credit:
        res = await confirm_topup(req, db=mock_db)
        mock_credit.assert_not_called()

    assert res["status"] == "already_processed"
    assert res["new_balance_paise"] == 12000


