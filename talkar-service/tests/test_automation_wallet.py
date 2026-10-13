import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import AutomationApiKey, Customer, Wallet
from routers.automation import CreditRequest, DeductRequest, credit_customer, deduct_customer


@pytest.mark.asyncio
async def test_automation_credit_uses_atomic_update():
    """Verify that credit_customer executes atomic SQL update(Wallet) rather than in-memory mutation."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=5000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=15000)

    # 1st query: customer lookup
    # 2nd query: update(Wallet)...returning(Wallet)
    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    # 3rd query: post-commit wallet lookup
    # 4th query: subscription lookup
    mock_sub_res = MagicMock()
    mock_sub_res.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(
        side_effect=[mock_cust_res, mock_update_res, mock_update_res, mock_sub_res]
    )

    req = CreditRequest(amount_rupees=100, description="Bonus credit")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash", name="test", scopes=["credit"])

    with (
        patch(
            "services.billing_service.get_billing_wallet",
            AsyncMock(return_value=(wallet_pre, 10)),
        ),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
    ):
        res = await credit_customer(100, req, db=mock_db, api_key=mock_api_key)

    assert res["status"] == "success"
    assert res["new_balance_rupees"] == 150.0

    # Ensure update(Wallet) was executed in db.execute calls
    calls = mock_db.execute.call_args_list
    update_clause = str(calls[1][0][0])
    assert "UPDATE wallets" in update_clause
    assert "balance_paise" in update_clause

    # Verify transaction was added and committed
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_automation_deduct_uses_atomic_update():
    """Verify that deduct_customer executes atomic SQL update(Wallet) with balance check."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=20000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=10000)

    # 1st query: customer lookup
    # 2nd query: update(Wallet)...where(balance_paise >= amount)...returning(Wallet)
    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_update_res])

    req = DeductRequest(amount_rupees=100, reason="Manual fee")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash2", name="test2", scopes=["deduct"])

    with (
        patch(
            "services.billing_service.get_billing_wallet",
            AsyncMock(return_value=(wallet_pre, 10)),
        ),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync,
    ):
        res = await deduct_customer(100, req, db=mock_db, api_key=mock_api_key)

    assert res["status"] == "success"
    assert res["new_balance_rupees"] == 100.0

    calls = mock_db.execute.call_args_list
    update_clause = str(calls[1][0][0])
    assert "UPDATE wallets" in update_clause
    assert "balance_paise" in update_clause

    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited()
    mock_sync.assert_awaited_once_with(mock_db, 10)


@pytest.mark.asyncio
async def test_automation_deduct_insufficient_balance_rejected():
    """Verify that deduct_customer rejects when atomic update returns None due to insufficient balance."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=5000)

    # 1st query: customer lookup
    # 2nd query: update(Wallet) returns None (concurrent drain or insufficient)
    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = None

    # 3rd query: current wallet balance query for error message
    mock_curr_res = MagicMock()
    mock_curr_res.scalar_one_or_none.return_value = wallet_pre

    mock_db.execute = AsyncMock(
        side_effect=[mock_cust_res, mock_update_res, mock_curr_res]
    )

    req = DeductRequest(amount_rupees=100, reason="Manual fee")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash3", name="test3", scopes=["deduct"])

    with patch(
        "services.billing_service.get_billing_wallet",
        AsyncMock(return_value=(wallet_pre, 10)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await deduct_customer(100, req, db=mock_db, api_key=mock_api_key)

    assert exc_info.value.status_code == 400
    assert "Insufficient balance" in exc_info.value.detail


@pytest.mark.asyncio
async def test_credit_customer_sends_positive_notification_amount():
    """Verify that credit_customer dispatches notify_customer_credit_granted with positive amount_paise."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=5000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=55000)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post
    mock_sub_res = MagicMock()
    mock_sub_res.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(
        side_effect=[mock_cust_res, mock_update_res, mock_update_res, mock_sub_res]
    )

    req = CreditRequest(amount_rupees=500, description="Promo reward")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash", name="test", scopes=["credit"])

    with (
        patch(
            "services.billing_service.get_billing_wallet",
            AsyncMock(return_value=(wallet_pre, 10)),
        ),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
        patch("services.notification_service.notify_customer_credit_granted") as mock_notify,
    ):
        await credit_customer(100, req, db=mock_db, api_key=mock_api_key)

    # amount_paise must be positive (50,000 paise = ₹500), never negative (-50,000)
    mock_notify.assert_called_once_with(
        customer_id=10,
        amount_paise=50000,
        description="[Automated] Promo reward",
    )


@pytest.mark.asyncio
async def test_notify_customer_credit_granted_formats_positive_amount():
    """Verify notify_customer_credit_granted formats positive currency in email and push body."""
    from services.notification_service import notify_customer_credit_granted

    with (
        patch("services.notification_service._is_building_stage", AsyncMock(return_value=False)),
        patch(
            "services.notification_service._get_customer_email",
            AsyncMock(return_value=("user@example.com", "Alice")),
        ),
        patch("services.notification_service.send_email_and_push", AsyncMock()) as mock_send,
    ):
        await notify_customer_credit_granted(
            customer_id=10,
            amount_paise=50000,
            description="Welcome bonus",
        )

        mock_send.assert_awaited_once()
        kwargs = mock_send.call_args.kwargs
        assert "₹500.00" in kwargs["body"]
        assert "₹-500" not in kwargs["body"]
        assert "₹500.00" in kwargs["push_body"]


@pytest.mark.asyncio
async def test_automation_credit_floating_point_precision():
    """Verify amount_rupees=19.99 converts to 1999 paise, not 1998 paise due to IEEE 754 truncation."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=5000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=6999)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post
    mock_sub_res = MagicMock()
    mock_sub_res.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(
        side_effect=[mock_cust_res, mock_update_res, mock_update_res, mock_sub_res]
    )

    req = CreditRequest(amount_rupees=19.99, description="Precise credit")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash", name="test", scopes=["credit"])

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 10))),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
        patch("services.notification_service.notify_customer_credit_granted"),
    ):
        res = await credit_customer(100, req, db=mock_db, api_key=mock_api_key)

    # Added txn must have exact 1999 paise (not 1998)
    added_txn = mock_db.add.call_args[0][0]
    assert added_txn.amount_paise == 1999
    assert res["status"] == "success"


@pytest.mark.asyncio
async def test_automation_deduct_floating_point_precision():
    """Verify amount_rupees=19.99 converts to 1999 paise in deduct_customer."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    mock_cust_res = MagicMock()
    mock_cust_res.scalar_one_or_none.return_value = customer

    wallet_pre = Wallet(id=1, customer_id=10, balance_paise=10000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=8001)

    mock_update_res = MagicMock()
    mock_update_res.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[mock_cust_res, mock_update_res])

    req = DeductRequest(amount_rupees=19.99, reason="Precise deduction")
    mock_api_key = AutomationApiKey(id=1, key_hash="hash2", name="test2", scopes=["deduct"])

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet_pre, 10))),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
    ):
        res = await deduct_customer(100, req, db=mock_db, api_key=mock_api_key)

    # Added txn must have exact -1999 paise (not -1998)
    added_txn = mock_db.add.call_args[0][0]
    assert added_txn.amount_paise == -1999
    assert res["status"] == "success"


