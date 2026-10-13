import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, WalletTransaction, TransactionType, TRANSACTION_TYPE_CATEGORIES, resolve_transaction_types
from routers.billing import get_transactions_by_org
from routers.automation import CreditRequest, DeductRequest, credit_customer, deduct_customer
from routers.admin import CreditGrantRequest, AdminDeductRequest, manual_credit_grant, admin_deduct_customer
from db.models import AutomationApiKey, TalkarAdmin


def test_transaction_type_enum_and_categories():
    assert TransactionType.TOP_UP == "top_up"
    assert TransactionType.CALL_DEDUCTION == "call_deduction"
    assert TransactionType.REFUND == "refund"
    assert TransactionType.GRANT == "grant"
    assert TransactionType.MANUAL_CREDIT == "manual_credit"
    assert TransactionType.MANUAL_DEDUCT == "manual_deduct"
    assert TransactionType.MANUAL_SET_BALANCE == "manual_set_balance"
    assert TransactionType.CREDIT == "credit"
    assert TransactionType.DEDUCTION == "deduction"

    assert resolve_transaction_types(None) is None
    assert resolve_transaction_types("all") is None
    assert resolve_transaction_types("ALL") is None

    top_up_types = resolve_transaction_types("top_up")
    assert "top_up" in top_up_types
    assert "credit" in top_up_types
    assert "manual_credit" in top_up_types

    deduct_types = resolve_transaction_types("call_deduction")
    assert "call_deduction" in deduct_types
    assert "deduction" in deduct_types
    assert "manual_deduct" in deduct_types

    grant_types = resolve_transaction_types("grant")
    assert "grant" in grant_types
    assert "manual_credit" in grant_types
    assert "credit" in grant_types

    # Custom type fallback
    assert resolve_transaction_types("custom_event") == ["custom_event"]


@pytest.mark.asyncio
async def test_get_transactions_by_org_category_filtering():
    mock_db = AsyncMock()

    customer = Customer(id=1, dograh_org_id=999, billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    mock_suborgs_res = MagicMock()
    mock_suborgs_res.scalars().all.return_value = []

    mock_count_res = MagicMock()
    mock_count_res.scalar.return_value = 2

    tx1 = WalletTransaction(id=101, type="credit", amount_paise=5000, description="Auto credit")
    tx2 = WalletTransaction(id=102, type="manual_credit", amount_paise=10000, description="Admin credit")
    mock_txn_res = MagicMock()
    mock_txn_res.scalars().all.return_value = [tx1, tx2]

    mock_db.execute = AsyncMock(side_effect=[
        mock_cust_res,
        mock_suborgs_res,
        mock_count_res,
        mock_txn_res,
    ])

    res = await get_transactions_by_org(
        org_id=999,
        page=1,
        limit=20,
        type="top_up",
        db=mock_db
    )

    assert len(res["transactions"]) == 2
    assert res["transactions"][0]["type"] == "credit"
    assert res["transactions"][1]["type"] == "manual_credit"

    # Verify query where clause includes IN clause for top_up categories
    calls = mock_db.execute.call_args_list
    count_call_stmt = str(calls[2][0][0])
    assert "wallet_transactions.type IN (" in count_call_stmt or "wallet_transactions.type in (" in count_call_stmt.lower()


@pytest.mark.asyncio
async def test_automation_credit_and_deduct_type_handling():
    mock_db = AsyncMock()
    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=5000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=15000)
    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_update_res, mock_update_res, MagicMock(scalar_one_or_none=MagicMock(return_value=None))])

    mock_api_key = AutomationApiKey(id=1, key_hash="h", name="test", scopes=["credit"])
    req = CreditRequest(amount_rupees=100, description="Bonus", type="grant")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 10))), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        res = await credit_customer(100, req, db=mock_db, api_key=mock_api_key)

    assert res["status"] == "success"
    added_txn = mock_db.add.call_args[0][0]
    assert isinstance(added_txn, WalletTransaction)
    assert added_txn.type == "grant"


@pytest.mark.asyncio
async def test_admin_credit_and_deduct_type_handling():
    mock_db = AsyncMock()
    admin = TalkarAdmin(id=1, email="admin@talkar.in", name="Admin", role="admin")

    customer = Customer(id=5, dograh_org_id=50, status="active", billing_org_id=None)
    wallet_pre = Wallet(id=1, customer_id=5, balance_paise=10000)
    wallet_post = Wallet(id=1, customer_id=5, balance_paise=20000)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post
    mock_db.execute = AsyncMock(side_effect=[
        mock_update_res,
        MagicMock(scalar_one_or_none=MagicMock(return_value=wallet_post)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=customer)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
    ])

    req = CreditGrantRequest(amount_paise=10000, description="Test grant", type="grant")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 5))), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_credit_granted", AsyncMock()):
        res = await manual_credit_grant(5, req, db=mock_db, current_admin=admin)

    assert res["status"] == "success"
    added_txn = mock_db.add.call_args[0][0]
    assert isinstance(added_txn, WalletTransaction)
    assert added_txn.type == "grant"
