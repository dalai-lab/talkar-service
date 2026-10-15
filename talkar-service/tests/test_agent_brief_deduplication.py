import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from db.models import Customer, Subscription
from routers.customers import save_agent_brief


@pytest.mark.asyncio
async def test_save_agent_brief_not_found():
    """Verify that calling new-agent-brief for a non-existent org returns 404."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_res

    with pytest.raises(HTTPException) as exc_info:
        await save_agent_brief(dograh_org_id=999, data={"form": {}}, db=mock_db)
    assert exc_info.value.status_code == 404
    assert "No customer record found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_save_agent_brief_returning_customer_with_active_master():
    """Verify that submitting brief for a sub-org of an active customer sends returning customer email and preserves documents."""
    sub_customer = Customer(
        id=2,
        dograh_org_id=123,
        contact_email="team@acme.com",
        company_name="Acme Support Agent",
        billing_org_id=1,
        onboarding_form={"approved_tier": "pro"},
        status="agent_building"
    )
    master_customer = Customer(
        id=1,
        contact_email="owner@acme.com",
        company_name="Acme Corp",
        status="active"
    )
    master_sub = Subscription(
        customer_id=1,
        plan="pro",
        status="active"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    res_sub_cust = MagicMock()
    res_sub_cust.scalar_one_or_none.return_value = sub_customer

    res_master_cust = MagicMock()
    res_master_cust.scalar_one_or_none.return_value = master_customer

    res_master_sub = MagicMock()
    res_master_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute.side_effect = [res_sub_cust, res_master_cust, res_master_sub]

    data = {
        "form": {"agent_role": "Customer Support", "primary_language": "English"},
        "documents": ["https://s3.amazonaws.com/doc1.pdf"]
    }

    with patch("services.notification_service.send_email", AsyncMock()) as mock_email:
        result = await save_agent_brief(dograh_org_id=123, data=data, db=mock_db)

    assert result.status == "agent_building"  # Blocker 16: preserved
    assert result.documents == ["https://s3.amazonaws.com/doc1.pdf"]
    # Preserves approved_tier while updating new form fields
    assert result.onboarding_form["approved_tier"] == "pro"
    assert result.onboarding_form["agent_role"] == "Customer Support"

    mock_email.assert_awaited_once()
    email_kwargs = mock_email.await_args[1]
    assert email_kwargs["to_email"] == settings.ADMIN_EMAIL
    assert "New Agent Brief (Returning Customer)" in email_kwargs["subject"]
    assert "Acme Corp" in email_kwargs["subject"]
    assert "Tier: pro" in email_kwargs["body"]
    assert "Integration Fee to 0" in email_kwargs["body"]


@pytest.mark.asyncio
async def test_save_agent_brief_standalone_customer():
    """Verify that submitting brief for a standalone workspace sends the standard admin notification."""
    customer = Customer(
        id=5,
        dograh_org_id=456,
        contact_email="dev@startup.io",
        company_name="Startup IO",
        billing_org_id=None,
        onboarding_form={},
        status="pending_approval"
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute.return_value = res_cust

    data = {
        "form": {"agent_role": "Inbound Receptionist"},
        "documents": []
    }

    with patch("services.notification_service.send_email", AsyncMock()) as mock_email:
        result = await save_agent_brief(dograh_org_id=456, data=data, db=mock_db)

    assert result.status == "under_review"
    mock_email.assert_awaited_once()
    email_kwargs = mock_email.await_args[1]
    assert "New Agent Brief Submitted: Startup IO" in email_kwargs["subject"]
    assert "Inbound Receptionist" in str(email_kwargs["body"])
