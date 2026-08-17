from fastapi import APIRouter, Depends, Query, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.session import get_db
from db.models import Customer, Subscription
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import logging
from services import notification_service
from config import settings

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
    result = await db.execute(select(Customer).where(Customer.contact_email == data.email).order_by(Customer.id))
    existing = result.scalars().first()
    
    billing_org_id = None
    if existing:
        billing_org_id = existing.billing_org_id if existing.billing_org_id else existing.id

    customer = Customer(
        contact_email=data.email,
        contact_name=data.contact_name,
        company_name=data.company_name,
        dograh_org_id=data.dograh_org_id,
        dograh_user_id=data.dograh_user_id,
        billing_org_id=billing_org_id,
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
    contact_email: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Called by Dograh UI middleware to gate account access.
    dograh_org_id, customer_id, or contact_email as query param.
    Unknown orgs return status=active (non-Talkar users pass through).
    """
    customer = None
    if dograh_org_id:
        result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
        customer = result.scalar_one_or_none()
    elif customer_id:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        customer = result.scalar_one_or_none()
    elif contact_email:
        result = await db.execute(select(Customer).where(Customer.contact_email == contact_email))
        customer = result.scalar_one_or_none()

    if not customer:
        return {"status": "active", "customer_id": None}

    resp: dict = {
        "status": customer.status, 
        "customer_id": customer.id,
        "dograh_org_id": customer.dograh_org_id,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID
    }
    if customer.status == "rejected" and customer.onboarding_form:
        resp["rejection_reason"] = customer.onboarding_form.get("rejection_reason")
        resp["reapply_countdown"] = f"{customer.onboarding_form.get('reapply_countdown_days', 30)}d 0h"
    if customer.status == "approved" and customer.setup_fee_order_id:
        resp["setup_fee_order_id"] = customer.setup_fee_order_id
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

@router.post("/by-org/{dograh_org_id}/onboarding")
async def submit_onboarding_by_org(dograh_org_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")
    if customer.status != "pending_approval":
        raise HTTPException(status_code=400, detail="Customer is not in pending_approval state")
    customer.onboarding_form = data.get("form", {})
    customer.documents = data.get("documents", [])
    customer.status = "under_review"
    await db.commit()
    await db.refresh(customer)
    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"New Application: {customer.company_name}",
        body=f"{customer.company_name} ({customer.contact_email}) submitted their onboarding form. Review at admin.talkar.ai/applications"
    )
    return customer

@router.post("/{customer_id}/onboarding")
async def submit_onboarding_by_id(customer_id: int, data: dict, db: AsyncSession = Depends(get_db)):
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
        to_email=settings.ADMIN_EMAIL,
        subject=f"New Application: {customer.company_name}",
        body=f"{customer.company_name} ({customer.contact_email}) submitted their onboarding form. Review at admin.talkar.ai/applications"
    )
    return customer
# Parameterized paths after static ones

@router.get("/{customer_id}")
async def get_customer(customer_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer






class SelectTierRequest(BaseModel):
    tier: str  # "starter" | "pro" | "elite"

@router.post("/by-org/{org_id}/select-tier")
async def select_tier(org_id: int, data: SelectTierRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "pending_plan_selection":
        raise HTTPException(400, "Customer is not pending plan selection")
        
    from config import TIER_CONFIG
    tier_cfg = TIER_CONFIG.get(data.tier)
    if not tier_cfg: raise HTTPException(400, "Invalid tier")
    
    # Store approved_tier in onboarding_form
    existing_form = customer.onboarding_form or {}
    existing_form["approved_tier"] = data.tier
    customer.onboarding_form = dict(existing_form)
    
    # Update Subscription
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    if sub:
        sub.plan = data.tier
        sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]
        
    customer.status = "active"
    await db.commit()
    
    # Re-run provisioning
    from services.provisioning_service import run_provisioning
    await run_provisioning(customer.id, None, db)
    
    await notification_service.send_email(
        to_email=customer.contact_email,
        subject="Your Talkar Agent is Live!",
        body=f"Hi {customer.contact_name}, your AI agent is fully active on the {data.tier} tier!"
    )
    return {"status": "active"}

class TierUpgradeRequest(BaseModel):
    requested_tier: str

@router.post("/by-org/{org_id}/request-tier-upgrade")
async def request_tier_upgrade(org_id: int, data: TierUpgradeRequest, db: AsyncSession = Depends(get_db)):
    from config import TIER_CONFIG
    
    if data.requested_tier not in TIER_CONFIG:
        raise HTTPException(400, f"Invalid tier: {data.requested_tier}")
        
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if customer.status not in ["active", "pending_plan_selection"]:
        raise HTTPException(400, f"Customer must be active to upgrade tier (current status: {customer.status})")
    
    is_activating = (customer.status == "pending_plan_selection")
    
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    current_tier = sub.plan if sub else "starter"
    
    if current_tier == data.requested_tier:
        raise HTTPException(400, f"Already on {data.requested_tier} tier")
        
    # Instantly update subscription
    if sub:
        sub.plan = data.requested_tier
        sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
    
    existing_form = customer.onboarding_form or {}
    # Apply the new tier
    existing_form["approved_tier"] = data.requested_tier
    # Clear any old upgrade requests
    existing_form.pop("tier_upgrade_requested", None)
    existing_form.pop("tier_upgrade_requested_at", None)
    
    customer.onboarding_form = dict(existing_form) 
    
    if is_activating:
        customer.status = "active"
        
    await db.commit()
    
    if is_activating:
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your Talkar Agent is Live!",
            body=f"Hi {customer.contact_name}, your AI agent is fully active on the {data.requested_tier} tier!"
        )
    
    # Run best-effort synchronous provisioning to sync to Dograh
    from services.provisioning_service import run_provisioning
    await run_provisioning(customer.id, data.requested_tier, db)
    
    # Notify customer of instant upgrade/downgrade
    await notification_service.send_email(
        to_email=customer.contact_email,
        subject=f"Your Talkar Tier has been updated to {data.requested_tier.title()}",
        body=f"Hi {customer.contact_name},\n\nYour tier has been instantly updated from {current_tier} to {data.requested_tier}. Your agent's concurrency limits and pricing have been adjusted automatically.\n\nThank you for using Talkar!"
    )
    return {"status": "success", "new_tier": data.requested_tier}

class PhoneNumberRequestBody(BaseModel):
    quantity: int
    region: str
    use_case: str

@router.post("/by-org/{org_id}/request-phone-numbers")
async def request_phone_numbers(org_id: int, data: PhoneNumberRequestBody, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "active": raise HTTPException(400, "Customer not active")
    
    from db.models import PhoneNumberRequest
    # Check for existing pending request
    existing = await db.execute(
        select(PhoneNumberRequest).where(
            PhoneNumberRequest.customer_id == customer.id,
            PhoneNumberRequest.status == "pending"
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "You already have a pending phone number request")
        
    req = PhoneNumberRequest(
        customer_id=customer.id,
        quantity=data.quantity,
        region=data.region,
        use_case=data.use_case
    )
    db.add(req)
    await db.commit()
    
    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar] Phone Number Request — {customer.company_name}",
        body=f"{customer.company_name} requested {data.quantity} numbers in {data.region}. Use Case: {data.use_case}."
    )
    return {"status": "request_submitted"}

class SupportRequestPayload(BaseModel):
    type: str
    subject: str
    description: str
    agent_id: Optional[int] = None

@router.post("/support-requests")
async def create_support_request(data: SupportRequestPayload, dograh_org_id: int = Query(...), x_talkar_email: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if not x_talkar_email or x_talkar_email != customer.contact_email:
        raise HTTPException(403, "Not authorized for this organization")
    
    from db.models import SupportRequest
    req = SupportRequest(
        customer_id=customer.id,
        type=data.type,
        subject=data.subject,
        description=data.description,
        agent_id=data.agent_id
    )
    db.add(req)
    await db.commit()
    
    await notification_service.send_email(
        to_email="admin@talkar.ai",
        subject=f"[Talkar Support] {data.subject} - {customer.company_name}",
        body=f"New support request from {customer.company_name} ({customer.contact_email}):\n\nType: {data.type}\n\nDescription: {data.description}"
    )
    return {"status": "success"}

@router.get("/support-requests")
async def get_support_requests(dograh_org_id: int = Query(...), x_talkar_email: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")

    if not x_talkar_email or x_talkar_email != customer.contact_email:
        raise HTTPException(403, "Not authorized for this organization")
    
    from db.models import SupportRequest
    reqs = await db.execute(select(SupportRequest).where(SupportRequest.customer_id == customer.id).order_by(SupportRequest.created_at.desc()))
    return reqs.scalars().all()

