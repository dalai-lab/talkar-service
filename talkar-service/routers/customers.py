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
    result = await db.execute(
        select(Customer)
        .where(Customer.dograh_org_id == data.dograh_org_id)
        .order_by(Customer.updated_at.desc())
        .limit(1)
    )
    existing_org = result.scalars().first()
    if existing_org:
        return existing_org

    # Only link as a sub-org if an existing record is in a real "established" state.
    # Stale pending_approval or rejected records (e.g. from old test runs or failed
    # applications) should NOT cause a new workspace to be treated as a sub-org.
    ESTABLISHED_STATUSES = ["active", "agent_building", "approved", "pending_deposit",
                            "pending_plan_selection", "under_review", "suspended"]
    result_email = await db.execute(
        select(Customer)
        .where(Customer.contact_email == data.email)
        .where(Customer.status.in_(ESTABLISHED_STATUSES))
        # Prefer master orgs (no billing_org_id) so we link to the billing root
        .order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())
        .limit(1)
    )
    existing_email = result_email.scalar_one_or_none()

    billing_org_id = None
    if existing_email:
        billing_org_id = existing_email.billing_org_id if existing_email.billing_org_id else existing_email.id

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
    
    # Sub-org auto-provisioning is now deferred until they submit the brief
    # in save_agent_brief, so we know what they actually want us to build.
    if billing_org_id:
        logger.info(f"Sub-org {customer.id} created for master {billing_org_id}; awaiting brief submission")
            
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
@router.get("/existing")
async def get_existing_customer(contact_email: str, db: AsyncSession = Depends(get_db)):
    """
    Check if an email already has an active/approved customer record in any org.
    Used by the frontend to detect returning customers creating a new workspace.
    Returns the master customer_id if found, or 404.
    """
    result = await db.execute(
        select(Customer)
        .where(Customer.contact_email == contact_email)
        .where(Customer.status.in_([
            "active", "agent_building", "pending_deposit",
            "pending_plan_selection", "under_review", "approved", "suspended"
        ]))
        # Prefer billing-master orgs (no billing_org_id)
        .order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())
        .limit(1)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="No existing customer found")
    return {
        "customer_id": customer.id,
        "status": customer.status,
        "contact_name": customer.contact_name,
        "company_name": customer.company_name,
    }

@router.post("/by-org/{dograh_org_id}/new-agent-brief")
async def save_agent_brief(dograh_org_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    """
    Called when a returning customer fills the brief form on a workspace where
    a sub-org customer record already exists (auto-created by the Dograh workspace hook)
    but has no onboarding form yet. Updates the existing record and notifies admin.
    """
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="No customer record found for this org. Use /new-agent-request instead.")

    form_data = data.get("form", {})
    customer.onboarding_form = form_data
    
    # Check if this is a sub-org and the master is active
    master_is_active = False
    master_sub = None
    if customer.billing_org_id:
        master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
        master = master_res.scalar_one_or_none()
        if master and master.status == "active":
            master_is_active = True
            master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master.id))
            master_sub = master_sub_res.scalar_one_or_none()
            
    if master_is_active and master_sub:
        customer.status = "agent_building"
        form_data["approved_tier"] = master_sub.plan
        customer.onboarding_form = form_data
        await db.commit()
        await db.refresh(customer)
        
        from services.provisioning_service import run_provisioning
        try:
            await run_provisioning(customer.id, master_sub.plan, db)
            logger.info(f"Auto-provisioned sub-org {customer.id} with tier {master_sub.plan} after brief submission")
        except Exception as e:
            logger.error(f"Failed to auto-provision sub-org {customer.id}: {e}")
            
        await notification_service.send_email(
            to_email=settings.ADMIN_EMAIL,
            subject=f"[Talkar] New Agent Requested — {customer.contact_email}",
            body=(
                f"Existing customer {customer.contact_email} has submitted a new agent brief "
                f"for Dograh Org ID: {customer.dograh_org_id}.\n\n"
                f"Tier: {master_sub.plan}\n"
                f"Brief:\n{form_data}\n\n"
                f"Please build the agent and mark it ready in the admin build queue."
            )
        )
    else:
        customer.status = "pending_approval"
        await db.commit()
        await db.refresh(customer)

        await notification_service.send_email(
            to_email=settings.ADMIN_EMAIL,
            subject=f"New Agent Brief: {customer.company_name or customer.contact_email}",
            body=(
                f"{customer.contact_email} has submitted a new agent brief "
                f"for workspace org {dograh_org_id}.\n\n"
                f"Brief:\n{form_data}\n\n"
                f"Review at admin.talkar.ai/applications"
            )
        )
    return customer

@router.post("/by-org/{dograh_org_id}/new-agent-request")

async def create_new_agent_request(dograh_org_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    """
    Called when a returning customer fills the brief form on a brand-new workspace
    that has no Talkar customer record yet. Creates a sub-org customer record
    linked to the master billing org and notifies admin.
    """
    master_customer_id = data.get("master_customer_id")
    if not master_customer_id:
        raise HTTPException(status_code=400, detail="master_customer_id is required")

    # Verify master exists
    master_res = await db.execute(select(Customer).where(Customer.id == master_customer_id))
    master = master_res.scalar_one_or_none()
    if not master:
        raise HTTPException(status_code=404, detail="Master customer not found")

    # Guard: don't create duplicates
    existing_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    existing = existing_res.scalar_one_or_none()
    if existing:
        return existing  # Idempotent

    # Determine billing master (if master is itself a sub-org, use its master)
    billing_org_id = master.billing_org_id or master.id

    new_customer = Customer(
        company_name=master.company_name,
        industry=master.industry,
        contact_name=master.contact_name,
        contact_email=master.contact_email,
        contact_phone=master.contact_phone,
        status="pending_approval",  # Admin must review and build
        onboarding_form=data.get("form", {}),
        billing_org_id=billing_org_id,
        dograh_org_id=dograh_org_id,
    )
    db.add(new_customer)
    await db.commit()
    await db.refresh(new_customer)

    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"New Agent Request: {master.company_name}",
        body=(
            f"{master.company_name} ({master.contact_email}) has requested a new agent "
            f"for workspace org {dograh_org_id}.\n\n"
            f"Brief:\n{data.get('form', {})}\n\n"
            f"Review at admin.talkar.ai/applications"
        )
    )
    return new_customer

@router.get("/status")

async def get_customer_status(
    dograh_org_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    contact_email: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Called by Dograh UI middleware to gate account access.
    dograh_org_id or customer_id as query param.
    Unknown orgs return 404 so the UI can redirect new workspaces to /onboarding.
    contact_email fallback only used as last resort (no org_id available) and
    returns the customer with no dograh_org_id set (i.e. legacy single-org users).
    """
    customer = None
    if dograh_org_id:
        # Use updated_at DESC + limit(1) so that if duplicate records exist for the same
        # org (shouldn't happen but has occurred from test cleanup), we always return the
        # most recently updated one (e.g. the just-approved one, not the stale under_review).
        result = await db.execute(
            select(Customer)
            .where(Customer.dograh_org_id == dograh_org_id)
            .order_by(Customer.updated_at.desc())
            .limit(1)
        )
        customer = result.scalars().first()
    elif customer_id:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        customer = result.scalar_one_or_none()
    elif contact_email:
        # Legacy fallback: only match customers that haven't been linked to a dograh_org yet
        result = await db.execute(
            select(Customer)
            .where(Customer.contact_email == contact_email)
            .where(Customer.dograh_org_id == None)  # noqa: E711
            .order_by(Customer.id.asc())
            .limit(1)
        )
        customer = result.scalar_one_or_none()

    if not customer:
        raise HTTPException(status_code=404, detail="No Talkar account found for this workspace")

    resp: dict = {
        "status": customer.status, 
        "customer_id": customer.id,
        "dograh_org_id": customer.dograh_org_id,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "is_sub_org": bool(customer.billing_org_id),
        "has_onboarding_form": bool(customer.onboarding_form)
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
    if customer.status not in ("pending_approval", "info_requested"):
        # Customer is already past the submission stage — silently succeed so the
        # frontend doesn't break, but DO NOT overwrite status or form data.
        # Critically: do NOT allow agent_building/approved/active to be reset to under_review.
        return customer
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

@router.post("/by-org/{dograh_org_id}/new-agent-brief")
async def submit_new_agent_brief(dograh_org_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")
    
    # Allow submitting brief as long as it's still building
    if customer.status != "agent_building":
        return customer 

    # We preserve any existing approved_tier set by auto-provisioning
    existing_form = customer.onboarding_form or {}
    existing_form.update(data.get("form", {}))
    customer.onboarding_form = existing_form
    
    await db.commit()
    await db.refresh(customer)
    
    # Check if there is a master org to lookup the name
    master_name = customer.company_name
    if customer.billing_org_id:
        master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
        master = master_res.scalar_one_or_none()
        if master: master_name = master.company_name

    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"New Agent Brief Submitted: {master_name}",
        body=f"{master_name} ({customer.contact_email}) has submitted a brief for their new agent (Org {dograh_org_id}).\n\nReview at admin.talkar.ai/applications"
    )
    return customer

@router.post("/{customer_id}/onboarding")
async def submit_onboarding_by_id(customer_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if customer.status not in ("pending_approval", "info_requested"):
        return customer # D-14: Silent success if already processed
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
    if customer.status not in ("pending_plan_selection", "pending_deposit"):
        raise HTTPException(400, f"Cannot select plan in current status: {customer.status}")

    from config import TIER_CONFIG, WALLET_ACTIVATION_THRESHOLD_PAISE
    tier_cfg = TIER_CONFIG.get(data.tier)
    if not tier_cfg: raise HTTPException(400, "Invalid tier")

    # If still in pending_deposit, verify wallet is funded before activating
    if customer.status == "pending_deposit":
        from services.billing_service import get_billing_wallet
        wallet, _ = await get_billing_wallet(db, customer.id)
        if not wallet or wallet.balance_paise < WALLET_ACTIVATION_THRESHOLD_PAISE:
            raise HTTPException(400, f"Wallet balance is below the minimum required to activate (₹{WALLET_ACTIVATION_THRESHOLD_PAISE // 100}). Please top up first.")
    
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
    
    # Re-run provisioning in its own session so any failure inside it cannot
    # rollback the customer.status = "active" commit above.
    from services.provisioning_service import run_provisioning
    await run_provisioning(customer.id, None, db=None)
    
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
    
    # Cascade to sub-orgs (Risk 3)
    sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
    for sub_org in sub_orgs_res.scalars().all():
        sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
        sub_sub = sub_sub_res.scalar_one_or_none()
        if sub_sub:
            sub_sub.plan = data.requested_tier
            sub_sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
            
        sub_form = sub_org.onboarding_form or {}
        sub_form["approved_tier"] = data.requested_tier
        sub_org.onboarding_form = dict(sub_form)
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(sub_org, "onboarding_form")
        
        await db.commit()
        if sub_org.status == "active":
            try:
                await run_provisioning(sub_org.id, data.requested_tier, db)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to cascade provisioning to sub-org {sub_org.id}: {e}")
    
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

