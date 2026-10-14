import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Notification
from routers.notifications import (
    get_notifications,
    mark_notification_read,
    mark_all_notifications_read,
    delete_notification,
)
from services import notification_service


@pytest.mark.asyncio
async def test_push_notification_cascades_to_all_suborgs():
    """
    Verify that push_notification(cascade_to_suborgs=True) writes
    in-app notification rows for the master and all child sub-orgs.
    """
    master = Customer(id=1, dograh_org_id=100, contact_email="agency@example.com")
    sub_1 = Customer(id=2, dograh_org_id=200, billing_org_id=1, contact_email="client1@example.com")
    sub_2 = Customer(id=3, dograh_org_id=300, billing_org_id=1, contact_email="client2@example.com")

    added_objects = []

    mock_db = MagicMock()

    def mock_add(obj):
        added_objects.append(obj)

    mock_db.add = mock_add
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [2, 3]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("db.session.AsyncSessionLocal", return_value=mock_session_cm):
        await notification_service.push_notification(
            customer_id=1,
            title="Service paused due to low balance",
            body="Low balance alert",
            notification_type="warning",
            cascade_to_suborgs=True,
        )

    # Must create a notification for master (id=1) AND sub-orgs (id=2, id=3)
    notif_customer_ids = [n.customer_id for n in added_objects if isinstance(n, Notification)]
    assert 1 in notif_customer_ids, "Master must receive in-app notification"
    assert 2 in notif_customer_ids, "Sub-org 1 must receive in-app notification"
    assert 3 in notif_customer_ids, "Sub-org 2 must receive in-app notification"
    assert len(notif_customer_ids) == 3


@pytest.mark.asyncio
async def test_billing_alerts_cascade_to_suborgs():
    """
    Verify that billing alerts (notify_customer_low_balance, notify_customer_service_paused,
    notify_customer_auto_recharge_failed) pass cascade_to_suborgs=True to push_notification.
    """
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("services.notification_service.send_email_and_push", new_callable=AsyncMock) as mock_send_email_and_push, \
         patch("services.notification_service._get_customer_email", AsyncMock(return_value=("agency@example.com", "Agency"))), \
         patch("services.redis_client.get_redis", AsyncMock(return_value=None)), \
         patch("services.notification_service.AsyncSessionLocal", return_value=mock_session_cm), \
         patch("db.session.AsyncSessionLocal", return_value=mock_session_cm):

        # 1. Low balance alert
        await notification_service.notify_customer_low_balance(customer_id=1, balance_paise=40000)
        assert mock_send_email_and_push.called
        assert mock_send_email_and_push.call_args.kwargs.get("cascade_to_suborgs") is True

        mock_send_email_and_push.reset_mock()

        # 2. Service paused alert
        await notification_service.notify_customer_service_paused(customer_id=1)
        assert mock_send_email_and_push.called
        assert mock_send_email_and_push.call_args.kwargs.get("cascade_to_suborgs") is True

        mock_send_email_and_push.reset_mock()

        # 3. Auto recharge failed alert
        await notification_service.notify_customer_auto_recharge_failed(customer_id=1, error_detail="Card expired")
        assert mock_send_email_and_push.called
        assert mock_send_email_and_push.call_args.kwargs.get("cascade_to_suborgs") is True

        mock_send_email_and_push.reset_mock()

        # 4. Suspended alert
        await notification_service.notify_customer_suspended(customer_id=1, reason="zero_balance")
        assert mock_send_email_and_push.called
        assert mock_send_email_and_push.call_args.kwargs.get("cascade_to_suborgs") is True


@pytest.mark.asyncio
async def test_get_notifications_for_suborg_inherits_master_fleet_alerts_without_duplication():
    """
    Verify GET /notifications for a sub-org:
    1. Returns notifications for the sub-org.
    2. Inherits master warning/critical/billing alerts if not already duplicated.
    3. Excludes master's private info notifications (e.g. team member invites).
    4. Accurately calculates unread_count.
    """
    sub_org = Customer(id=2, dograh_org_id=200, billing_org_id=1, contact_email="sub@example.com")

    # Sub-org's own notification
    sub_notif = Notification(
        id=201,
        customer_id=2,
        title="Your Voice Agent is Live",
        body="Ready to take calls",
        type="info",
        is_read=True,
        created_at=datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc),
    )

    # Master fleet alerts
    master_warning = Notification(
        id=101,
        customer_id=1,
        title="Low Balance Warning: Talkar Wallet",
        body="Low balance: ₹450.00. Please add credits.",
        type="warning",
        is_read=False,
        created_at=datetime(2026, 10, 14, 11, 0, tzinfo=timezone.utc),
    )
    master_private_info = Notification(
        id=102,
        customer_id=1,
        title="Team Member Invited to Agency",
        body="dev@agency.com was invited",
        type="info",  # Should not be inherited by sub-org!
        is_read=False,
        created_at=datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
    )

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "notifications.type in" in q_str:
            # Query for master notifications filtered by type
            mock_res.scalars.return_value.all.return_value = [master_warning]
        elif "notifications.customer_id = :customer_id_1" in q_str or "customer_id = 2" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_notif]
        else:
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("routers.notifications.verify_customer_access", AsyncMock()):
        resp = await get_notifications(dograh_org_id=200, db=mock_db)

    # Sub-org should see its own notif AND master's warning alert, but NOT master's private info notif
    notif_titles = [n["title"] for n in resp["notifications"]]
    assert "Your Voice Agent is Live" in notif_titles
    assert "Low Balance Warning: Talkar Wallet" in notif_titles
    assert "Team Member Invited to Agency" not in notif_titles
    assert resp["unread_count"] == 1  # 1 unread (the master warning)


@pytest.mark.asyncio
async def test_suborg_mark_read_and_delete_notification():
    """
    Verify PATCH /notifications/{id}/read and DELETE /notifications/{id}
    allow sub-org to mark read and delete notifications.
    """
    sub_org = Customer(id=2, dograh_org_id=200, billing_org_id=1, contact_email="sub@example.com")
    notif = Notification(id=101, customer_id=2, title="Alert", body="Test alert", type="warning", is_read=False)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "notifications.id = :id_1" in q_str:
            mock_res.scalar_one_or_none.return_value = notif
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()
    mock_db.delete = AsyncMock()

    with patch("routers.notifications.verify_customer_access", AsyncMock()):
        # 1. Mark read
        res_read = await mark_notification_read(notification_id=101, dograh_org_id=200, db=mock_db)
        assert res_read["status"] == "success"

        # 2. Mark all read
        res_all = await mark_all_notifications_read(dograh_org_id=200, db=mock_db)
        assert res_all["status"] == "success"

        # 3. Delete notification
        res_del = await delete_notification(notification_id=101, dograh_org_id=200, db=mock_db)
        assert res_del["status"] == "deleted"
        mock_db.delete.assert_called_once_with(notif)
