import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer
from routers.customers import create_customer, CreateCustomerRequest


@pytest.mark.asyncio
async def test_fix_22_authenticated_workspace_linking_bypasses_60s_rate_limit():
    """
    Test Fix 22 (Issue 20):
    When a customer was created within the last 60 seconds (spam_record exists),
    an existing authenticated user creating a shared/linked workspace under their
    established master is EXCLUDED from the 429 block.
    """
    master = Customer(
        id=1,
        contact_email="owner@brand.com",
        contact_name="Owner",
        company_name="HQ Location",
        dograh_org_id=10,
        dograh_user_id=100,
        billing_org_id=None,
        status="active"
    )

    recent_spam_customer = Customer(
        id=2,
        contact_email="owner@brand.com",
        dograh_org_id=10,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    # Query 1: lookup customer by dograh_org_id=20 -> None
    res_org = MagicMock()
    res_org.scalars.return_value.first.return_value = None
    res_org.scalar_one_or_none.return_value = None

    # Query 2: recent_spam check -> returns recent_spam_customer (created within 1 min)
    res_spam = MagicMock()
    res_spam.scalar_one_or_none.return_value = recent_spam_customer

    # Query 3: master_check for established master -> returns master
    res_master_check = MagicMock()
    res_master_check.scalar_one_or_none.return_value = master

    # Query 4: existing_email lookup in linking logic -> returns master
    res_existing_email = MagicMock()
    res_existing_email.scalar_one_or_none.return_value = master

    mock_db.execute.side_effect = [res_org, res_spam, res_master_check, res_existing_email]

    req = CreateCustomerRequest(
        email="owner@brand.com",
        contact_name="Owner",
        company_name="Branch Location",
        dograh_org_id=20,
        dograh_user_id=100,
        account_type="shared"
    )

    with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)):
        cust = await create_customer(
            data=req,
            db=mock_db,
            authorization="Bearer change-me-in-production-billing-token"
        )

    assert cust.dograh_org_id == 20
    assert cust.billing_org_id == 1, "Linked workspace must inherit master's billing_org_id"


@pytest.mark.asyncio
async def test_fix_22_independent_creation_enforces_60s_rate_limit():
    """
    When a customer was created within the last 60 seconds and attempts
    to create another independent workspace, HTTP 429 is raised.
    """
    recent_spam_customer = Customer(
        id=2,
        contact_email="spammer@domain.com",
        dograh_org_id=10,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    res_org = MagicMock()
    res_org.scalars.return_value.first.return_value = None
    res_org.scalar_one_or_none.return_value = None

    res_spam = MagicMock()
    res_spam.scalar_one_or_none.return_value = recent_spam_customer

    mock_db.execute.side_effect = [res_org, res_spam]

    req = CreateCustomerRequest(
        email="spammer@domain.com",
        contact_name="Spammer",
        company_name="Independent Spammer",
        dograh_org_id=30,
        dograh_user_id=200,
        account_type="independent"
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_customer(
            data=req,
            db=mock_db,
            authorization="Bearer change-me-in-production-billing-token"
        )

    assert exc_info.value.status_code == 429
    assert "Too many requests" in exc_info.value.detail
