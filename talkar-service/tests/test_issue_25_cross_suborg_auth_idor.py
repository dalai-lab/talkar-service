import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Invoice
from services.customer_auth import verify_customer_access
from routers.invoices import get_invoice, get_invoices_by_org


@pytest.mark.asyncio
async def test_sub_org_caller_cannot_access_sibling_sub_org_via_verify_customer_access():
    """Verify Issue 25: Client A on Sub-Org 1 cannot access Sub-Org 2 even though both share billing_org_id."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")
    sub2 = Customer(id=3, dograh_org_id=30, billing_org_id=1, contact_email="client_b@client.com")

    mock_db = MagicMock()
    # When querying for master customer (Customer.id == 1), return master
    mock_master_res = MagicMock()
    mock_master_res.scalar_one_or_none.return_value = master
    mock_db.execute = AsyncMock(return_value=mock_master_res)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="client_a@client.com"), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):

        # Client A tries to access Sub-Org 2
        with pytest.raises(HTTPException) as exc_info:
            await verify_customer_access(
                customer=sub2,
                db=mock_db,
                x_talkar_email="client_a@client.com",
                authorization="Bearer valid-token"
            )

        assert exc_info.value.status_code == 403
        assert "Forbidden" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_caller_cannot_access_master_account_via_verify_customer_access():
    """Verify Issue 25: Client A on Sub-Org 1 cannot access Master organization resources."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")

    mock_db = MagicMock()

    with patch("services.customer_auth.resolve_authenticated_email", return_value="client_a@client.com"), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):

        # Client A tries to access Master
        with pytest.raises(HTTPException) as exc_info:
            await verify_customer_access(
                customer=master,
                db=mock_db,
                x_talkar_email="client_a@client.com",
                authorization="Bearer valid-token"
            )

        assert exc_info.value.status_code == 403
        assert "Forbidden" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_caller_cannot_view_sibling_sub_org_invoice():
    """Verify Issue 25: Client A cannot fetch Sub-Org 2's legal invoice via GET /invoices/{id}."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub2 = Customer(id=3, dograh_org_id=30, billing_org_id=1, contact_email="client_b@client.com", company_name="Client B Ltd")
    inv = Invoice(id=301, customer_id=3, invoice_number="INV-SUB2-001", amount_paise=50000, status="paid")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "invoices.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = inv
        elif "customers.id =" in q_str:
            # Query by customer_id: if querying for sub2 (id=3), return sub2; if querying for master (id=1), return master
            if "3" in q_str:
                mock_res.scalar_one_or_none.return_value = sub2
            else:
                mock_res.scalar_one_or_none.return_value = master
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="client_a@client.com"), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):

        with pytest.raises(HTTPException) as exc_info:
            await get_invoice(
                invoice_id=301,
                db=mock_db,
                x_talkar_email="client_a@client.com",
                authorization="Bearer token"
            )

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_sub_org_caller_cannot_view_master_invoice():
    """Verify Issue 25: Client A cannot fetch Master legal invoice via GET /invoices/{id}."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com", company_name="Agency Inc")
    inv = Invoice(id=101, customer_id=1, invoice_number="INV-MASTER-001", amount_paise=1000000, status="paid")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "invoices.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = inv
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="client_a@client.com"), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):

        with pytest.raises(HTTPException) as exc_info:
            await get_invoice(
                invoice_id=101,
                db=mock_db,
                x_talkar_email="client_a@client.com",
                authorization="Bearer token"
            )

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_sub_org_caller_cannot_list_sibling_sub_org_invoices():
    """Verify Issue 25: Client A cannot list Sub-Org 2 invoices via GET /invoices/by-org/{dograh_org_id}."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub2 = Customer(id=3, dograh_org_id=30, billing_org_id=1, contact_email="client_b@client.com")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub2
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="client_a@client.com"), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):

        with pytest.raises(HTTPException) as exc_info:
            await get_invoices_by_org(
                dograh_org_id=30,
                db=mock_db,
                x_talkar_email="client_a@client.com",
                authorization="Bearer token"
            )

        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_master_owner_allowed_access_to_sub_org_invoices():
    """Verify Master owner can view sub-org invoices."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub2 = Customer(id=3, dograh_org_id=30, billing_org_id=1, contact_email="client_b@client.com", company_name="Client B Ltd")
    inv = Invoice(id=301, customer_id=3, invoice_number="INV-SUB2-001", amount_paise=50000, status="paid")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "invoices.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = inv
        elif "customers.id =" in q_str:
            if "3" in q_str:
                mock_res.scalar_one_or_none.return_value = sub2
            else:
                mock_res.scalar_one_or_none.return_value = master
        elif "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub2
        elif "invoices.customer_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [inv]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="agency@agency.com"):
        res = await get_invoice(
            invoice_id=301,
            db=mock_db,
            x_talkar_email="agency@agency.com",
            authorization="Bearer token"
        )
        assert res["id"] == 301
        assert res["invoice_number"] == "INV-SUB2-001"

        list_res = await get_invoices_by_org(
            dograh_org_id=30,
            db=mock_db,
            x_talkar_email="agency@agency.com",
            authorization="Bearer token"
        )
        assert len(list_res["invoices"]) == 1


@pytest.mark.asyncio
async def test_master_owner_case_insensitive_email_allowed():
    """Verify Master owner email check is case-insensitive."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="Agency@Agency.COM")
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    mock_db = MagicMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = master
    mock_db.execute = AsyncMock(return_value=mock_res)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="agency@agency.com"):
        # Accessing sub1 with lowercased caller email
        await verify_customer_access(
            customer=sub1,
            db=mock_db,
            x_talkar_email="agency@agency.com",
            authorization="Bearer token"
        )


@pytest.mark.asyncio
async def test_master_dograh_org_owner_allowed_sub_org_access():
    """Verify that an owner of master's Dograh org is allowed to access sub-org resources."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, contact_email="agency@agency.com")
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com")

    mock_db = MagicMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = master
    mock_db.execute = AsyncMock(return_value=mock_res)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="partner@agency.com"), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)):

        await verify_customer_access(
            customer=sub1,
            db=mock_db,
            x_talkar_email="partner@agency.com",
            authorization="Bearer token"
        )


@pytest.mark.asyncio
async def test_sub_org_owner_allowed_access_to_own_invoices():
    """Verify Sub-Org owner can view their own invoices."""
    sub1 = Customer(id=2, dograh_org_id=20, billing_org_id=1, contact_email="client_a@client.com", company_name="Client A Ltd")
    inv = Invoice(id=201, customer_id=2, invoice_number="INV-SUB1-001", amount_paise=30000, status="paid")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "invoices.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = inv
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub1
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.customer_auth.resolve_authenticated_email", return_value="client_a@client.com"):
        res = await get_invoice(
            invoice_id=201,
            db=mock_db,
            x_talkar_email="client_a@client.com",
            authorization="Bearer token"
        )
        assert res["id"] == 201
        assert res["invoice_number"] == "INV-SUB1-001"
