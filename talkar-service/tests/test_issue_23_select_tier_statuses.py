import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.customers import select_tier, SelectTierRequest
from config import TIER_CONFIG


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["under_review", "pending_approval", "approved", "agent_building", "pending_deposit"])
async def test_select_tier_allowed_for_pre_activation_statuses(status):
    """Verify Issue 23 / Fix 25: select_tier permits all pre-activation statuses."""
    customer = Customer(
        id=10,
        dograh_org_id=100,
        contact_email="test@example.com",
        status=status,
        onboarding_form={},
        billing_org_id=None
    )
    wallet = Wallet(id=1, customer_id=10, balance_paise=0)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = customer

    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(side_effect=[cust_res, sub_res])

    req = SelectTierRequest(tier="starter")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))):
        res = await select_tier(org_id=100, data=req, db=mock_db)

    assert res["status"] == "pending_deposit"
    assert customer.status == "pending_deposit"
    assert customer.onboarding_form.get("approved_tier") == "starter"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["suspended", "churned", "rejected"])
async def test_select_tier_rejected_for_invalid_statuses(status):
    """Verify Issue 23 / Fix 25: select_tier rejects suspended, churned, and rejected accounts."""
    customer = Customer(
        id=10,
        dograh_org_id=100,
        contact_email="test@example.com",
        status=status,
        onboarding_form={},
        billing_org_id=None
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=cust_res)

    req = SelectTierRequest(tier="starter")

    with pytest.raises(HTTPException) as exc_info:
        await select_tier(org_id=100, data=req, db=mock_db)

    assert exc_info.value.status_code == 400
    assert f"Cannot select plan in current status: {status}" in exc_info.value.detail
