import sys
from pathlib import Path
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer
from routers.customers import submit_onboarding_by_org, save_agent_brief


@pytest.mark.asyncio
async def test_blocker_15_rejected_customer_can_reapply_when_countdown_zero():
    """
    Test Blocker 15:
    When customer.status == 'rejected' and reapply_countdown_days == 0,
    the customer can resubmit onboarding and status becomes 'under_review'.
    """
    customer = Customer(
        id=1,
        dograh_org_id=10,
        status="rejected",
        onboarding_form={"reapply_countdown_days": 0, "rejection_reason": "Incomplete"}
    )
    mock_db = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    with patch("services.notification_service.send_email", AsyncMock()):
        result = await submit_onboarding_by_org(
            dograh_org_id=10,
            data={"form": {"businessName": "Reapplied Inc", "pocName": "CEO"}},
            db=mock_db
        )

    assert result.status == "under_review"
    assert result.company_name == "Reapplied Inc"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_blocker_15_rejected_customer_locked_out_when_countdown_active():
    """
    Test Blocker 15:
    When customer.status == 'rejected' and reapply_countdown_days has not elapsed,
    submission raises HTTP 400.
    """
    recent_rejection = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    customer = Customer(
        id=2,
        dograh_org_id=20,
        status="rejected",
        onboarding_form={
            "reapply_countdown_days": 30,
            "rejected_at": recent_rejection,
            "rejection_reason": "Policy violation"
        }
    )
    mock_db = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res)

    with pytest.raises(HTTPException) as exc_info:
        await submit_onboarding_by_org(
            dograh_org_id=20,
            data={"form": {"businessName": "Premature Inc"}},
            db=mock_db
        )
    assert exc_info.value.status_code == 400
    assert "Reapplication is locked" in exc_info.value.detail


@pytest.mark.asyncio
async def test_blocker_16_save_agent_brief_preserves_active_and_agent_building_status():
    """
    Test Blocker 16:
    When an active customer updates their agent brief, customer.status remains 'active'.
    When in agent_building, customer.status remains 'agent_building'.
    When in pending_approval, customer.status is updated to 'under_review'.
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    # 1. Active customer
    active_cust = Customer(id=1, dograh_org_id=10, status="active", onboarding_form={})
    res_active = MagicMock()
    res_active.scalar_one_or_none.return_value = active_cust
    mock_db.execute = AsyncMock(return_value=res_active)

    with patch("services.notification_service.send_email", AsyncMock()):
        await save_agent_brief(dograh_org_id=10, data={"form": {"goals": "Sell more"}}, db=mock_db)

    assert active_cust.status == "active"

    # 2. Agent building customer
    building_cust = Customer(id=2, dograh_org_id=20, status="agent_building", onboarding_form={})
    res_building = MagicMock()
    res_building.scalar_one_or_none.return_value = building_cust
    mock_db.execute = AsyncMock(return_value=res_building)

    with patch("services.notification_service.send_email", AsyncMock()):
        await save_agent_brief(dograh_org_id=20, data={"form": {"goals": "Support"}}, db=mock_db)

    assert building_cust.status == "agent_building"

    # 3. Pending approval customer
    pending_cust = Customer(id=3, dograh_org_id=30, status="pending_approval", onboarding_form={})
    res_pending = MagicMock()
    res_pending.scalar_one_or_none.return_value = pending_cust
    mock_db.execute = AsyncMock(return_value=res_pending)

    with patch("services.notification_service.send_email", AsyncMock()):
        await save_agent_brief(dograh_org_id=30, data={"form": {"goals": "Inbound"}}, db=mock_db)

    assert pending_cust.status == "under_review"
