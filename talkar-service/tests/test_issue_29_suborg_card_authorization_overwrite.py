import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from db.models import Customer, Wallet
from db.session import get_db
from main import app


@pytest.fixture
def mock_multi_org_db():
    master = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="master@agency.com",
        contact_name="Agency Master",
        company_name="Master Agency LLC",
        status="active",
        billing_org_id=None,
    )
    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        contact_email="client@suborg.com",
        contact_name="Sub Org Client",
        company_name="Client Corp",
        status="active",
        billing_org_id=1,
    )
    master_wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=500000,
        razorpay_customer_id="cust_master_agency",
        razorpay_payment_method_id="token_master_corporate_card",
        auto_recharge_enabled=True,
        auto_recharge_threshold_paise=150000,
        auto_recharge_amount_paise=500000,
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.add = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from customers" in q_str:
            if "dograh_org_id = 20" in q_str or "dograh_org_id = :dograh_org_id_1" in q_str or ":dograh_org_id" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            elif "dograh_org_id = 10" in q_str:
                res.scalar_one_or_none.return_value = master
            elif "id = 1" in q_str or "customer.id = :id_1" in q_str or "customers.id = :id_1" in q_str:
                res.scalar_one_or_none.return_value = master
            else:
                res.scalar_one_or_none.return_value = sub_org
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = master_wallet
            res.scalars.return_value.first.return_value = master_wallet
        return res

    mock_session.execute = mock_execute
    return mock_session, master, sub_org, master_wallet


# ---------------------------------------------------------------------------
# Test Suite for Issue 29: Sub-Org Card Authorization Protection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suborg_card_endpoints_rejected_with_403(mock_multi_org_db):
    """
    Verify Issue 29: Any attempt by a sub-organization to call card authorization,
    tokenization, or mandate endpoints is rejected with HTTP 403 Forbidden,
    preventing overwriting of the master agency's credit card.
    """
    mock_session, master, sub_org, master_wallet = mock_multi_org_db
    app.dependency_overrides[get_db] = lambda: mock_session

    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    headers = {
        "Authorization": f"Bearer {service_token}",
        "X-Talkar-Email": "client@suborg.com",
    }

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. /billing/wallet/add-card/session
            res = await client.post(
                "/billing/wallet/add-card/session",
                json={"dograh_org_id": 20},
                headers=headers,
            )
            assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
            assert "Payment cards can only be managed by the Master Organization owner" in res.json().get("detail", "")

            # 2. /billing/confirm-add-card
            res = await client.post(
                "/billing/confirm-add-card",
                json={
                    "dograh_org_id": 20,
                    "razorpay_payment_id": "pay_sub_attempt",
                    "razorpay_order_id": "order_sub_attempt",
                    "razorpay_signature": "sig_sub",
                },
                headers=headers,
            )
            assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
            assert "Payment cards can only be managed by the Master Organization owner" in res.json().get("detail", "")

            # 3. /billing/save-card
            res = await client.post(
                "/billing/save-card",
                json={
                    "dograh_org_id": 20,
                    "razorpay_payment_method_id": "token_sub_card",
                },
                headers=headers,
            )
            assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"

            # 4. /billing/wallet/remove-card/by-org/20
            res = await client.post(
                "/billing/wallet/remove-card/by-org/20",
                headers=headers,
            )
            assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"

            # 5. /billing/razorpay-customer/create
            res = await client.post(
                "/billing/razorpay-customer/create",
                json={
                    "dograh_org_id": 20,
                    "name": "Sub Client",
                    "email": "client@suborg.com",
                },
                headers=headers,
            )
            assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"

            # 6. /billing/wallet/auto-recharge/by-org/20
            res = await client.patch(
                "/billing/wallet/auto-recharge/by-org/20",
                json={
                    "enabled": True,
                    "threshold_paise": 200000,
                    "amount_paise": 1000000,
                },
                headers=headers,
            )
            assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
            assert "Auto-recharge settings can only be managed by the Master Organization owner" in res.json().get("detail", "")

            # Master corporate card MUST remain completely untouched
            assert master_wallet.razorpay_payment_method_id == "token_master_corporate_card"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_suborg_user_targeting_master_org_rejected_by_require_owner():
    """
    Verify Issue 29: If a sub-org user tries to forge a card registration request
    specifying the Master Organization's dograh_org_id, require_owner=True blocks
    them with HTTP 403 Forbidden.
    """
    master = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="master@agency.com",
        contact_name="Agency Master",
        company_name="Master Agency LLC",
        status="active",
        billing_org_id=None,
    )
    master_wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=500000,
        razorpay_customer_id="cust_master_agency",
        razorpay_payment_method_id="token_master_corporate_card",
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        res = MagicMock()
        q_str = str(query).lower()
        if "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = master_wallet
        return res

    mock_session.execute = mock_execute
    app.dependency_overrides[get_db] = lambda: mock_session

    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    # Sub-org user attempting to register their personal card under master's org 10
    headers = {
        "Authorization": f"Bearer {service_token}",
        "X-Talkar-Email": "client@suborg.com",
    }

    try:
        # Sub-org user is NOT the owner in Dograh org 10
        with patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=False)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.post(
                    "/billing/wallet/add-card/session",
                    json={"dograh_org_id": 10},
                    headers=headers,
                )
                assert res.status_code == 403
                assert "Forbidden" in res.json().get("detail", "")

                res_confirm = await client.post(
                    "/billing/confirm-add-card",
                    json={
                        "dograh_org_id": 10,
                        "razorpay_payment_id": "pay_rogue",
                        "razorpay_order_id": "order_reg",
                    },
                    headers=headers,
                )
                assert res_confirm.status_code == 403
                assert "Forbidden" in res_confirm.json().get("detail", "")

                # Master card remains unchanged
                assert master_wallet.razorpay_payment_method_id == "token_master_corporate_card"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_confirm_add_card_enforces_order_and_customer_binding():
    """
    Verify Issue 29: confirm_add_card validates that the payment returned from Razorpay
    matches both the add-card order session and the wallet's customer_id.
    """
    master = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="master@agency.com",
        status="active",
        billing_org_id=None,
    )
    master_wallet = Wallet(
        id=1,
        customer_id=1,
        razorpay_customer_id="cust_master_agency",
        razorpay_payment_method_id="token_master_corporate_card",
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        res = MagicMock()
        q_str = str(query).lower()
        if "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = master_wallet
        return res

    mock_session.execute = mock_execute
    app.dependency_overrides[get_db] = lambda: mock_session

    service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    headers = {
        "Authorization": f"Bearer {service_token}",
        "X-Talkar-Email": "master@agency.com",
    }

    try:
        # Case A: Mismatched order_id
        fake_payment_mismatched_order = {
            "id": "pay_valid_123",
            "order_id": "order_different_session",
            "customer_id": "cust_master_agency",
            "token_id": "token_new_card",
        }
        with patch("services.razorpay_client.client") as mock_rzp, \
             patch.object(settings, "RAZORPAY_KEY_SECRET", "test_secret"):
            mock_rzp.payment.fetch.return_value = fake_payment_mismatched_order
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.post(
                    "/billing/confirm-add-card",
                    json={
                        "dograh_org_id": 10,
                        "razorpay_payment_id": "pay_valid_123",
                        "razorpay_order_id": "order_expected_session",
                    },
                    headers=headers,
                )
                assert res.status_code == 400
                assert "Payment does not match the add-card order session" in res.json().get("detail", "")

        # Case B: Mismatched customer_id
        fake_payment_mismatched_customer = {
            "id": "pay_valid_456",
            "order_id": "order_expected_session",
            "customer_id": "cust_foreign_attacker",
            "token_id": "token_attacker_card",
        }
        with patch("services.razorpay_client.client") as mock_rzp, \
             patch.object(settings, "RAZORPAY_KEY_SECRET", "test_secret"):
            mock_rzp.payment.fetch.return_value = fake_payment_mismatched_customer
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.post(
                    "/billing/confirm-add-card",
                    json={
                        "dograh_org_id": 10,
                        "razorpay_payment_id": "pay_valid_456",
                        "razorpay_order_id": "order_expected_session",
                    },
                    headers=headers,
                )
                assert res.status_code == 403
                assert "Payment does not belong to this organization's payment profile" in res.json().get("detail", "")
    finally:
        app.dependency_overrides.pop(get_db, None)
