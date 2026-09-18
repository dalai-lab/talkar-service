import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from db.session import get_db
from db.models import Announcement, Customer, Notification, TalkarAdmin
from services import notification_service
from services.admin_auth import get_current_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/announcements", tags=["Announcements"])


class CreateAnnouncementRequest(BaseModel):
    title: str
    body: str
    type: str = "general"  # "general" | "update" | "maintenance" | "critical"
    send_in_app: bool = True
    send_email: bool = True
    is_scheduled: bool = False
    scheduled_for: Optional[datetime] = None  # ISO timestamp


async def execute_announcement_broadcast(db: AsyncSession, announcement: Announcement) -> int:
    """Delivers an announcement to all eligible customers."""
    query = select(Customer).where(
        Customer.status.in_(["active", "agent_building", "approved"])
    )
    res = await db.execute(query)
    customers = res.scalars().all()

    channels = announcement.channels or []
    send_in_app = "in_app" in channels
    send_email = "email" in channels

    recipients_count = 0
    email_subject = f"Talkar Announcement: {announcement.title}"
    if announcement.type == "critical":
        email_subject = f"[CRITICAL] {announcement.title}"
    elif announcement.type == "maintenance":
        email_subject = f"[MAINTENANCE] {announcement.title}"
    elif announcement.type == "update":
        email_subject = f"[UPDATE] {announcement.title}"

    for cust in customers:
        recipients_count += 1
        if send_in_app:
            notif = Notification(
                customer_id=cust.id,
                title=announcement.title,
                body=announcement.body,
                type=announcement.type,
                is_read=False,
            )
            db.add(notif)

        if send_email and cust.contact_email:
            try:
                await notification_service.send_email(
                    to_email=cust.contact_email,
                    subject=email_subject,
                    body=announcement.body
                )
            except Exception as e:
                logger.error(f"Failed to dispatch announcement email to {cust.contact_email}: {e}")

    announcement.status = "sent"
    announcement.sent_at = datetime.now(timezone.utc)
    announcement.recipients_count = recipients_count
    await db.commit()
    logger.info(f"Broadcast announcement #{announcement.id} sent to {recipients_count} customers.")
    return recipients_count


@router.get("")
async def list_announcements(
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    """Retrieve all announcements ordered by most recent."""
    query = select(Announcement, TalkarAdmin.name.label("admin_name"))\
        .outerjoin(TalkarAdmin, Announcement.sent_by == TalkarAdmin.id)\
        .order_by(desc(Announcement.created_at))\
        .limit(100)
    
    res = await db.execute(query)
    items = []
    for ann, admin_name in res.all():
        items.append({
            "id": ann.id,
            "title": ann.title,
            "body": ann.body,
            "type": ann.type,
            "channels": ann.channels,
            "status": ann.status,
            "scheduled_for": ann.scheduled_for.isoformat() if ann.scheduled_for else None,
            "sent_at": ann.sent_at.isoformat() if ann.sent_at else None,
            "created_at": ann.created_at.isoformat() if ann.created_at else None,
            "recipients_count": ann.recipients_count,
            "author_name": admin_name or "Talkar Team",
        })
    return items


@router.post("")
async def create_announcement(
    data: CreateAnnouncementRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    """Create and either immediately broadcast or schedule an announcement."""
    if not data.title.strip():
        raise HTTPException(400, "Announcement title cannot be empty")
    if not data.body.strip():
        raise HTTPException(400, "Announcement message body cannot be empty")
    if not data.send_in_app and not data.send_email:
        raise HTTPException(400, "Please select at least one delivery channel (In-App or Email)")

    channels = []
    if data.send_in_app:
        channels.append("in_app")
    if data.send_email:
        channels.append("email")

    now = datetime.now(timezone.utc)
    is_scheduled = data.is_scheduled and data.scheduled_for and data.scheduled_for > now

    announcement = Announcement(
        title=data.title.strip(),
        body=data.body.strip(),
        type=data.type,
        channels=channels,
        status="scheduled" if is_scheduled else "sending",
        scheduled_for=data.scheduled_for if is_scheduled else None,
        sent_by=current_admin.id,
        recipients_count=0,
    )
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)

    if not is_scheduled:
        recipients = await execute_announcement_broadcast(db, announcement)
        return {
            "status": "sent",
            "id": announcement.id,
            "recipients_count": recipients,
            "message": f"Announcement broadcasted to {recipients} customers."
        }
    else:
        return {
            "status": "scheduled",
            "id": announcement.id,
            "scheduled_for": announcement.scheduled_for.isoformat(),
            "message": "Announcement scheduled successfully."
        }


@router.post("/{announcement_id}/cancel")
async def cancel_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    """Cancel a pending scheduled announcement."""
    query = select(Announcement).where(Announcement.id == announcement_id)
    res = await db.execute(query)
    ann = res.scalar_one_or_none()

    if not ann:
        raise HTTPException(404, "Announcement not found")
    if ann.status != "scheduled":
        raise HTTPException(400, f"Cannot cancel an announcement with status '{ann.status}'")

    ann.status = "cancelled"
    await db.commit()
    return {"status": "cancelled", "id": ann.id}
