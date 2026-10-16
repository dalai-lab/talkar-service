from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, desc
from db.session import get_db
from db.models import Customer, Notification
from services.customer_auth import verify_customer_access

router = APIRouter()

@router.get("/")
async def get_notifications(
    dograh_org_id: int = Query(...),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization,
    )

    limit_val = getattr(limit, "default", limit)
    try:
        effective_limit = int(limit_val)
    except (TypeError, ValueError):
        effective_limit = 20

    if customer.billing_org_id:
        # Sub-org account: fetch sub-org's own notifications plus any master fleet billing/warning alerts
        notif_res = await db.execute(
            select(Notification)
            .where(Notification.customer_id == customer.id)
            .order_by(desc(Notification.created_at))
            .limit(effective_limit)
        )
        sub_notifs = list(notif_res.scalars().all())
        seen_keys = {(n.title, n.body) for n in sub_notifs}

        master_res = await db.execute(
            select(Notification)
            .where(
                Notification.customer_id == customer.billing_org_id,
                Notification.type.in_(["warning", "critical", "billing"])
            )
            .order_by(desc(Notification.created_at))
            .limit(effective_limit)
        )
        master_notifs = master_res.scalars().all()
        combined_notifs = list(sub_notifs)
        for mn in master_notifs:
            if (mn.title, mn.body) not in seen_keys:
                combined_notifs.append(mn)
                seen_keys.add((mn.title, mn.body))

        combined_notifs.sort(key=lambda n: n.created_at or datetime.min, reverse=True)
        notifications_list = combined_notifs[:effective_limit]
        unread_count = sum(1 for n in combined_notifs if not n.is_read)
    else:
        # Master account
        unread_res = await db.execute(
            select(func.count(Notification.id))
            .where(Notification.customer_id == customer.id, Notification.is_read == False)
        )
        unread_count = unread_res.scalar_one()

        notif_res = await db.execute(
            select(Notification)
            .where(Notification.customer_id == customer.id)
            .order_by(desc(Notification.created_at))
            .limit(effective_limit)
        )
        notifications_list = notif_res.scalars().all()
    
    notifications = []
    for n in notifications_list:
        notifications.append({
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "type": n.type,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None
        })
        
    return {
        "unread_count": unread_count,
        "notifications": notifications
    }

@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    dograh_org_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization,
    )
        
    # Sub-orgs must NEVER delete or mutate master notifications
    allowed_ids = [customer.id]

    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.customer_id.in_(allowed_ids))
        .values(is_read=True)
    )
    await db.commit()
    return {"status": "success"}

@router.post("/read-all")
async def mark_all_notifications_read(
    dograh_org_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization,
    )
        
    # Sub-orgs must NEVER delete or mutate master notifications
    allowed_ids = [customer.id]

    result = await db.execute(
        update(Notification)
        .where(Notification.customer_id.in_(allowed_ids), Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"status": "success"}

@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    dograh_org_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization,
    )
        
    # Sub-orgs must NEVER delete or mutate master notifications
    allowed_ids = [customer.id]

    res = await db.execute(
        select(Notification).where(Notification.id == notification_id, Notification.customer_id.in_(allowed_ids))
    )
    n = res.scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Notification not found")
        
    await db.delete(n)
    await db.commit()
    return {"status": "deleted"}
