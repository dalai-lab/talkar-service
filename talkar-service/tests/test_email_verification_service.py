import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, TalkarAdmin
from routers.notifications import send_verification_email_endpoint, SendVerificationEmailRequest
from routers.admin import verify_customer_email_override


@pytest.mark.asyncio
async def test_send_verification_email_endpoint():
    """Verify talkar-service notifications router can dispatch verification emails."""
    req = SendVerificationEmailRequest(to_email="test@example.com", otp_code="123456")
    
    with patch("services.notification_service.send_verification_email_otp", AsyncMock(return_value=True)) as mock_send:
        res = await send_verification_email_endpoint(
            data=req,
            authorization="Bearer change-me-in-production-billing-token"
        )
        assert res["success"] is True
        mock_send.assert_awaited_once_with(to_email="test@example.com", otp_code="123456")


@pytest.mark.asyncio
async def test_verify_customer_email_override():
    """Verify superadmin override marks user as verified in Dograh DB."""
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    cust = Customer(
        id=7,
        company_name="Firewall Blocked Corp",
        contact_email="client@firewall.com",
        status="pending_verification",
        dograh_user_id=88,
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cust)))
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    mock_ddb = MagicMock()
    mock_ddb.execute = AsyncMock()
    mock_ddb.commit = AsyncMock()
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb):
        res = await verify_customer_email_override(
            customer_id=7,
            db=mock_db,
            current_admin=admin
        )

    assert res["success"] is True
    assert res["status"] == "under_review"
    assert mock_ddb.execute.called
    assert mock_ddb.commit.called
    assert mock_db.commit.called
