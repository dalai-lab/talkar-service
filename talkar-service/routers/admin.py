from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from db.session import get_db
from db.models import Customer, Wallet, WalletTransaction, CallLog, Agent, TalkarAdmin, Subscription
from services import razorpay_client, notification_service
from services.admin_auth import get_current_admin, create_admin_access_token
from pydantic import BaseModel
from typing import Optional
from config import WALLET_ACTIVATION_THRESHOLD_PAISE

router = APIRouter()

class AdminLoginRequest(BaseModel):
    email: str
    password: str

class ApproveApplicationRequest(BaseModel):
    integration_fee_paise: int = 0
    integration_description: str = ""

class RejectApplicationRequest(BaseModel):
    reason: str
    reapply_countdown_days: int = 30

class RequestInfoRequest(BaseModel):
    message: str

class CreditGrantRequest(BaseModel):
    amount_paise: int
    description: str

class AdminCreateRequest(BaseModel):
    email: str
    password_hash: str
    name: str
    role: Optional[str] = "admin"

class CustomerUpdateRequest(BaseModel):
    status: Optional[str] = None
    tier: Optional[str] = None

# --- AUTH ---

@router.post("/login")
async def admin_login(data: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TalkarAdmin).where(TalkarAdmin.email == data.email))
    admin = result.scalar_one_or_none()

    if not admin:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # bcrypt password verification
    import bcrypt
    try:
        password_valid = bcrypt.checkpw(
            data.password.encode("utf-8"),
            admin.password_hash.encode("utf-8")
        )
    except Exception:
        password_valid = False

    if not password_valid:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_admin_access_token(
        data={"sub": str(admin.id), "is_admin": True, "role": admin.role}
    )
    return {"access_token": access_token, "token_type": "bearer", "admin": {"name": admin.name, "email": admin.email}}

# --- APPLICATIONS ---

@router.get("/applications")
async def get_applications(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.status == "under_review").order_by(Customer.created_at.asc()))
    return result.scalars().all()

@router.post("/applications/{customer_id}/approve")
async def approve_application(customer_id: int, data: ApproveApplicationRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "under_review": raise HTTPException(400, "Customer is not under review")
    
    # Store the integration fee in onboarding_form JSON
    existing_form = customer.onboarding_form or {}
    existing_form["integration_fee_paise"] = data.integration_fee_paise
    existing_form["integration_description"] = data.integration_description
    customer.onboarding_form = existing_form

    if data.integration_fee_paise == 0:
        customer.status = "agent_building"
        await db.commit()
        from services.provisioning_service import run_provisioning
        await run_provisioning(customer.id, None, db)
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your Talkar Application is Approved!",
            body=f"Hi {customer.contact_name}, your application is approved! We are now building your agent."
        )
        return {"status": "agent_building"}
    else:
        customer.status = "approved"
        order = await razorpay_client.create_setup_fee_order(data.integration_fee_paise, f"setup_{customer.id}", customer.id, "custom")
        customer.setup_fee_order_id = order["id"]
        await db.commit()
        fee_display = f"₹{data.integration_fee_paise / 100:.2f}"
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your Talkar Application is Approved!",
            body=f"Hi {customer.contact_name}, your application is approved! Please complete your integration fee payment ({fee_display}) to get started. Your payment link will appear on your dashboard."
        )
        return {"status": "approved", "setup_fee_order_id": order["id"]}

@router.post("/applications/{customer_id}/reject")
async def reject_application(customer_id: int, data: RejectApplicationRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    customer.status = "rejected"
    # Store rejection reason and reapply countdown in onboarding_form
    existing_form = customer.onboarding_form or {}
    existing_form["rejection_reason"] = data.reason
    existing_form["reapply_countdown_days"] = data.reapply_countdown_days
    customer.onboarding_form = existing_form
    await db.commit()
    
    # Send rejection email to customer
    await notification_service.notify_customer_rejected(customer.id, data.reason)
    return {"status": "rejected"}

@router.post("/applications/{customer_id}/request-info")
async def request_info(customer_id: int, data: RequestInfoRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    # Send email to customer
    await notification_service.send_email(
        to_email=customer.contact_email,
        subject="Additional Information Required for Your Talkar Application",
        body=data.message
    )
    return {"status": "info_requested"}

# --- CUSTOMERS ---

@router.get("/customers")
async def get_all_customers(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).order_by(Customer.created_at.desc()))
    return result.scalars().all()

@router.get("/customers/{customer_id}")
async def get_customer(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    return customer

@router.patch("/customers/{customer_id}")
async def update_customer(customer_id: int, data: CustomerUpdateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if data.status: customer.status = data.status
    if data.tier:
        from config import TIER_CONFIG
        tier_cfg = TIER_CONFIG.get(data.tier)
        if not tier_cfg: raise HTTPException(400, "Invalid tier")

        # Update subscription record
        sub = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
        sub = sub.scalar_one_or_none()
        if sub:
            sub.plan = data.tier
            sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]

        # Store new tier in onboarding_form so provisioning picks it up
        existing_form = customer.onboarding_form or {}
        existing_form["approved_tier"] = data.tier
        existing_form.pop("tier_upgrade_requested", None)
        existing_form.pop("tier_upgrade_requested_at", None)
        customer.onboarding_form = dict(existing_form)

        await db.commit()

        # Re-run provisioning to update Dograh org config (LLM model, TTS, limits)
        if customer.status in ("active", "agent_building"):
            from services.provisioning_service import run_provisioning
            try:
                await run_provisioning(customer_id, None, db)
            except Exception as e:
                # Don't fail the whole request — tier is saved, provisioning can be retried
                import logging
                logging.getLogger(__name__).error(f"Re-provisioning failed after tier upgrade: {e}")
    else:
        await db.commit()

    return {"status": "success"}

class UpdateAgentRateRequest(BaseModel):
    per_minute_rate_paise: Optional[int] = None

@router.get("/customers/{customer_id}/agents")
async def get_customer_agents(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Agent).where(Agent.customer_id == customer_id))
    return result.scalars().all()

@router.patch("/customers/{customer_id}/agents/{agent_id}/rate")
async def update_agent_rate(customer_id: int, agent_id: int, data: UpdateAgentRateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.customer_id == customer_id))
    agent = result.scalar_one_or_none()
    if not agent: raise HTTPException(404, "Agent not found for this customer")
    agent.per_minute_rate_paise = data.per_minute_rate_paise
    await db.commit()
    return {"status": "ok", "per_minute_rate_paise": agent.per_minute_rate_paise}


@router.post("/customers/{customer_id}/credit")
async def manual_credit_grant(customer_id: int, data: CreditGrantRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer_id)
    if not wallet: raise HTTPException(404, "Wallet not found")

    wallet.balance_paise += data.amount_paise
    
    # Store transaction
    txn = WalletTransaction(
        customer_id=master_id,
        type="manual_credit",
        amount_paise=data.amount_paise,
        description=data.description
    )
    db.add(txn)
    
    await db.commit()
    return {"status": "success", "new_balance_paise": wallet.balance_paise}

class AdminDeductRequest(BaseModel):
    amount_paise: int
    reason: str

@router.post("/customers/{customer_id}/deduct")
async def admin_deduct_customer(customer_id: int, data: AdminDeductRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if data.amount_paise <= 0: raise HTTPException(400, "Amount must be positive")
    
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer_id)
    if not wallet: raise HTTPException(404, "Wallet not found")
    
    # Deduct balance
    result = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == master_id)
        .values(balance_paise=Wallet.balance_paise - data.amount_paise)
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
    # Record transaction
    transaction = WalletTransaction(
        customer_id=master_id,
        type="manual_deduct",
        amount_paise=-data.amount_paise,
        description=f"Admin deduction: {data.reason} (by {current_admin.email})"
    )
    db.add(transaction)
    await db.commit()

    if wallet.balance_paise < 0:
        import logging
        logging.getLogger(__name__).warning(f"Customer {customer_id} wallet went negative after admin deduct: {wallet.balance_paise}")

    return {"status": "success", "new_balance_paise": wallet.balance_paise}

@router.post("/customers/{customer_id}/deny-tier-upgrade")
async def deny_tier_upgrade(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if customer.onboarding_form and "tier_upgrade_requested" in customer.onboarding_form:
        existing_form = dict(customer.onboarding_form)
        existing_form.pop("tier_upgrade_requested", None)
        existing_form.pop("tier_upgrade_requested_at", None)
        customer.onboarding_form = existing_form
        await db.commit()
    
    return {"status": "success"}

@router.post("/customers/{customer_id}/suspend")
async def suspend_customer(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    customer.status = "suspended"
    await db.commit()
    return {"status": "suspended"}

@router.post("/customers/{customer_id}/provision/retry")
async def retry_provisioning(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status not in ("approved", "agent_building"):
        raise HTTPException(400, "Customer is not in a provisionable state")
    from services.provisioning_service import run_provisioning
    try:
        await run_provisioning(customer_id)
        return {"status": "provisioning_complete"}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to retry provisioning for customer {customer_id}: {e}")
        raise HTTPException(500, f"Provisioning trigger failed: {str(e)}")

# --- SUPPORT REQUESTS ---

class SupportRequestUpdate(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None

@router.get("/support-requests")
async def get_all_support_requests(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    from db.models import SupportRequest
    # Need to join with Customer to get company name and email
    query = select(SupportRequest, Customer).join(Customer, SupportRequest.customer_id == Customer.id).order_by(SupportRequest.created_at.desc())
    result = await db.execute(query)
    
    response = []
    for req, customer in result.all():
        data = {
            "id": req.id,
            "type": req.type,
            "subject": req.subject,
            "description": req.description,
            "status": req.status,
            "admin_note": req.admin_note,
            "created_at": req.created_at,
            "customer": {
                "id": customer.id,
                "company_name": customer.company_name,
                "contact_email": customer.contact_email,
                "dograh_org_id": customer.dograh_org_id
            }
        }
        response.append(data)
    return response

@router.patch("/support-requests/{req_id}/resolve")
async def update_support_request(req_id: int, data: SupportRequestUpdate, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    from db.models import SupportRequest
    result = await db.execute(select(SupportRequest).where(SupportRequest.id == req_id))
    req = result.scalar_one_or_none()
    if not req: raise HTTPException(404, "Support request not found")
    
    if data.status:
        req.status = data.status
        if data.status in ["resolved", "closed"]:
            req.resolved_at = func.now()
            req.resolved_by = current_admin.name
    if data.admin_note is not None:
        req.admin_note = data.admin_note
        
    await db.commit()
    return {"status": "success"}

# --- BUILD QUEUE ---

@router.get("/build-queue")
async def get_build_queue(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.status == "agent_building").order_by(Customer.created_at.asc()))
    return result.scalars().all()

@router.patch("/build-queue/{customer_id}/assign")
async def assign_build(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    import httpx
    from config import settings
    
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.DOGRAH_API_URL}/api/v1/superuser/impersonate",
            headers={
                "X-API-Key": settings.DOGRAH_ADMIN_TOKEN,
                "Content-Type": "application/json"
            },
            json={"user_id": customer.dograh_user_id}
        )
        if response.status_code != 200:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Impersonation failed: {response.text}")
            raise HTTPException(500, f"Failed to generate impersonation link. Dograh error: {response.text}")
            
        data = response.json()
        
    return {
        "status": "assigned",
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token")
    }

@router.patch("/build-queue/{customer_id}/ready")
async def mark_ready(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "agent_building": raise HTTPException(400, "Customer is not in agent_building state")
    
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer_id))
    wallet = wallet_res.scalar_one_or_none()
    
    if wallet and wallet.balance_paise >= WALLET_ACTIVATION_THRESHOLD_PAISE:
        customer.status = "pending_plan_selection"
        await db.commit()
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your AI Agent is Ready! Choose a plan",
            body=f"Hi {customer.contact_name}, your Talkar AI agent is live! Log in to choose your plan and activate."
        )
        return {"status": "pending_plan_selection"}
    else:
        customer.status = "pending_deposit"
        await db.commit()
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your AI Agent is Ready! Activate your wallet",
            body=f"Hi {customer.contact_name}, your Talkar AI agent is live! Add ₹2000 to your wallet to activate it."
        )
        return {"status": "pending_deposit"}

# --- WALLET / STATS ---

@router.get("/wallet/overview")
async def get_wallet_overview(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(func.sum(Wallet.balance_paise)))
    total_balance = result.scalar() or 0
    return {"total_platform_balance_paise": total_balance}

@router.get("/wallet/alerts")
async def get_wallet_alerts(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Yellow alert: balance < ₹500 (50000 paise) — low but not zero
    yellow_result = await db.execute(
        select(Wallet).where(
            Wallet.balance_paise > 0,
            Wallet.balance_paise < 50000
        ).order_by(Wallet.balance_paise.asc())
    )
    yellow = yellow_result.scalars().all()

    # Red/urgent alert: balance = 0, calls blocked
    red_result = await db.execute(
        select(Wallet).where(Wallet.balance_paise <= 0).order_by(Wallet.updated_at.asc())
    )
    red = red_result.scalars().all()

    return {"low_balance": yellow, "zero_balance": red}

@router.get("/calls/active")
async def get_active_calls(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Stub reading from Dograh Redis
    return {"active_calls": 0}

@router.get("/calls/stats")
async def get_calls_stats(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Stub call stats per customer
    return []

@router.get("/profitability")
async def get_profitability(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Stub profitability calculation
    return {"revenue": 0, "cost": 0, "margin": 0}

# --- TEAM ---

@router.get("/team")
async def get_team(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(TalkarAdmin))
    return result.scalars().all()

@router.post("/team")
async def add_team_member(data: AdminCreateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    admin = TalkarAdmin(
        email=data.email,
        password_hash=data.password_hash,
        name=data.name,
        role=data.role
    )
    db.add(admin)
    await db.commit()
    return {"status": "success", "admin_id": admin.id}

@router.delete("/team/{admin_id}")
async def remove_team_member(admin_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(TalkarAdmin).where(TalkarAdmin.id == admin_id))
    admin = result.scalar_one_or_none()
    if admin:
        await db.delete(admin)
        await db.commit()
    return {"status": "deleted"}

# --- PHONE NUMBERS ---
from db.models import PhoneNumberRequest, PhoneNumber

class AssignPhoneNumberRequest(BaseModel):
    number: str
    plivo_number_id: str

class ApprovePhoneNumberRequestBody(BaseModel):
    numbers: list[str]

class DenyPhoneNumberRequestBody(BaseModel):
    admin_note: str

@router.get("/customers/{customer_id}/phone-numbers")
async def get_customer_phone_numbers(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(PhoneNumber).where(PhoneNumber.customer_id == customer_id))
    return result.scalars().all()

@router.post("/customers/{customer_id}/assign-phone-number")
async def assign_phone_number(customer_id: int, data: AssignPhoneNumberRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    pn = PhoneNumber(
        customer_id=customer_id,
        number=data.number,
        plivo_number_id=data.plivo_number_id
    )
    db.add(pn)
    await db.commit()
    return {"status": "assigned"}

@router.get("/phone-number-requests")
async def get_phone_number_requests(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(PhoneNumberRequest).order_by(PhoneNumberRequest.requested_at.desc()))
    return result.scalars().all()

@router.patch("/phone-number-requests/{request_id}/approve")
async def approve_phone_number_request(request_id: int, data: ApprovePhoneNumberRequestBody, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(PhoneNumberRequest).where(PhoneNumberRequest.id == request_id))
    req = result.scalar_one_or_none()
    if not req: raise HTTPException(404, "Request not found")
    
    req.status = "approved"
    req.resolved_at = func.now()
    
    for number in data.numbers:
        pn = PhoneNumber(
            customer_id=req.customer_id,
            number=number,
        )
        db.add(pn)
        
    await db.commit()
    return {"status": "approved"}

@router.patch("/phone-number-requests/{request_id}/deny")
async def deny_phone_number_request(request_id: int, data: DenyPhoneNumberRequestBody, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(PhoneNumberRequest).where(PhoneNumberRequest.id == request_id))
    req = result.scalar_one_or_none()
    if not req: raise HTTPException(404, "Request not found")
    
    req.status = "denied"
    req.admin_note = data.admin_note
    req.resolved_at = func.now()
    await db.commit()
    return {"status": "denied"}
