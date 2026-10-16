import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from db.models import Customer, Subscription, Wallet
from db.session import get_db
from main import app


@pytest.fixture
def mock_db_with_customer():
    """Provides a mock database session with a registered customer for org 10."""
    customer = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="alice@company.com",
        contact_name="Alice Smith",
        company_name="Acme Corp",
        status="active",
        billing_org_id=None,
        report_settings={"low_balance_alert_paise": 150000, "cc_email": None},
        onboarding_form={"approved_tier": "starter"}
    )
    sub = Subscription(
        id=1,
        customer_id=1,
        plan="starter",
        per_minute_rate_paise=600,
        status="active"
    )
    wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=500000,
        auto_recharge_enabled=False
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from customers" in q_str:
            res.scalar_one_or_none.return_value = customer
            res.scalars.return_value.all.return_value = [customer]
            res.scalars.return_value.first.return_value = customer
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = sub
            res.scalars.return_value.all.return_value = [sub]
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
            res.scalars.return_value.all.return_value = [wallet]
        elif "count" in q_str:
            res.scalar.return_value = 0
        elif "from wallet_transactions" in q_str:
            res.scalars.return_value.all.return_value = []
        elif "from call_logs" in q_str:
            res.scalars.return_value.all.return_value = []
        elif "from agents" in q_str:
            res.scalars.return_value.all.return_value = []
        elif "from phone_number_requests" in q_str:
            res.scalar_one_or_none.return_value = None
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_session.execute = AsyncMock(side_effect=mock_execute)

    async def _get_db_override():
        yield mock_session

    app.dependency_overrides[get_db] = _get_db_override
    yield mock_session
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/billing/transactions/by-org/10", None),
    ("GET", "/billing/usage/by-org/10", None),
    ("GET", "/billing/subscription/by-org/10", None),
    ("POST", "/billing/upgrade/create-order", {"dograh_org_id": 10, "requested_tier": "pro"}),
    ("POST", "/customers/by-org/10/select-tier", {"tier": "pro"}),
    ("POST", "/customers/by-org/10/request-tier-upgrade", {"requested_tier": "pro"}),
    ("PATCH", "/customers/by-org/10/voice", {"voice_id": "aura-2-thalia-en", "provider": "deepgram"}),
    ("PATCH", "/customers/by-org/10/profile", {"company_name": "New Corp", "contact_name": "New Name"}),
    ("GET", "/customers/by-org/10/notification-settings", None),
    ("PATCH", "/customers/by-org/10/notification-settings", {"low_balance_alert_paise": 200000}),
    ("POST", "/customers/by-org/10/request-phone-numbers", {"quantity": 1, "region": "US", "use_case": "Support"}),
    ("GET", "/customers/by-org/10", None),
    ("GET", "/customers/by-org/10/agents", None),
    ("GET", "/customers/by-org/10/crm-links", None),
    ("POST", "/customers/by-org/10/new-agent-brief", {"form": {"prompt": "test"}}),
    ("POST", "/customers/by-org/10/onboarding", {"form": {"businessName": "Acme"}}),
    ("POST", "/customers/1/onboarding", {"form": {"businessName": "Acme"}}),
    ("GET", "/customers/status?dograh_org_id=10", None),
    ("GET", "/customers/by-email/alice@company.com", None),
])
async def test_endpoint_unauthenticated_request_rejected_with_401(mock_db_with_customer, method, path, payload):
    """
    Issue 27 & Fix 10: Verify that anonymous requests without Authorization header
    to all critical billing and customer management routes are rejected with HTTP 401.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        if method == "GET":
            res = await client.get(path)
        elif method == "POST":
            res = await client.post(path, json=payload)
        elif method == "PATCH":
            res = await client.patch(path, json=payload)
        
        assert res.status_code == 401, f"{method} {path} returned {res.status_code}: {res.text}"
        data = res.json()
        assert "Authentication required" in data.get("detail", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/billing/transactions/by-org/10", None),
    ("GET", "/billing/usage/by-org/10", None),
    ("GET", "/billing/subscription/by-org/10", None),
    ("POST", "/billing/upgrade/create-order", {"dograh_org_id": 10, "requested_tier": "pro"}),
    ("POST", "/customers/by-org/10/select-tier", {"tier": "pro"}),
    ("POST", "/customers/by-org/10/request-tier-upgrade", {"requested_tier": "pro"}),
    ("PATCH", "/customers/by-org/10/voice", {"voice_id": "aura-2-thalia-en", "provider": "deepgram"}),
    ("PATCH", "/customers/by-org/10/profile", {"company_name": "New Corp", "contact_name": "New Name"}),
    ("GET", "/customers/by-org/10/notification-settings", None),
    ("PATCH", "/customers/by-org/10/notification-settings", {"low_balance_alert_paise": 200000}),
    ("POST", "/customers/by-org/10/request-phone-numbers", {"quantity": 1, "region": "US", "use_case": "Support"}),
    ("GET", "/customers/by-org/10", None),
    ("GET", "/customers/by-org/10/agents", None),
    ("GET", "/customers/by-org/10/crm-links", None),
    ("POST", "/customers/by-org/10/new-agent-brief", {"form": {"prompt": "test"}}),
    ("POST", "/customers/by-org/10/onboarding", {"form": {"businessName": "Acme"}}),
    ("POST", "/customers/1/onboarding", {"form": {"businessName": "Acme"}}),
    ("GET", "/customers/status?dograh_org_id=10", None),
    ("GET", "/customers/by-email/alice@company.com", None),
])
async def test_endpoint_forged_bearer_token_rejected_with_401(mock_db_with_customer, method, path, payload):
    """
    Issue 27 & Fix 10: Verify that requests with invalid/forged Bearer tokens are rejected with HTTP 401.
    """
    headers = {
        "Authorization": "Bearer invalid-garbage-token-12345",
        "X-Talkar-Email": "alice@company.com"
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        if method == "GET":
            res = await client.get(path, headers=headers)
        elif method == "POST":
            res = await client.post(path, json=payload, headers=headers)
        elif method == "PATCH":
            res = await client.patch(path, json=payload, headers=headers)

        assert res.status_code == 401, f"{method} {path} returned {res.status_code}: {res.text}"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/billing/transactions/by-org/10", None),
    ("GET", "/billing/usage/by-org/10", None),
    ("GET", "/billing/subscription/by-org/10", None),
    ("POST", "/billing/upgrade/create-order", {"dograh_org_id": 10, "requested_tier": "pro"}),
    ("POST", "/customers/by-org/10/select-tier", {"tier": "pro"}),
    ("POST", "/customers/by-org/10/request-tier-upgrade", {"requested_tier": "pro"}),
    ("PATCH", "/customers/by-org/10/voice", {"voice_id": "aura-2-thalia-en", "provider": "deepgram"}),
    ("PATCH", "/customers/by-org/10/profile", {"company_name": "New Corp", "contact_name": "New Name"}),
    ("GET", "/customers/by-org/10/notification-settings", None),
    ("PATCH", "/customers/by-org/10/notification-settings", {"low_balance_alert_paise": 200000}),
    ("POST", "/customers/by-org/10/request-phone-numbers", {"quantity": 1, "region": "US", "use_case": "Support"}),
    ("GET", "/customers/by-org/10", None),
    ("GET", "/customers/by-org/10/agents", None),
    ("GET", "/customers/by-org/10/crm-links", None),
    ("POST", "/customers/by-org/10/new-agent-brief", {"form": {"prompt": "test"}}),
    ("POST", "/customers/by-org/10/onboarding", {"form": {"businessName": "Acme"}}),
    ("POST", "/customers/1/onboarding", {"form": {"businessName": "Acme"}}),
    ("GET", "/customers/status?dograh_org_id=10", None),
    ("GET", "/customers/by-email/alice@company.com", None),
])
async def test_endpoint_cross_org_idor_rejected_with_403(mock_db_with_customer, method, path, payload):
    """
    Issue 27: Verify that authenticated callers attempting to access a different organization's
    resources (IDOR) are rejected with HTTP 403 Forbidden.
    """
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    headers = {
        "Authorization": f"Bearer {service_token}",
        "X-Talkar-Email": "attacker@evil.com"
    }
    with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            if method == "GET":
                res = await client.get(path, headers=headers)
            elif method == "POST":
                res = await client.post(path, json=payload, headers=headers)
            elif method == "PATCH":
                res = await client.patch(path, json=payload, headers=headers)

            assert res.status_code == 403, f"{method} {path} returned {res.status_code}: {res.text}"
            data = res.json()
            assert "Forbidden" in data.get("detail", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/billing/transactions/by-org/10", None),
    ("GET", "/billing/usage/by-org/10", None),
    ("GET", "/billing/subscription/by-org/10", None),
    ("GET", "/customers/by-org/10/notification-settings", None),
    ("GET", "/customers/by-org/10", None),
    ("GET", "/customers/by-org/10/agents", None),
    ("GET", "/customers/by-org/10/crm-links", None),
])
async def test_endpoint_authorized_caller_allowed(mock_db_with_customer, method, path, payload):
    """
    Issue 27: Verify that legitimate callers authenticated through the Next.js server proxy
    (service token + matching X-Talkar-Email) are granted access with HTTP 200.
    """
    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    headers = {
        "Authorization": f"Bearer {service_token}",
        "X-Talkar-Email": "alice@company.com"
    }
    with patch("services.dograh_client.get_org_workflows", AsyncMock(return_value=[])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get(path, headers=headers)
            assert res.status_code == 200, f"{method} {path} returned {res.status_code}: {res.text}"
