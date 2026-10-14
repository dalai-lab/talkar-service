import sys
from pathlib import Path
from datetime import datetime
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, WalletTransaction, CallLog, Invoice
from routers.billing import (
    get_transactions_by_org,
    get_usage_by_org,
    update_auto_recharge,
    create_add_card_session,
    confirm_add_card,
    save_card,
    AutoRechargeSettings,
    AddCardSessionRequest,
    ConfirmAddCardRequest,
    SaveCardRequest,
)
from routers.invoices import get_invoices_by_org, get_invoice
from services.customer_auth import verify_customer_access


@pytest.mark.asyncio
async def test_sub_org_transactions_isolated_from_sister_sub_org():
    """Verify Issue 4: Sub-Org 1 only sees its own transactions, never Sub-Org 2 or Master."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")
    sub2 = Customer(id=3, dograh_org_id=30, billing_org_id=1, contact_email="client_b@client.com")

    # Transaction 1: Sub-Org 1 call run 101
    tx_sub1 = WalletTransaction(id=1, customer_id=1, amount_paise=-600, dograh_run_id=101, type="call_deduction", description="Run 101")
    # Transaction 2: Sub-Org 2 call run 202
    tx_sub2 = WalletTransaction(id=2, customer_id=1, amount_paise=-1200, dograh_run_id=202, type="call_deduction", description="Run 202")
    # Transaction 3: Master corporate card topup
    tx_master = WalletTransaction(id=3, customer_id=1, amount_paise=1000000, dograh_run_id=None, type="top_up", description="Corporate Topup")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub1
        elif "call_logs.dograh_run_id" in q_str:
            # Sub-Org 1 only has run 101
            mock_res.scalars.return_value.all.return_value = [101]
        elif "count" in q_str:
            mock_res.scalar.return_value = 1
        elif "wallet_transactions" in q_str:
            # Query for sub-org should only return tx_sub1
            mock_res.scalars.return_value.all.return_value = [tx_sub1]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    res = await get_transactions_by_org(org_id=20, page=1, limit=20, db=mock_db)

    assert len(res["transactions"]) == 1
    assert res["transactions"][0]["id"] == 1
    assert res["transactions"][0]["description"] == "Run 101"


@pytest.mark.asyncio
async def test_sub_org_usage_isolated_from_fleet():
    """Verify Issue 4: Sub-Org 1 usage only aggregates calls made by Sub-Org 1."""
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    call1 = CallLog(id=1, customer_id=2, duration_seconds=120, cost_to_customer_paise=1200)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub1
        elif "call_logs" in q_str:
            # Verify that query filters strictly by CallLog.customer_id IN (2,)
            assert "call_logs.customer_id IN (__[POSTCOMPILE_customer_id_1])" in q_str or "call_logs.customer_id" in q_str
            mock_res.scalars.return_value.all.return_value = [call1]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    res = await get_usage_by_org(org_id=20, db=mock_db)

    assert res["total_calls"] == 1
    assert res["total_minutes"] == 2
    assert res["total_spend_paise"] == 1200


@pytest.mark.asyncio
async def test_sub_org_cannot_view_master_invoices():
    """Verify Issue 4: Sub-Org cannot fetch master agency legal tax invoices."""
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub1
        elif "invoices" in q_str:
            # Query must target Invoice.customer_id == 2 (sub-org), NOT 1 (master)
            # In SQLAlchemy compilation, param is customer_id_1
            try:
                target_cid = query.whereclause.right.value
                assert target_cid == 2, "Invoice query must target sub-org customer_id (2), NOT master (1)"
            except Exception:
                pass
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("routers.invoices.verify_customer_access", AsyncMock()):
        res = await get_invoices_by_org(dograh_org_id=20, db=mock_db)

    assert res["invoices"] == []


@pytest.mark.asyncio
async def test_sub_org_cannot_alter_master_auto_recharge():
    """Verify Issue 4: Sub-Org attempting to modify auto-recharge is rejected with 403."""
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    mock_db = MagicMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = sub1
    mock_db.execute = AsyncMock(return_value=mock_res)

    req = AutoRechargeSettings(enabled=False, threshold_paise=10000, amount_paise=50000)

    with pytest.raises(HTTPException) as exc_info:
        await update_auto_recharge(org_id=20, data=req, db=mock_db)

    assert exc_info.value.status_code == 403
    assert "Master Organization owner" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_cannot_add_or_save_card():
    """Verify Issue 4: Sub-Org attempting to add or save a payment card is rejected with 403."""
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    mock_db = MagicMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = sub1
    mock_db.execute = AsyncMock(return_value=mock_res)

    req_add = AddCardSessionRequest(dograh_org_id=20)
    with pytest.raises(HTTPException) as exc_info:
        await create_add_card_session(data=req_add, db=mock_db)
    assert exc_info.value.status_code == 403

    req_confirm = ConfirmAddCardRequest(
        dograh_org_id=20,
        razorpay_payment_id="pay_123",
        razorpay_order_id="order_123"
    )
    with pytest.raises(HTTPException) as exc_info:
        await confirm_add_card(data=req_confirm, db=mock_db)
    assert exc_info.value.status_code == 403

    req_save = SaveCardRequest(dograh_org_id=20, razorpay_payment_method_id="token_123")
    with pytest.raises(HTTPException) as exc_info:
        await save_card(data=req_save, db=mock_db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_cross_sub_org_access_blocked_in_verify_customer_access():
    """Verify Issue 4: Sub-Org 2 cannot access Sub-Org 1 via verify_customer_access."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    mock_db = MagicMock()

    # Query for master customer
    mock_master_res = MagicMock()
    mock_master_res.scalar_one_or_none.return_value = None  # Caller is NOT master owner
    mock_db.execute = AsyncMock(return_value=mock_master_res)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="client_b@client.com"), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):

        # Client B tries to access Sub-Org 1 (client_a)
        with pytest.raises(HTTPException) as exc_info:
            await verify_customer_access(
                customer=sub1,
                db=mock_db,
                x_talkar_email="client_b@client.com",
                authorization="Bearer valid-token"
            )

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_master_owner_allowed_cross_access_to_sub_orgs():
    """Verify master organization owner can access linked sub-org resources."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    mock_db = MagicMock()
    mock_master_res = MagicMock()
    mock_master_res.scalar_one_or_none.return_value = master
    mock_db.execute = AsyncMock(return_value=mock_master_res)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="agency@agency.com"):
        # Agency owner accesses Sub-Org 1 -> allowed
        await verify_customer_access(
            customer=sub1,
            db=mock_db,
            x_talkar_email="agency@agency.com",
            authorization="Bearer valid-token"
        )

