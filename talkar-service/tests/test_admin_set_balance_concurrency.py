import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, TalkarAdmin, Wallet
from routers.admin import AdminSetBalanceRequest, admin_set_balance


@pytest.mark.asyncio
async def test_admin_set_balance_uses_for_update_lock():
    """Verify admin_set_balance executes SELECT ... FOR UPDATE to lock the wallet row exclusively."""
    mock_db = AsyncMock()
    mock_admin = TalkarAdmin(id=1, email="admin@talkar.in", role="superadmin")

    customer = Customer(id=10, billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_locked = Wallet(id=1, customer_id=10, balance_paise=5000)
    mock_lock_res = MagicMock()
    mock_lock_res.scalar_one_or_none.return_value = wallet_locked

    wallet_updated = Wallet(id=1, customer_id=10, balance_paise=20000)
    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_updated

    mock_db.execute = AsyncMock(
        side_effect=[mock_cust_res, mock_lock_res, mock_update_res]
    )

    req = AdminSetBalanceRequest(amount_paise=20000, reason="Manual adjustment")

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_locked, 10))),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync,
    ):
        res = await admin_set_balance(10, req, db=mock_db, current_admin=mock_admin)

    assert res["status"] == "success"
    assert res["new_balance_paise"] == 20000

    # Verify that the second execute call was SELECT with FOR UPDATE
    calls = mock_db.execute.call_args_list
    lock_query = str(calls[1][0][0])
    assert "FOR UPDATE" in lock_query.upper()
    mock_sync.assert_awaited_once_with(mock_db, 10)


@pytest.mark.asyncio
async def test_admin_set_balance_raises_409_on_concurrent_modification():
    """When expected_balance_paise does not match locked wallet balance, raise 409 Conflict without overwriting."""
    mock_db = AsyncMock()
    mock_admin = TalkarAdmin(id=1, email="admin@talkar.in", role="superadmin")

    customer = Customer(id=10, billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    # Admin saw 5000 paise on UI, but concurrent call deduction dropped balance to 4200 paise
    wallet_locked = Wallet(id=1, customer_id=10, balance_paise=4200)
    mock_lock_res = MagicMock()
    mock_lock_res.scalar_one_or_none.return_value = wallet_locked

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_lock_res])

    req = AdminSetBalanceRequest(
        amount_paise=20000,
        reason="Manual adjustment",
        expected_balance_paise=5000  # Stale balance from admin UI
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_locked, 10))),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await admin_set_balance(10, req, db=mock_db, current_admin=mock_admin)

    assert exc_info.value.status_code == 409
    assert "Concurrent modification detected" in exc_info.value.detail
    assert "Current balance is 4200 paise, but expected 5000 paise" in exc_info.value.detail
    # Update and commit must not have been executed
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_admin_set_balance_records_correct_diff_in_transaction():
    """Verify transaction records the exact diff between locked balance and new balance."""
    mock_db = AsyncMock()
    mock_admin = TalkarAdmin(id=1, email="admin@talkar.in", role="superadmin")

    customer = Customer(id=10, billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_locked = Wallet(id=1, customer_id=10, balance_paise=8000)
    mock_lock_res = MagicMock()
    mock_lock_res.scalar_one_or_none.return_value = wallet_locked

    wallet_updated = Wallet(id=1, customer_id=10, balance_paise=10000)
    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_updated

    mock_db.execute = AsyncMock(
        side_effect=[mock_cust_res, mock_lock_res, mock_update_res]
    )

    req = AdminSetBalanceRequest(
        amount_paise=10000,
        reason="Correcting balance discrepancy",
        expected_balance_paise=8000
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_locked, 10))),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
    ):
        res = await admin_set_balance(10, req, db=mock_db, current_admin=mock_admin)

    assert res["status"] == "success"
    assert res["new_balance_paise"] == 10000

    # Ensure transaction was added with diff = 10000 - 8000 = +2000 paise
    added_txn = mock_db.add.call_args[0][0]
    assert added_txn.amount_paise == 2000
    assert added_txn.type == "manual_set_balance"
    mock_db.commit.assert_awaited_once()
