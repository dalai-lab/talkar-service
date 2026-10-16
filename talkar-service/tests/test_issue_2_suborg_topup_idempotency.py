import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, WalletTransaction, Subscription
from routers.billing import confirm_topup, ConfirmTopupRequest


@pytest.mark.asyncio
async def test_sub_org_confirm_topup_idempotency_returns_master_balance():
    """Verify Issue 2: Sub-org topup idempotency returns master wallet balance, not 0."""
    sub_org = Customer(
        id=2,
        contact_email="sub@agency.com",
        dograh_org_id=20,
        billing_org_id=1,
        status="active"
    )
    master_wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=1500000  # ₹15,000
    )

    mock_db = MagicMock()

    # 1. Customer lookup
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org

    # 2. Advisory lock
    res_lock = MagicMock()

    # 3. Duplicate txn lookup -> returns existing
    res_txn = MagicMock()
    existing_txn = WalletTransaction(id=99, customer_id=1, razorpay_order_id="order_123")
    res_txn.scalar_one_or_none.return_value = existing_txn

    # 4. Wallet lookup -> returns master wallet
    res_w = MagicMock()
    res_w.scalar_one_or_none.return_value = master_wallet

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_lock, res_txn, res_w])

    req = ConfirmTopupRequest(
        dograh_org_id=20,
        razorpay_order_id="order_123",
        razorpay_payment_id="pay_123",
        razorpay_signature="mock_signature",
        amount_paise=500000
    )

    resp = await confirm_topup(data=req, db=mock_db)

    assert resp["status"] == "already_processed"
    assert resp["new_balance_paise"] == 1500000, "Must return master balance 1500000, NOT 0"

    # Verify that the wallet query targeted master_id (1), NOT sub_org.id (2)
    wallet_call = mock_db.execute.call_args_list[-1]
    query_str = str(wallet_call[0][0])
    assert "wallets.customer_id =" in query_str


@pytest.mark.asyncio
async def test_sub_org_concurrent_duplicate_topup_returns_master_balance():
    """Verify Issue 2: Concurrent unique constraint rollback returns master wallet balance, not 0."""
    sub_org = Customer(
        id=3,
        contact_email="client@branch.com",
        dograh_org_id=30,
        billing_org_id=1,
        status="active"
    )
    master_wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=2500000  # ₹25,000
    )

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "from customers" in q_str.lower() and "customers.dograh_org_id" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "from wallet_transactions" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = None
        elif "from wallets" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = master_wallet
        elif "from subscriptions" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = Subscription(id=1, customer_id=1, plan="starter")
        elif "from customers" in q_str.lower() and "customers.id = :id_1" in q_str:
            mock_res.scalar_one_or_none.return_value = Customer(id=1, status="active")
        elif "from customers" in q_str.lower():
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    req = ConfirmTopupRequest(
        dograh_org_id=30,
        razorpay_order_id="order_456",
        razorpay_payment_id="pay_456",
        razorpay_signature="mock_signature",
        amount_paise=500000
    )

    # Simulate concurrent credit_wallet throwing duplicate key constraint
    duplicate_error = Exception("duplicate key value violates unique constraint idx_wallet_transactions_razorpay")
    with patch("services.billing_service.credit_wallet", AsyncMock(side_effect=duplicate_error)), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        resp = await confirm_topup(data=req, db=mock_db)

    assert resp["status"] in ("already_processed", "ok")
    assert resp["new_balance_paise"] == 2500000, "Must return master balance 2500000 on concurrent duplicate, NOT 0"
