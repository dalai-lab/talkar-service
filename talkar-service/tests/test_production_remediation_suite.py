import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from db.models import Customer, Subscription, Wallet, CallLog
from routers.billing import ConfirmTopupRequest, DograhQuotaRequest, DograhDeductRequest, confirm_topup, check_quota, deduct_for_run


@pytest.mark.asyncio
async def test_confirm_topup_blocks_mock_signature_in_production():
    """CRITICAL-1: In production/staging, mock_signature must be rejected with 400."""
    req = ConfirmTopupRequest(
        dograh_org_id=10,
        amount_rupees=500,
        razorpay_order_id="order_123",
        razorpay_payment_id="pay_123",
        razorpay_signature="mock_signature",
    )
    mock_db = AsyncMock()
    with patch.object(settings, "ENVIRONMENT", "production"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", "secret_key"):
        with pytest.raises(HTTPException) as exc_info:
            await confirm_topup(req, db=mock_db)
        assert exc_info.value.status_code == 400
        assert "Mock payment signatures are disabled in production" in exc_info.value.detail


@pytest.mark.asyncio
async def test_confirm_topup_fails_closed_if_secret_missing_in_production():
    """CRITICAL-1: In production, missing RAZORPAY_KEY_SECRET must reject transactions with 500."""
    req = ConfirmTopupRequest(
        dograh_org_id=10,
        amount_rupees=500,
        razorpay_order_id="order_123",
        razorpay_payment_id="pay_123",
        razorpay_signature="sig_123",
    )
    mock_db = AsyncMock()
    with patch.object(settings, "ENVIRONMENT", "production"), \
         patch.object(settings, "RAZORPAY_KEY_SECRET", ""):
        with pytest.raises(HTTPException) as exc_info:
            await confirm_topup(req, db=mock_db)
        assert exc_info.value.status_code == 500
        assert "Payment gateway is not configured for production" in exc_info.value.detail


@pytest.mark.asyncio
async def test_startup_blocks_on_default_secrets_in_production():
    """CRITICAL-2: In production, default or short token/secret must crash startup."""
    from main import lifespan
    from fastapi import FastAPI
    app = FastAPI()

    with patch.object(settings, "ENVIRONMENT", "production"), \
         patch.object(settings, "TALKAR_BILLING_API_TOKEN", "change-me-in-production-billing-token"):
        with pytest.raises(RuntimeError, match="CRITICAL SECURITY ERROR: TALKAR_BILLING_API_TOKEN"):
            async with lifespan(app):
                pass

    with patch.object(settings, "ENVIRONMENT", "production"), \
         patch.object(settings, "TALKAR_BILLING_API_TOKEN", "secure-token-123456789"), \
         patch.object(settings, "TALKAR_ADMIN_JWT_SECRET", "change-me-in-production-admin-secret"):
        with pytest.raises(RuntimeError, match="CRITICAL SECURITY ERROR: TALKAR_ADMIN_JWT_SECRET"):
            async with lifespan(app):
                pass


@pytest.mark.asyncio
async def test_deduct_value_error_zeros_call_log_cost():
    """MEDIUM-4: If ValueError occurs (e.g. master suspended), call_log.cost_to_customer_paise is 0."""
    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub])

    added_objects = []
    def fake_add(obj):
        added_objects.append(obj)
    mock_db.add = fake_add

    req = DograhDeductRequest(
        workflow_run_id=902,
        duration_seconds=60,
        organization_id=200,
        mode="telephony",
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(side_effect=ValueError("Master org 10 is suspended"))):
        res = await deduct_for_run(data=req, db=mock_db, _token="test_token")

    assert res["status"] == "master_suspended"
    assert len(added_objects) == 1
    call_log = added_objects[0]
    assert isinstance(call_log, CallLog)
    assert call_log.cost_to_customer_paise == 0


@pytest.mark.asyncio
async def test_active_calls_bounds_validation_in_check_quota():
    """MEDIUM-6: check_quota rejects active_calls > 1000."""
    mock_db = AsyncMock()
    customer = Customer(id=1, dograh_org_id=10, status="active")
    sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_wallet = MagicMock()
    res_wallet.scalar_one_or_none.return_value = wallet

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub, res_wallet])

    req = DograhQuotaRequest(
        organization_id=10,
        mode="telephony",
        active_calls=1500,
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))):
        res = await check_quota(data=req, db=mock_db, _token="valid_token")

    assert res["has_quota"] is False
    assert res["reason"] == "invalid_concurrency_count"
