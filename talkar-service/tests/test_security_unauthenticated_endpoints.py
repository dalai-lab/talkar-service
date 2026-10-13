import sys
import jwt
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from db.models import Customer, Wallet, TalkarAdmin
from services.customer_auth import verify_customer_access, resolve_authenticated_email
from routers.customers import get_all_customers, get_customer
from routers.wallet import get_wallet


@pytest.mark.asyncio
async def test_raw_x_talkar_email_without_auth_is_rejected():
    """Verify that raw unverified X-Talkar-Email without a valid Authorization token is rejected with 401."""
    customer = Customer(id=1, contact_email="alice@company.com", billing_org_id=None, dograh_org_id=10)
    mock_db = MagicMock()

    # Attacker passes X-Talkar-Email: alice@company.com without an Authorization token
    with pytest.raises(HTTPException) as exc_info:
        await verify_customer_access(
            customer=customer,
            db=mock_db,
            x_talkar_email="alice@company.com",
            authorization=None
        )
    assert exc_info.value.status_code == 401
    assert "Authentication required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_forged_x_talkar_email_with_invalid_token_is_rejected():
    """Verify that an invalid or forged Bearer token is rejected with 401."""
    customer = Customer(id=1, contact_email="alice@company.com", billing_org_id=None, dograh_org_id=10)
    mock_db = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await verify_customer_access(
            customer=customer,
            db=mock_db,
            x_talkar_email="alice@company.com",
            authorization="Bearer invalid-garbage-token"
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_service_token_authenticates_customer():
    """Verify that requests forwarded by trusted server proxy with service token are allowed for matching customer."""
    customer = Customer(id=1, contact_email="alice@company.com", billing_org_id=None, dograh_org_id=10)
    mock_db = MagicMock()
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "")

    # Authorized access via service token
    await verify_customer_access(
        customer=customer,
        db=mock_db,
        x_talkar_email="alice@company.com",
        authorization=f"Bearer {service_token}"
    )


@pytest.mark.asyncio
async def test_valid_service_token_rejects_idor_mismatched_customer():
    """Verify that even with service token, an attacker cannot access a different customer's account (IDOR)."""
    customer = Customer(id=1, contact_email="victim@company.com", billing_org_id=None, dograh_org_id=10)
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_res
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "")

    with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc_info:
            await verify_customer_access(
                customer=customer,
                db=mock_db,
                x_talkar_email="attacker@evil.com",
                authorization=f"Bearer {service_token}"
            )
        assert exc_info.value.status_code == 403
        assert "Forbidden" in exc_info.value.detail


@pytest.mark.asyncio
async def test_admin_jwt_grants_access():
    """Verify that a valid admin JWT grants full access."""
    customer = Customer(id=1, contact_email="alice@company.com", billing_org_id=None, dograh_org_id=10)
    mock_db = MagicMock()

    admin_token = jwt.encode(
        {"sub": "admin@talkar.in", "is_admin": True},
        settings.TALKAR_ADMIN_JWT_SECRET,
        algorithm="HS256"
    )

    await verify_customer_access(
        customer=customer,
        db=mock_db,
        x_talkar_email="alice@company.com",
        authorization=f"Bearer {admin_token}"
    )


@pytest.mark.asyncio
async def test_get_all_customers_requires_admin_dependency():
    """Verify that get_all_customers requires current_admin."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_res

    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    res = await get_all_customers(page=1, limit=50, db=mock_db, current_admin=admin)
    assert res == []


@pytest.mark.asyncio
async def test_get_customer_requires_admin_dependency():
    """Verify that get_customer requires current_admin."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    customer = Customer(id=1, contact_email="alice@company.com")
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = customer
    mock_db.execute.return_value = mock_res

    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    res = await get_customer(customer_id=1, db=mock_db, current_admin=admin)
    assert res.id == 1


@pytest.mark.asyncio
async def test_get_wallet_requires_admin_dependency():
    """Verify that get_wallet requires current_admin."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    wallet = Wallet(customer_id=1, balance_paise=50000, auto_recharge_enabled=False)
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = wallet
    mock_db.execute.return_value = mock_res

    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    res = await get_wallet(customer_id=1, db=mock_db, current_admin=admin)
    assert res["balance_paise"] == 50000


@pytest.mark.asyncio
async def test_http_get_customers_unauthenticated_returns_401():
    from main import app
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/customers/")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_http_get_customer_by_id_unauthenticated_returns_401():
    from main import app
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/customers/1")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_http_get_wallet_by_id_unauthenticated_returns_401():
    from main import app
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/wallet/1")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_http_post_customers_unauthenticated_returns_401():
    from main import app
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/customers/",
            json={"email": "attacker@evil.com", "contact_name": "Attacker", "dograh_org_id": 99, "dograh_user_id": 1}
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_support_requests_rejects_unverified_x_talkar_email():
    """Verify that calling support-requests with unverified X-Talkar-Email header returns 401."""
    from routers.customers import get_support_requests, create_support_request, SupportRequestPayload
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    customer = Customer(id=1, dograh_org_id=10, contact_email="victim@company.com")
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = customer
    mock_db.execute.return_value = mock_res

    # Attacker passes X-Talkar-Email without Bearer Authorization
    with pytest.raises(HTTPException) as exc_info:
        await get_support_requests(
            dograh_org_id=10,
            db=mock_db,
            x_talkar_email="victim@company.com",
            authorization=None
        )
    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        await create_support_request(
            data=SupportRequestPayload(type="bug", subject="Hack", description="Exploit"),
            dograh_org_id=10,
            db=mock_db,
            x_talkar_email="victim@company.com",
            authorization=None
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_notifications_rejects_unverified_x_talkar_email():
    """Verify that calling notifications endpoints with unverified X-Talkar-Email returns 401."""
    from routers.notifications import get_notifications, mark_notification_read
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    customer = Customer(id=1, dograh_org_id=10, contact_email="victim@company.com")
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = customer
    mock_db.execute.return_value = mock_res

    with pytest.raises(HTTPException) as exc_info:
        await get_notifications(
            dograh_org_id=10,
            limit=20,
            db=mock_db,
            x_talkar_email="victim@company.com",
            authorization=None
        )
    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        await mark_notification_read(
            notification_id=1,
            dograh_org_id=10,
            db=mock_db,
            x_talkar_email="victim@company.com",
            authorization=None
        )
    assert exc_info.value.status_code == 401



