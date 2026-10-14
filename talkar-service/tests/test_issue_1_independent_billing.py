import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.customers import (
    create_customer,
    CreateCustomerRequest,
    set_billing_account_type,
    SetBillingAccountTypeRequest,
    get_customer_status,
)


@pytest.mark.asyncio
async def test_create_customer_independent_billing_leaves_billing_org_id_none():
    """Verify that specifying account_type='independent' creates a standalone master account under an existing email."""
    master = Customer(
        id=1,
        contact_email="founder@acme.com",
        contact_name="Founder",
        company_name="Acme Logistics LLC",
        dograh_org_id=10,
        dograh_user_id=100,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    res_none = MagicMock()
    res_none.scalars.return_value.first.return_value = None
    res_none.scalar_one_or_none.return_value = None

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute.side_effect = [res_none, res_none, res_master]

    req = CreateCustomerRequest(
        email="founder@acme.com",
        contact_name="Founder",
        company_name="Acme Health Clinic",
        dograh_org_id=20,
        dograh_user_id=100,
        account_type="independent"
    )

    with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)):
        cust = await create_customer(data=req, db=mock_db, authorization="Bearer change-me-in-production-billing-token")

    assert cust.dograh_org_id == 20
    assert cust.billing_org_id is None, "Independent account must NOT inherit billing_org_id"
    assert cust.company_name == "Acme Health Clinic"


@pytest.mark.asyncio
async def test_create_customer_link_billing_false_leaves_billing_org_id_none():
    """Verify that link_billing=False creates a standalone master customer."""
    master = Customer(
        id=1,
        contact_email="consultant@agency.com",
        contact_name="Consultant",
        dograh_org_id=10,
        dograh_user_id=200,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    res_none = MagicMock()
    res_none.scalars.return_value.first.return_value = None
    res_none.scalar_one_or_none.return_value = None

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute.side_effect = [res_none, res_none, res_master]

    req = CreateCustomerRequest(
        email="consultant@agency.com",
        contact_name="Consultant",
        company_name="Client A Workspace",
        dograh_org_id=30,
        dograh_user_id=200,
        link_billing=False
    )

    with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)):
        cust = await create_customer(data=req, db=mock_db, authorization="Bearer change-me-in-production-billing-token")

    assert cust.dograh_org_id == 30
    assert cust.billing_org_id is None, "link_billing=False must result in billing_org_id=None"


@pytest.mark.asyncio
async def test_create_customer_shared_billing_links_to_master():
    """Verify that account_type='shared' links to the existing master account."""
    master = Customer(
        id=5,
        contact_email="owner@brand.com",
        contact_name="Owner",
        company_name="Main Brand",
        dograh_org_id=15,
        dograh_user_id=300,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    res_none = MagicMock()
    res_none.scalars.return_value.first.return_value = None
    res_none.scalar_one_or_none.return_value = None

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute.side_effect = [res_none, res_none, res_master]

    req = CreateCustomerRequest(
        email="owner@brand.com",
        contact_name="Owner",
        company_name="Second Location",
        dograh_org_id=40,
        dograh_user_id=300,
        account_type="shared"
    )

    with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)):
        cust = await create_customer(data=req, db=mock_db, authorization="Bearer change-me-in-production-billing-token")

    assert cust.dograh_org_id == 40
    assert cust.billing_org_id == 5, "Shared account must link to master id 5"


@pytest.mark.asyncio
async def test_set_billing_account_type_endpoint():
    """Verify POST /customers/by-org/{dograh_org_id}/billing-account-type toggles independent vs shared."""
    sub_org = Customer(
        id=2,
        contact_email="user@domain.com",
        dograh_org_id=50,
        dograh_user_id=400,
        billing_org_id=1,
        status="pending_approval"
    )
    master = Customer(
        id=1,
        contact_email="user@domain.com",
        dograh_org_id=45,
        dograh_user_id=400,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    # 1. Switch to independent
    res_sub = MagicMock()
    res_sub.scalars.return_value.first.return_value = sub_org

    mock_db.execute.return_value = res_sub

    with patch("routers.customers.verify_customer_access", AsyncMock()):
        resp = await set_billing_account_type(
            dograh_org_id=50,
            data=SetBillingAccountTypeRequest(account_type="independent"),
            db=mock_db,
            x_talkar_email="user@domain.com"
        )

    assert resp["status"] == "ok"
    assert resp["account_type"] == "independent"
    assert resp["is_sub_org"] is False
    assert sub_org.billing_org_id is None

    # 2. Switch back to shared
    res_sub2 = MagicMock()
    res_sub2.scalars.return_value.first.return_value = sub_org

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute.side_effect = [res_sub2, res_master]

    with patch("routers.customers.verify_customer_access", AsyncMock()):
        resp2 = await set_billing_account_type(
            dograh_org_id=50,
            data=SetBillingAccountTypeRequest(account_type="shared"),
            db=mock_db,
            x_talkar_email="user@domain.com"
        )

    assert resp2["status"] == "ok"
    assert resp2["account_type"] == "shared"
    assert resp2["is_sub_org"] is True
    assert sub_org.billing_org_id == 1


@pytest.mark.asyncio
async def test_get_customer_status_reflects_account_type():
    """Verify that get_customer_status accurately reports account_type and billing_org_id."""
    independent_cust = Customer(
        id=10,
        contact_email="boss@biz.com",
        dograh_org_id=60,
        billing_org_id=None,
        status="active"
    )
    sub = Subscription(customer_id=10, plan="starter")

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()

    res_cust = MagicMock()
    res_cust.scalars.return_value.first.return_value = independent_cust

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute.side_effect = [res_cust, res_sub]

    with patch("services.dograh_client.get_org_config", AsyncMock(return_value=None)):
        status_resp = await get_customer_status(dograh_org_id=60, db=mock_db)

    assert status_resp["is_sub_org"] is False
    assert status_resp["account_type"] == "independent"
    assert status_resp["billing_org_id"] is None


@pytest.mark.asyncio
async def test_existing_org_updates_to_independent():
    """Verify that if an org was previously linked, calling create_customer with account_type='independent' unlinks it."""
    existing_sub = Customer(
        id=7,
        contact_email="owner@brand.com",
        dograh_org_id=70,
        dograh_user_id=500,
        billing_org_id=1,
        status="pending_approval"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    res_exist = MagicMock()
    res_exist.scalars.return_value.first.return_value = existing_sub

    mock_db.execute.return_value = res_exist

    req = CreateCustomerRequest(
        email="owner@brand.com",
        contact_name="Owner",
        dograh_org_id=70,
        dograh_user_id=500,
        account_type="independent"
    )

    cust = await create_customer(data=req, db=mock_db, authorization="Bearer change-me-in-production-billing-token")

    assert cust.id == 7
    assert cust.billing_org_id is None, "Existing linked sub-org should be unlinked to None when independent is requested"
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_set_billing_account_type_invalid_raises_400():
    """Verify that an invalid account_type raises HTTP 400."""
    customer = Customer(
        id=8,
        contact_email="owner@brand.com",
        dograh_org_id=80,
        billing_org_id=None,
        status="pending_approval"
    )

    mock_db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = customer
    mock_db.execute = AsyncMock(return_value=res)

    with patch("routers.customers.verify_customer_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await set_billing_account_type(
                dograh_org_id=80,
                data=SetBillingAccountTypeRequest(account_type="invalid_type"),
                db=mock_db,
                x_talkar_email="owner@brand.com"
            )
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_billing_account_type_mismatched_user_id_raises_403():
    """Verify that linking to a master account owned by a different dograh_user_id raises HTTP 403."""
    customer = Customer(
        id=9,
        contact_email="hijacker@brand.com",
        dograh_org_id=90,
        dograh_user_id=999,
        billing_org_id=None,
        status="pending_approval"
    )
    master = Customer(
        id=1,
        contact_email="victim@brand.com",
        dograh_org_id=1,
        dograh_user_id=111,  # Different user ID
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalars.return_value.first.return_value = customer

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_master])

    with patch("routers.customers.verify_customer_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await set_billing_account_type(
                dograh_org_id=90,
                data=SetBillingAccountTypeRequest(account_type="shared", master_customer_id=1),
                db=mock_db,
                x_talkar_email="hijacker@brand.com"
            )
        assert exc_info.value.status_code == 403

