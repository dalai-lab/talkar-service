from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from db.models import Customer
from pydantic import BaseModel
from typing import Optional
import logging
from services import notification_service

logger = logging.getLogger(__name__)

router = APIRouter()

class CreateCustomerRequest(BaseModel):
    email: str
    contact_name: str
    company_name: str = ""
    dograh_org_id: int
    dograh_user_id: int

class UpdateStatusRequest(BaseModel):
    status: str
    reason: Optional[str] = None

@router.post("/")
async def create_customer(data: CreateCustomerRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.contact_email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Customer with this email already exists")
    customer = Customer(
        contact_email=data.email,
        contact_name=data.contact_name,
        company_name=data.company_name,
        dograh_org_id=data.dograh_org_id,
        dograh_user_id=data.dograh_user_id,
        status="pending_approval"
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer

@router.get("/")
async def get_all_customers(
    status: Optional[str] = None,
    plan: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    query = select(Customer)
    if status:
        query = query.where(Customer.status == status)
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()

# IMPORTANT: static paths must come before /{customer_id} to avoid shadowing

@router.get("/status")
async def get_customer_status(
    dograh_org_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Called by Dograh UI middleware to gate account access.
    dograh_org_id or customer_id as query param.
    Unknown orgs return status=active (non-Talkar users pass through).
    """
    customer = None
    if dograh_org_id:
        result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
        customer = result.scalar_one_or_none()
    elif customer_id:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        customer = result.scalar_one_or_none()

    if not customer:
        return {"status": "active", "customer_id": None}

    resp: dict = {"status": customer.status, "customer_id": customer.id}
    if customer.status == "rejected" and customer.onboarding_form:
        resp["rejection_reason"] = customer.onboarding_form.get("rejection_reason")
    if customer.status == "suspended":
        from db.models import Wallet
        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
        wallet = wallet_res.scalar_one_or_none()
        resp["balance_paise"] = wallet.balance_paise if wallet else 0
    return resp

@router.get("/by-org/{dograh_org_id}")
async def get_customer_by_org(dograh_org_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")
    return customer

# Parameterized paths after static ones

@router.get("/{customer_id}")
async def get_customer(customer_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer

@router.patch("/{customer_id}/status")
async def update_customer_status(customer_id: int, data: UpdateStatusRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    customer.status = data.status
    await db.commit()
    await db.refresh(customer)
    return customer

@router.post("/{customer_id}/onboarding")
async def submit_onboarding(customer_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if customer.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Customer is not in pending_approval state")
    customer.onboarding_form = data.get("form", {})
    customer.documents = data.get("documents", [])
    customer.status = "under_review"
    await db.commit()
    await db.refresh(customer)
    await notification_service.send_email(
        to_email="admin@talkar.ai",
        subject=f"New Application: {customer.company_name}",
        body=f"{customer.company_name} ({customer.contact_email}) submitted their onboarding form. Review at admin.talkar.ai/applications"
    )
    return customer
