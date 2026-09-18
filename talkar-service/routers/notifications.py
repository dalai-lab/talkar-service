from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, desc
from db.session import get_db
from db.models import Customer, Notification

router = APIRouter()

@router.get("/")
async def get_notifications(
    dograh_org_id: int = Query(...),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    # Get unread count
    unread_res = await db.execute(
        select(func.count(Notification.id))
        .where(Notification.customer_id == customer.id, Notification.is_read == False)
    )
    unread_count = unread_res.scalar_one()

    # Get recent notifications
    notif_res = await db.execute(
        select(Notification)
        .where(Notification.customer_id == customer.id)
        .order_by(desc(Notification.created_at))
        .limit(limit)
    )
    
    notifications = []
    for n in notif_res.scalars().all():
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
async def mark_notification_read(notification_id: int, dograh_org_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.customer_id == customer.id)
        .values(is_read=True)
    )
    await db.commit()
    return {"status": "success"}

@router.post("/read-all")
async def mark_all_notifications_read(dograh_org_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    result = await db.execute(
        update(Notification)
        .where(Notification.customer_id == customer.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"status": "success"}

@router.delete("/{notification_id}")
async def delete_notification(notification_id: int, dograh_org_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    res = await db.execute(select(Notification).where(Notification.id == notification_id, Notification.customer_id == customer.id))
    n = res.scalar_one_or_none()
    if not n:
        raise HTTPException(404, "Notification not found")
        
    await db.delete(n)
    await db.commit()
    return {"status": "deleted"}
