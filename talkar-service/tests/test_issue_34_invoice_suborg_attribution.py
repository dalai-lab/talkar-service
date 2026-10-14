"""
Tests for Issue 34: Top-Up Receipt Invoices Sub-Org Attribution & Legal Entity Separation.

Verifies:
1. When a sub-org performs a wallet top-up, the tax receipt invoice is generated with:
   - customer_id = sub_org.id (the paying legal entity, NOT master agency)
   - sub_org_id = sub_org.id
   - dograh_org_id = sub_org.dograh_org_id
2. When a master agency tops up, the invoice is generated with:
   - customer_id = master.id
   - sub_org_id = None
   - dograh_org_id = None
3. Sub-orgs can list their own top-up receipt invoices via GET /invoices/by-org/{dograh_org_id}.
4. GET /invoices/{invoice_id} returns the sub-org's corporate legal entity details
   (company_name, contact_email, contact_name, contact_phone, gstin, billing_address)
   for ITC (Input Tax Credit) and tax compliance.
5. Telephony DID lease invoices correctly attribute customer_id, sub_org_id, and dograh_org_id
   to the sub-organization.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Invoice, Wallet, WalletTransaction
from routers.invoices import get_invoice, get_invoices_by_org
from services.billing_service import credit_wallet, charge_initial_phone_number_lease


@pytest.mark.asyncio
async def test_credit_wallet_sub_org_topup_attributes_invoice_to_sub_org():
    """Verify Issue 34: Top-up by sub-org creates invoice issued to sub-org customer_id with sub_org_id & dograh_org_id."""
    sub_org = Customer(
        id=30,
        billing_org_id=5,
        dograh_org_id=205,
        company_name="Acme Client Sub-Org LLC",
        contact_name="Alice Client",
        contact_email="alice@acmeclient.com",
        status="active",
    )
    master = Customer(
        id=5,
        billing_org_id=None,
        dograh_org_id=105,
        company_name="Alpha Master Agency Corp",
        contact_name="Bob Agency",
        contact_email="bob@alphamaster.com",
        status="active",
    )
    master_wallet = Wallet(id=1, customer_id=5, balance_paise=100000)

    db = AsyncMock()

    bw_c1 = MagicMock()
    bw_c1.scalar_one_or_none.return_value = sub_org

    bw_c2 = MagicMock()
    bw_c2.scalar_one_or_none.return_value = master

    bw_w = MagicMock()
    bw_w.scalar_one_or_none.return_value = master_wallet

    idemp_res = MagicMock()
    idemp_res.scalar_one_or_none.return_value = None

    updated_wallet = Wallet(id=1, customer_id=5, balance_paise=600000)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    dograh_id_res = MagicMock()
    dograh_id_res.scalar_one_or_none.return_value = 205

    db.execute.side_effect = [
        bw_c1,
        bw_c2,
        bw_w,
        idemp_res,
        update_res,
        dograh_id_res,
    ]

    wallet = await credit_wallet(
        db,
        customer_id=30,
        amount_paise=500000,
        razorpay_order_id="order_suborg_invoice_1",
        description="Sub-org branch deposit",
    )

    added_objects = [call[0][0] for call in db.add.call_args_list]
    invoices = [o for o in added_objects if isinstance(o, Invoice)]
    assert len(invoices) == 1, "Invoice must be created on top-up"

    inv = invoices[0]
    # Legal separation: invoice MUST be issued to sub-org (30), NOT master (5)
    assert inv.customer_id == 30, f"Expected invoice.customer_id == 30, got {inv.customer_id}"
    assert inv.sub_org_id == 30, f"Expected invoice.sub_org_id == 30, got {inv.sub_org_id}"
    assert inv.dograh_org_id == 205, f"Expected invoice.dograh_org_id == 205, got {inv.dograh_org_id}"
    assert inv.amount_paise == 500000
    assert inv.status == "paid"
    assert inv.invoice_number.startswith("INV-")


@pytest.mark.asyncio
async def test_credit_wallet_master_topup_attributes_invoice_to_master():
    """Verify master agency top-up creates invoice issued to master customer_id without sub_org_id."""
    master = Customer(
        id=5,
        billing_org_id=None,
        dograh_org_id=105,
        company_name="Alpha Master Agency Corp",
        contact_name="Bob Agency",
        contact_email="bob@alphamaster.com",
        status="active",
    )
    master_wallet = Wallet(id=1, customer_id=5, balance_paise=100000)

    db = AsyncMock()

    bw_c = MagicMock()
    bw_c.scalar_one_or_none.return_value = master

    bw_w = MagicMock()
    bw_w.scalar_one_or_none.return_value = master_wallet

    idemp_res = MagicMock()
    idemp_res.scalar_one_or_none.return_value = None

    updated_wallet = Wallet(id=1, customer_id=5, balance_paise=300000)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    db.execute.side_effect = [
        bw_c,
        bw_w,
        idemp_res,
        update_res,
    ]

    wallet = await credit_wallet(
        db,
        customer_id=5,
        amount_paise=200000,
        razorpay_order_id="order_master_invoice_1",
        description="Master agency top-up",
    )

    added_objects = [call[0][0] for call in db.add.call_args_list]
    invoices = [o for o in added_objects if isinstance(o, Invoice)]
    assert len(invoices) == 1

    inv = invoices[0]
    assert inv.customer_id == 5
    assert inv.sub_org_id is None
    assert inv.dograh_org_id is None
    assert inv.amount_paise == 200000


@pytest.mark.asyncio
async def test_get_invoices_by_org_returns_sub_org_invoices_with_attribution():
    """Verify sub-org receives invoices issued to its customer_id with sub_org_id and dograh_org_id."""
    sub_org = Customer(id=30, billing_org_id=5, dograh_org_id=205, contact_email="alice@acmeclient.com")
    inv = Invoice(
        id=401,
        customer_id=30,
        sub_org_id=30,
        dograh_org_id=205,
        invoice_number="INV-2026-SUB30",
        amount_paise=500000,
        status="paid",
    )

    mock_db = MagicMock()

    async def mock_exec(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            res.scalar_one_or_none.return_value = sub_org
        elif "invoices" in q_str:
            res.scalars.return_value.all.return_value = [inv]
        return res

    mock_db.execute = AsyncMock(side_effect=mock_exec)

    with patch("routers.invoices.verify_customer_access", AsyncMock()):
        resp = await get_invoices_by_org(
            dograh_org_id=205,
            db=mock_db,
            x_talkar_email="alice@acmeclient.com",
            authorization="Bearer token",
        )

    assert "invoices" in resp
    assert len(resp["invoices"]) == 1
    inv_item = resp["invoices"][0]
    assert inv_item["id"] == 401
    assert inv_item["invoice_number"] == "INV-2026-SUB30"
    assert inv_item["amount_paise"] == 500000
    assert inv_item["sub_org_id"] == 30
    assert inv_item["dograh_org_id"] == 205


@pytest.mark.asyncio
async def test_get_invoice_returns_sub_org_legal_entity_for_tax_compliance():
    """Verify GET /invoices/{id} returns the sub-org legal entity details and tax metadata (GSTIN, address)."""
    sub_org = Customer(
        id=30,
        billing_org_id=5,
        dograh_org_id=205,
        company_name="Acme Healthcare Solutions Pvt Ltd",
        contact_name="Alice Doctor",
        contact_email="alice@acmehealthcare.com",
        contact_phone="+919876543210",
        onboarding_form={
            "gstin": "27AABCU9603R1ZM",
            "billing_address": "Suite 400, Medical Plaza, Mumbai, MH 400001",
        },
    )
    inv = Invoice(
        id=402,
        customer_id=30,
        sub_org_id=30,
        dograh_org_id=205,
        invoice_number="INV-2026-TAX-001",
        amount_paise=1000000,
        status="paid",
    )

    mock_db = MagicMock()

    async def mock_exec(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "invoices.id =" in q_str:
            res.scalar_one_or_none.return_value = inv
        elif "customers.id =" in q_str:
            res.scalar_one_or_none.return_value = sub_org
        return res

    mock_db.execute = AsyncMock(side_effect=mock_exec)

    with patch("routers.invoices.verify_customer_access", AsyncMock()):
        resp = await get_invoice(
            invoice_id=402,
            db=mock_db,
            x_talkar_email="alice@acmehealthcare.com",
            authorization="Bearer token",
        )

    assert resp["id"] == 402
    assert resp["invoice_number"] == "INV-2026-TAX-001"
    assert resp["amount_paise"] == 1000000
    assert resp["sub_org_id"] == 30
    assert resp["dograh_org_id"] == 205

    # Customer legal entity details must belong to Sub-Org, NOT Master Agency
    cust = resp["customer"]
    assert cust["id"] == 30
    assert cust["company_name"] == "Acme Healthcare Solutions Pvt Ltd"
    assert cust["contact_name"] == "Alice Doctor"
    assert cust["contact_email"] == "alice@acmehealthcare.com"
    assert cust["contact_phone"] == "+919876543210"
    assert cust["gstin"] == "27AABCU9603R1ZM"
    assert cust["billing_address"] == "Suite 400, Medical Plaza, Mumbai, MH 400001"


@pytest.mark.asyncio
async def test_initial_phone_lease_invoice_attribution_to_sub_org():
    """Verify charge_initial_phone_number_lease creates invoice issued to sub_org with sub_org_id & dograh_org_id."""
    sub_org = Customer(id=30, billing_org_id=5, dograh_org_id=205, status="active", company_name="Acme Branch")
    master = Customer(id=5, billing_org_id=None, status="active")
    master_wallet = Wallet(id=1, customer_id=5, balance_paise=1000000)
    phone = MagicMock()
    phone.id = 99
    phone.number = "+14155552671"
    phone.monthly_cost_paise = 50000

    db = AsyncMock()

    # Query 1: get_billing_wallet
    bw_c1 = MagicMock()
    bw_c1.scalar_one_or_none.return_value = sub_org
    bw_c2 = MagicMock()
    bw_c2.scalar_one_or_none.return_value = master
    bw_w = MagicMock()
    bw_w.scalar_one_or_none.return_value = master_wallet

    # Query 2: Subscription lookup
    from db.models import Subscription
    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = Subscription(customer_id=5, plan="starter")

    # Query 3: fleet_cust_ids lookup
    fleet_res = MagicMock()
    fleet_res.scalars.return_value.all.return_value = [5, 30]

    # Query 4: active_pn count (1 existing active, allowance 1 -> extra billable)
    active_pn_res = MagicMock()
    active_pn_res.scalar_one.return_value = 1

    # Query 5: update wallet balance
    upd_w = Wallet(id=1, customer_id=5, balance_paise=950000)
    upd_res = MagicMock()
    upd_res.scalar_one_or_none.return_value = upd_w

    # Query 6: Customer lookup for company_name and dograh_org_id
    c_res = MagicMock()
    c_row = MagicMock()
    c_row.dograh_org_id = 205
    c_row.company_name = "Acme Branch"
    c_res.fetchone.return_value = c_row

    db.execute.side_effect = [
        bw_c1,
        bw_c2,
        bw_w,
        sub_res,
        fleet_res,
        active_pn_res,
        upd_res,
        c_res,
    ]

    with patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()):
        await charge_initial_phone_number_lease(
            db=db,
            customer_id=30,
            phone_number=phone,
        )

    added_objects = [call[0][0] for call in db.add.call_args_list]
    invoices = [o for o in added_objects if isinstance(o, Invoice)]
    assert len(invoices) == 1

    inv = invoices[0]
    assert inv.customer_id == 30
    assert inv.sub_org_id == 30
    assert inv.dograh_org_id == 205
    assert inv.amount_paise == 50000
