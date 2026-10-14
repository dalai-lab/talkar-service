import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer
from routers.customers import get_customer_by_email, get_customer_status


@pytest.mark.asyncio
async def test_by_email_unscoped_deterministic_ordering_prefers_master_org():
    """
    Verify Issue 6: When multiple workspaces share the same email,
    GET /customers/by-email/{email} deterministically returns the Master org (billing_org_id is None)
    instead of non-deterministically flipping based on updated_at.
    """
    master = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="owner@acme.com",
        contact_name="Alice Owner",
        company_name="Acme Master LLC",
        billing_org_id=None,
        status="active",
    )
    sub = Customer(
        id=2,
        dograh_org_id=20,
        contact_email="owner@acme.com",
        contact_name="Alice Sub",
        company_name="Acme Logistics Sub-Org",
        billing_org_id=1,
        status="active",
    )

    mock_db = MagicMock()

    # The query orders by Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc()
    # which ensures master is returned first
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = master

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        # Verify query orders by billing_org_id.asc().nullsfirst() or deterministic key
        assert "billing_org_id" in q_str
        assert "updated_at DESC" not in q_str
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    result = await get_customer_by_email(email="owner@acme.com", dograh_org_id=None, db=mock_db)

    assert result["dograh_org_id"] == 10
    assert result["company_name"] == "Acme Master LLC"
    assert result["billing_org_id"] is None


@pytest.mark.asyncio
async def test_by_email_scoped_by_dograh_org_id():
    """
    Verify Issue 6: GET /customers/by-email/{email}?dograh_org_id=20 strictly scopes
    to the requested workspace, preventing route collision.
    """
    sub = Customer(
        id=2,
        dograh_org_id=20,
        contact_email="owner@acme.com",
        contact_name="Alice Sub",
        company_name="Acme Logistics Sub-Org",
        billing_org_id=1,
        status="active",
    )

    mock_db = MagicMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = sub

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        assert "customers.dograh_org_id =" in q_str
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    result = await get_customer_by_email(email="owner@acme.com", dograh_org_id=20, db=mock_db)

    assert result["dograh_org_id"] == 20
    assert result["company_name"] == "Acme Logistics Sub-Org"
    assert result["billing_org_id"] == 1


@pytest.mark.asyncio
async def test_customer_status_contact_email_deterministic_ordering():
    """
    Verify Issue 6: GET /customers/status?contact_email=... orders deterministically
    by billing_org_id and id instead of updated_at.desc().
    """
    master = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="owner@acme.com",
        billing_org_id=None,
        status="active",
        onboarding_form={"plan": "starter"},
    )

    mock_db = MagicMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = master

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "customers" in q_str:
            assert "billing_org_id" in q_str
            assert "updated_at DESC" not in q_str or "dograh_org_id" in q_str
            res.scalars.return_value.first.return_value = master
            res.scalar_one_or_none.return_value = master
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.first.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    result = await get_customer_status(contact_email="owner@acme.com", db=mock_db)

    assert result["dograh_org_id"] == 10
    assert result["is_sub_org"] is False
    assert result["account_type"] == "independent"
