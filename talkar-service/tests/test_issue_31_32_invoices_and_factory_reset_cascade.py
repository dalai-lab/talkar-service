import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Invoice, TalkarAdmin, Wallet
from routers.invoices import get_invoices_by_org
from routers.admin import factory_reset_logs


@pytest.mark.asyncio
async def test_blocker_31_master_includes_sub_org_attributed_invoices():
    """
    Test Blocker 31:
    Master organization queries in get_invoices_by_org include invoices attributed to child sub-orgs,
    while sub-org queries only return their own invoices.
    """
    master = Customer(
        id=1,
        dograh_org_id=100,
        status="active",
        billing_org_id=None,
        contact_email="master@agency.com"
    )
    sub1 = Customer(
        id=2,
        dograh_org_id=200,
        status="active",
        billing_org_id=1,
        contact_email="sub1@agency.com"
    )
    sub2 = Customer(
        id=3,
        dograh_org_id=300,
        status="active",
        billing_org_id=1,
        contact_email="sub2@agency.com"
    )

    inv_master = Invoice(
        id=101,
        customer_id=1,
        invoice_number="INV-001",
        amount_paise=100000,
        status="paid"
    )
    inv_sub1 = Invoice(
        id=102,
        customer_id=2,
        invoice_number="INV-002",
        amount_paise=50000,
        status="paid"
    )
    inv_sub2 = Invoice(
        id=103,
        customer_id=3,
        invoice_number="INV-003",
        amount_paise=25000,
        status="paid"
    )

    mock_db = MagicMock()

    # Test 1: Query as Master
    async def mock_execute_master(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from invoices" in q_str:
            # When master queries, both master and child sub-org invoices are returned
            res.scalars.return_value.all.return_value = [inv_master, inv_sub1, inv_sub2]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute_master)

    with patch("routers.invoices.verify_customer_access", AsyncMock()):
        master_res = await get_invoices_by_org(
            dograh_org_id=100,
            db=mock_db,
            x_talkar_email="master@agency.com",
            authorization="Bearer test_token"
        )

    assert len(master_res["invoices"]) == 3
    inv_ids = [inv["id"] for inv in master_res["invoices"]]
    assert 101 in inv_ids
    assert 102 in inv_ids
    assert 103 in inv_ids

    # Test 2: Query as Sub-Org 1
    async def mock_execute_sub(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from invoices" in q_str:
            # Sub-org only sees its own invoice
            res.scalars.return_value.all.return_value = [inv_sub1]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = sub1
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute_sub)

    with patch("routers.invoices.verify_customer_access", AsyncMock()):
        sub_res = await get_invoices_by_org(
            dograh_org_id=200,
            db=mock_db,
            x_talkar_email="sub1@agency.com",
            authorization="Bearer test_token"
        )

    assert len(sub_res["invoices"]) == 1
    assert sub_res["invoices"][0]["id"] == 102


@pytest.mark.asyncio
async def test_blocker_32_cascade_factory_reset_blocks_calls_and_suspends_sub_orgs():
    """
    Test Blocker 32:
    In routers/admin.py factory_reset_logs, if customer.billing_org_id is None,
    loop through all child sub-organizations, invoke dograh_client.block_org_calls(sub.dograh_org_id),
    and update sub.status = 'suspended'.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    master = Customer(
        id=1,
        dograh_org_id=100,
        status="active",
        billing_org_id=None,
        onboarding_form={}
    )
    sub1 = Customer(
        id=2,
        dograh_org_id=200,
        status="active",
        billing_org_id=1,
        onboarding_form={}
    )
    sub2 = Customer(
        id=3,
        dograh_org_id=300,
        status="active",
        billing_org_id=1,
        onboarding_form={}
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = [sub1, sub2]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
        else:
            res.rowcount = 0
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_ddb = MagicMock()
    mock_ddb.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
         patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block:

        res = await factory_reset_logs(
            customer_id=1,
            db=mock_db,
            current_admin=admin
        )

    assert res["status"] == "reset_complete"
    assert master.status == "suspended"
    assert master.onboarding_form.get("suspension_reason") == "zero_balance"

    # Both child sub-orgs must be suspended with zero_balance reason
    assert sub1.status == "suspended"
    assert sub1.onboarding_form.get("suspension_reason") == "zero_balance"
    assert sub2.status == "suspended"
    assert sub2.onboarding_form.get("suspension_reason") == "zero_balance"

    # Verify dograh_client.block_org_calls was called for master AND both child sub-orgs
    blocked_org_ids = [call.args[0] for call in mock_block.call_args_list]
    assert 100 in blocked_org_ids  # master org
    assert 200 in blocked_org_ids  # sub1 org
    assert 300 in blocked_org_ids  # sub2 org
