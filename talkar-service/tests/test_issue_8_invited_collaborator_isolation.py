import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer
from routers.customers import (
    create_customer,
    CreateCustomerRequest,
    set_billing_account_type,
    SetBillingAccountTypeRequest,
    create_new_agent_request,
)


@pytest.mark.asyncio
async def test_invited_collaborator_registration_defaults_to_independent_billing():
    """
    Issue 8 Core Test:
    When dev@agency.com (dograh_user_id=10) has an established agency master account (Customer ID 1),
    and is invited as a member/collaborator to an external client workspace (dograh_org_id=999),
    Talkar MUST NOT link Org 999 to Customer 1.
    It must create an independent customer record (billing_org_id=None).
    """
    agency_master = Customer(
        id=1,
        contact_email="dev@agency.com",
        contact_name="Agency Dev",
        company_name="Top Agency LLC",
        dograh_org_id=100,
        dograh_user_id=10,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    # 1. Check existing org: None
    res_existing_org = MagicMock()
    res_existing_org.scalars.return_value.first.return_value = None
    res_existing_org.scalar_one_or_none.return_value = None

    # 2. Check spam rate limit: None
    res_spam = MagicMock()
    res_spam.scalar_one_or_none.return_value = None

    # 3. Find existing_email: agency_master
    res_existing_email = MagicMock()
    res_existing_email.scalar_one_or_none.return_value = agency_master

    mock_db.execute.side_effect = [res_existing_org, res_spam, res_existing_email]

    req = CreateCustomerRequest(
        email="dev@agency.com",
        contact_name="Agency Dev",
        company_name="External Client Workspace",
        dograh_org_id=999,
        dograh_user_id=10,
    )

    # dev@agency.com is a member (require_owner=False -> True), but NOT owner (require_owner=True -> False)
    async def mock_verify(org_id, user_id=None, email=None, require_owner=False):
        if require_owner:
            return False  # dev is just an invited member/collaborator
        return True  # dev is a member

    with patch("services.dograh_client.verify_dograh_org_user", side_effect=mock_verify):
        new_cust = await create_customer(
            data=req,
            db=mock_db,
            authorization="Bearer change-me-in-production-billing-token"
        )

    assert new_cust.dograh_org_id == 999
    assert new_cust.billing_org_id is None, "Invited collaborator workspace must NOT hijack agency wallet (billing_org_id must be None)"
    assert new_cust.contact_email == "dev@agency.com"


@pytest.mark.asyncio
async def test_invited_collaborator_explicit_shared_billing_rejected():
    """
    If an invited collaborator tries to explicitly pass account_type='shared'
    to link a client workspace to their agency wallet, it must be rejected with HTTP 403.
    """
    agency_master = Customer(
        id=1,
        contact_email="dev@agency.com",
        contact_name="Agency Dev",
        company_name="Top Agency LLC",
        dograh_org_id=100,
        dograh_user_id=10,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    res_existing_org = MagicMock()
    res_existing_org.scalars.return_value.first.return_value = None
    res_existing_org.scalar_one_or_none.return_value = None

    res_spam = MagicMock()
    res_spam.scalar_one_or_none.return_value = None

    res_existing_email = MagicMock()
    res_existing_email.scalar_one_or_none.return_value = agency_master

    mock_db.execute.side_effect = [res_existing_org, res_spam, res_existing_email]

    req = CreateCustomerRequest(
        email="dev@agency.com",
        contact_name="Agency Dev",
        company_name="External Client Workspace",
        dograh_org_id=999,
        dograh_user_id=10,
        account_type="shared"
    )

    async def mock_verify(org_id, user_id=None, email=None, require_owner=False):
        if require_owner:
            return False
        return True

    with patch("services.dograh_client.verify_dograh_org_user", side_effect=mock_verify):
        with pytest.raises(HTTPException) as exc_info:
            await create_customer(
                data=req,
                db=mock_db,
                authorization="Bearer change-me-in-production-billing-token"
            )

    assert exc_info.value.status_code == 403
    assert "Only organization owners/admins can link an organization to a shared billing wallet" in exc_info.value.detail


@pytest.mark.asyncio
async def test_workspace_owner_allowed_to_link_shared_billing():
    """
    When the agency owner creates their OWN new branch workspace (dograh_org_id=101)
    and is verified as the owner/admin, shared billing linkage to agency master is permitted.
    """
    agency_master = Customer(
        id=1,
        contact_email="owner@agency.com",
        contact_name="Agency Owner",
        company_name="Top Agency LLC",
        dograh_org_id=100,
        dograh_user_id=10,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    res_existing_org = MagicMock()
    res_existing_org.scalars.return_value.first.return_value = None
    res_existing_org.scalar_one_or_none.return_value = None

    res_spam = MagicMock()
    res_spam.scalar_one_or_none.return_value = None

    res_existing_email = MagicMock()
    res_existing_email.scalar_one_or_none.return_value = agency_master

    mock_db.execute.side_effect = [res_existing_org, res_spam, res_existing_email]

    req = CreateCustomerRequest(
        email="owner@agency.com",
        contact_name="Agency Owner",
        company_name="Agency Branch 2",
        dograh_org_id=101,
        dograh_user_id=10,
    )

    # User is both member and owner
    async def mock_verify(org_id, user_id=None, email=None, require_owner=False):
        return True

    with patch("services.dograh_client.verify_dograh_org_user", side_effect=mock_verify):
        new_cust = await create_customer(
            data=req,
            db=mock_db,
            authorization="Bearer change-me-in-production-billing-token"
        )

    assert new_cust.dograh_org_id == 101
    assert new_cust.billing_org_id == 1, "Verified owner workspace should link to master billing account"


@pytest.mark.asyncio
async def test_set_billing_account_type_rejects_collaborator_linking_shared():
    """
    POST /customers/by-org/{dograh_org_id}/billing-account-type must reject
    collaborators who are not owners/admins when attempting to set account_type='shared'.
    """
    client_cust = Customer(
        id=2,
        contact_email="dev@agency.com",
        contact_name="Agency Dev",
        company_name="External Client Workspace",
        dograh_org_id=999,
        dograh_user_id=10,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    res_cust = MagicMock()
    res_cust.scalars.return_value.first.return_value = client_cust
    mock_db.execute.return_value = res_cust

    req = SetBillingAccountTypeRequest(account_type="shared")

    # verify_dograh_org_user returns False when require_owner=True
    async def mock_verify(org_id, user_id=None, email=None, require_owner=False):
        if require_owner:
            return False
        return True

    with patch("services.dograh_client.verify_dograh_org_user", side_effect=mock_verify), \
         patch("routers.customers.verify_customer_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await set_billing_account_type(
                dograh_org_id=999,
                data=req,
                db=mock_db,
                x_talkar_email="dev@agency.com"
            )

    assert exc_info.value.status_code == 403
    assert "Only organization owners/admins can link an organization to a shared billing wallet" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_new_agent_request_rejects_non_owner():
    """
    POST /customers/by-org/{dograh_org_id}/new-agent-request must reject requests
    where the master customer is not an owner/admin of the target dograh_org_id.
    """
    agency_master = Customer(
        id=1,
        contact_email="dev@agency.com",
        contact_name="Agency Dev",
        company_name="Agency LLC",
        dograh_org_id=100,
        dograh_user_id=10,
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = agency_master

    res_existing = MagicMock()
    res_existing.scalar_one_or_none.return_value = None

    mock_db.execute.side_effect = [res_master, res_existing]

    async def mock_verify(org_id, user_id=None, email=None, require_owner=False):
        if require_owner:
            return False  # Not an owner
        return True

    with patch("services.dograh_client.verify_dograh_org_user", side_effect=mock_verify):
        with pytest.raises(HTTPException) as exc_info:
            await create_new_agent_request(
                dograh_org_id=999,
                data={"master_customer_id": 1},
                db=mock_db,
                x_talkar_email="dev@agency.com",
                authorization="Bearer change-me-in-production-billing-token"
            )

    assert exc_info.value.status_code == 403
    assert "Master customer is not an owner/admin of this organization" in exc_info.value.detail
