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


@pytest.mark.asyncio
async def test_fix_11_get_customer_status_dynamic_countdown_active():
    """
    Test Fix 11:
    Verify that get_customer_status dynamically computes remaining_days and reapply_countdown
    from rejected_at timestamp instead of returning static strings.
    """
    from routers.customers import get_customer_status
    rejection_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    customer = Customer(
        id=1,
        dograh_org_id=10,
        status="rejected",
        onboarding_form={
            "reapply_countdown_days": 30,
            "rejected_at": rejection_time,
            "rejection_reason": "Incomplete verification",
        }
    )
    mock_db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = customer
    res.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=res)

    resp = await get_customer_status(dograh_org_id=10, db=mock_db)
    assert resp["status"] == "rejected"
    assert resp["rejection_reason"] == "Incomplete verification"
    assert resp["remaining_days"] == 20
    assert resp["reapply_countdown_days"] == 20
    assert "20d" in resp["reapply_countdown"]


@pytest.mark.asyncio
async def test_fix_11_get_customer_status_dynamic_countdown_elapsed():
    """
    Test Fix 11:
    Verify that when rejected_at + reapply_countdown_days has passed,
    remaining_days is 0 and reapply_countdown is '0d 0h'.
    """
    from routers.customers import get_customer_status
    rejection_time = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    customer = Customer(
        id=2,
        dograh_org_id=20,
        status="rejected",
        onboarding_form={
            "reapply_countdown_days": 30,
            "rejected_at": rejection_time,
            "rejection_reason": "Policy violation",
        }
    )
    mock_db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = customer
    res.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=res)

    resp = await get_customer_status(dograh_org_id=20, db=mock_db)
    assert resp["status"] == "rejected"
    assert resp["remaining_days"] == 0
    assert resp["reapply_countdown_days"] == 0
    assert resp["reapply_countdown"] == "0d 0h"


@pytest.mark.asyncio
async def test_fix_11_get_customer_status_zero_countdown_days():
    """
    Test Fix 11:
    Verify that when reapply_countdown_days is explicitly 0,
    remaining_days is 0 immediately.
    """
    from routers.customers import get_customer_status
    rejection_time = datetime.now(timezone.utc).isoformat()
    customer = Customer(
        id=3,
        dograh_org_id=30,
        status="rejected",
        onboarding_form={
            "reapply_countdown_days": 0,
            "rejected_at": rejection_time,
            "rejection_reason": "Minor error",
        }
    )
    mock_db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = customer
    res.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=res)

    resp = await get_customer_status(dograh_org_id=30, db=mock_db)
    assert resp["status"] == "rejected"
    assert resp["remaining_days"] == 0
    assert resp["reapply_countdown_days"] == 0
    assert resp["reapply_countdown"] == "0d 0h"


@pytest.mark.asyncio
async def test_fix_12_request_info_saves_message_and_returned_in_status():
    """
    Test Fix 12:
    Verify that admin request_info saves data.message to customer.onboarding_form["info_requested_message"],
    and get_customer_status returns info_requested_message.
    """
    from routers.admin import request_info, RequestInfoRequest
    from routers.customers import get_customer_status
    from db.models import TalkarAdmin

    customer = Customer(
        id=1,
        dograh_org_id=10,
        contact_email="alice@company.com",
        status="under_review",
        onboarding_form={"businessName": "Acme Inc"}
    )
    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q = str(query).lower()
        res = MagicMock()
        if "from customers" in q:
            res.scalar_one_or_none.return_value = customer
            res.scalars.return_value.first.return_value = customer
        elif "from subscriptions" in q:
            res.scalar_one_or_none.return_value = None
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.first.return_value = None
        return res
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    mock_admin = TalkarAdmin(id=1, email="admin@talkar.in", name="Admin", password_hash="hash", role="superadmin")

    with patch("services.notification_service.send_email", AsyncMock()) as mock_email:
        result = await request_info(
            customer_id=1,
            data=RequestInfoRequest(message="Please clarify call volume and upload GST certificate."),
            db=mock_db,
            current_admin=mock_admin
        )

    assert result["status"] == "info_requested"
    assert customer.status == "info_requested"
    assert customer.onboarding_form["info_requested_message"] == "Please clarify call volume and upload GST certificate."
    mock_email.assert_awaited_once()

    # Now verify get_customer_status exposes this to the frontend
    status_resp = await get_customer_status(dograh_org_id=10, db=mock_db)
    assert status_resp["status"] == "info_requested"
    assert status_resp["info_requested_message"] == "Please clarify call volume and upload GST certificate."


