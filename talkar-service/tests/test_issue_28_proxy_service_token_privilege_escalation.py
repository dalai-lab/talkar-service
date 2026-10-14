import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import jwt
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from db.models import Customer, Subscription, Wallet
from db.session import get_db
from main import app
from services.customer_auth import (
    is_admin_or_service_request,
    verify_customer_access,
    TALKAR_ADMIN_JWT_SECRET,
    ALGORITHM,
)


def _make_admin_token() -> str:
    return jwt.encode(
        {"sub": "1", "email": "admin@talkar.io", "is_admin": True},
        TALKAR_ADMIN_JWT_SECRET,
        algorithm=ALGORITHM,
    )


def _make_user_token(email: str = "attacker@evil.com") -> str:
    return jwt.encode(
        {"sub": "99", "email": email, "is_admin": False},
        TALKAR_ADMIN_JWT_SECRET,
        algorithm=ALGORITHM,
    )


# ---------------------------------------------------------------------------
# Unit Tests for is_admin_or_service_request
# ---------------------------------------------------------------------------

def test_is_admin_or_service_request_matrix():
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    admin_token = _make_admin_token()
    user_token = _make_user_token()

    # 1. Pure internal backend service call (no forwarded user email) -> True
    assert is_admin_or_service_request(f"Bearer {service_token}") is True
    assert is_admin_or_service_request(f"Bearer {service_token}", x_talkar_email=None) is True
    assert is_admin_or_service_request(f"Bearer {service_token}", x_talkar_email="") is True

    # 2. Proxied user session through Next.js proxy (service token + X-Talkar-Email) -> False
    # CRITICAL: Forwarded user requests must NEVER be elevated to global admin/service status!
    assert is_admin_or_service_request(f"Bearer {service_token}", x_talkar_email="attacker@evil.com") is False
    assert is_admin_or_service_request(f"Bearer {service_token}", x_talkar_email="alice@company.com") is False

    # 3. Genuine Talkar Admin JWT (is_admin: True) -> True regardless of email header
    assert is_admin_or_service_request(f"Bearer {admin_token}") is True
    assert is_admin_or_service_request(f"Bearer {admin_token}", x_talkar_email="attacker@evil.com") is True

    # 4. Regular non-admin user JWT -> False
    assert is_admin_or_service_request(f"Bearer {user_token}") is False
    assert is_admin_or_service_request(f"Bearer {user_token}", x_talkar_email="attacker@evil.com") is False

    # 5. Invalid tokens or missing auth -> False
    assert is_admin_or_service_request("Bearer invalid-secret") is False
    assert is_admin_or_service_request(None) is False
    assert is_admin_or_service_request("") is False


# ---------------------------------------------------------------------------
# Unit Tests for verify_customer_access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_customer_access_blocks_proxied_user_idor():
    """
    Ensure that a proxied user session (Bearer TALKAR_BILLING_API_TOKEN + X-Talkar-Email: attacker)
    cannot access victim customer resources.
    """
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()

    victim_customer = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="victim@company.com",
        status="active",
        billing_org_id=None,
    )

    # Attacker proxied through Next.js: service token + attacker email
    with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):
        with pytest.raises(Exception) as exc_info:
            await verify_customer_access(
                customer=victim_customer,
                db=mock_db,
                x_talkar_email="attacker@evil.com",
                authorization=f"Bearer {service_token}",
            )
        assert exc_info.value.status_code == 403
        assert "Forbidden" in exc_info.value.detail

    # Legitimate customer proxied through Next.js: service token + victim email -> Allowed
    await verify_customer_access(
        customer=victim_customer,
        db=mock_db,
        x_talkar_email="victim@company.com",
        authorization=f"Bearer {service_token}",
    )

    # Pure backend service call without user email -> Allowed
    await verify_customer_access(
        customer=victim_customer,
        db=mock_db,
        x_talkar_email=None,
        authorization=f"Bearer {service_token}",
    )

    # Talkar Admin JWT override -> Allowed
    admin_token = _make_admin_token()
    await verify_customer_access(
        customer=victim_customer,
        db=mock_db,
        x_talkar_email="attacker@evil.com",
        authorization=f"Bearer {admin_token}",
    )


# ---------------------------------------------------------------------------
# Endpoint Tests: create_customer and update_suborg_spending_limits
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_suborg_db():
    master = Customer(
        id=100,
        dograh_org_id=100,
        contact_email="master@company.com",
        contact_name="Master Admin",
        company_name="Master Corp",
        status="active",
        billing_org_id=None,
    )
    suborg = Customer(
        id=200,
        dograh_org_id=200,
        contact_email="suborg@client.com",
        contact_name="Sub Org User",
        company_name="Client Corp",
        status="active",
        billing_org_id=100,
        onboarding_form={"monthly_spending_cap_paise": 500000, "concurrent_call_limit": 5},
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.add = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from customers" in q_str:
            if "customers.id = :id_1" in q_str or "customer.id = :id_1" in q_str or "customer.id = 100" in q_str:
                res.scalar_one_or_none.return_value = master
            else:
                res.scalar_one_or_none.return_value = suborg
        return res

    mock_session.execute = mock_execute
    return mock_session


@pytest.mark.asyncio
async def test_proxied_user_cannot_call_create_customer():
    """
    POST /customers/ is reserved for backend service registration or Talkar admin.
    A user request forwarded through Next.js (service token + X-Talkar-Email) must be rejected with 401.
    """
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = None
    mock_res.scalar_one_or_none.return_value = None
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_res)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.add = MagicMock()

    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Proxied attacker request
            res = await client.post(
                "/customers/",
                json={
                    "company_name": "Rogue Corp",
                    "contact_name": "Rogue",
                    "email": "attacker@evil.com",
                    "account_type": "independent",
                    "dograh_org_id": 999,
                    "dograh_user_id": 999,
                },
                headers={
                    "Authorization": f"Bearer {service_token}",
                    "X-Talkar-Email": "attacker@evil.com",
                },
            )
            assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"

            # Backend service without X-Talkar-Email is permitted
            with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)):
                res_service = await client.post(
                    "/customers/",
                    json={
                        "company_name": "Valid Corp",
                        "contact_name": "Valid",
                        "email": "valid@company.com",
                        "account_type": "independent",
                        "dograh_org_id": 1001,
                        "dograh_user_id": 1001,
                    },
                    headers={
                        "Authorization": f"Bearer {service_token}",
                    },
                )
                assert res_service.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_suborg_spending_limits_blocks_proxied_attacker(mock_suborg_db):
    """
    PATCH /customers/by-org/{dograh_org_id}/spending-limits
    An attacker sending service token + attacker email must be blocked with HTTP 403 Forbidden.
    Only the master org owner (or admin/service) can update sub-org limits.
    """
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    app.dependency_overrides[get_db] = lambda: mock_suborg_db

    try:
        with patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # 1. Attacker proxied request -> 403 Forbidden
                res = await client.patch(
                    "/customers/by-org/200/spending-limits",
                    json={"monthly_spending_cap_paise": 100000000, "concurrent_call_limit": 50},
                    headers={
                        "Authorization": f"Bearer {service_token}",
                        "X-Talkar-Email": "attacker@evil.com",
                    },
                )
                assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
                assert "Master Organization owner" in res.json().get("detail", "")

                # 2. Master organization owner proxied request -> 200 OK
                res_master = await client.patch(
                    "/customers/by-org/200/spending-limits",
                    json={"monthly_spending_cap_paise": 800000, "concurrent_call_limit": 8},
                    headers={
                        "Authorization": f"Bearer {service_token}",
                        "X-Talkar-Email": "master@company.com",
                    },
                )
                assert res_master.status_code == 200

                # 3. Talkar Admin JWT -> 200 OK
                admin_token = _make_admin_token()
                res_admin = await client.patch(
                    "/customers/by-org/200/spending-limits",
                    json={"monthly_spending_cap_paise": 900000, "concurrent_call_limit": 10},
                    headers={
                        "Authorization": f"Bearer {admin_token}",
                        "X-Talkar-Email": "attacker@evil.com",
                    },
                )
                assert res_admin.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
