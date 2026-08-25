from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from db.session import get_db
from db.models import Customer, Wallet, WalletTransaction, CallLog, Agent, TalkarAdmin, Subscription
from services import razorpay_client, notification_service
from services.admin_auth import get_current_admin, create_admin_access_token
from pydantic import BaseModel
from typing import Optional
from config import CALL_BLOCK_THRESHOLD_PAISE

router = APIRouter()

class AdminLoginRequest(BaseModel):
    email: str
    password: str

class ApproveApplicationRequest(BaseModel):
    integration_fee_paise: int = 0
    integration_description: str = ""
    approved_tier: str = "starter"

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
    result = await db.execute(
        select(Customer)
        .where(Customer.status.in_(["under_review", "pending_approval"]))
        # Exclude auto-created sub-org placeholder records that have no brief form yet.
        # These are just workspace-hook artifacts — the customer hasn't submitted their
        # brief yet. Only show them once they have an onboarding form.
        .where(
            (Customer.billing_org_id == None) |  # master orgs always show  # noqa: E711
            (Customer.onboarding_form != None)   # sub-orgs only if they have a form  # noqa: E711
        )
        .order_by(Customer.created_at.asc())
    )
    return result.scalars().all()

@router.post("/applications/{customer_id}/approve")
async def approve_application(customer_id: int, data: ApproveApplicationRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status not in ("under_review", "pending_approval"): raise HTTPException(400, "Customer is not under review or pending approval")
    
    # Store the integration fee in onboarding_form JSON
    existing_form = customer.onboarding_form or {}
    existing_form["integration_fee_paise"] = data.integration_fee_paise
    existing_form["integration_description"] = data.integration_description
    customer.onboarding_form = existing_form

    if data.integration_fee_paise == 0:
        customer.status = "agent_building"
        await db.commit()
        from services.provisioning_service import run_provisioning
        await run_provisioning(customer.id, data.approved_tier, db)

        # If this is a master org, also handle any auto-created sub-org placeholders
        # that the workspace hook made while the master was under review.
        # Sub-orgs with no brief form are just artifacts - leave status as pending_approval
        # but they will show the brief form to the user (not the admin queue should be clean).
        # Sub-orgs WITH a brief form remain pending_approval for separate admin review.

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
    if customer.status not in ("under_review", "pending_approval"): raise HTTPException(400, "Customer is not under review or pending approval")
    
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
    customer.status = "info_requested"
    await db.commit()
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

@router.post("/customers/{customer_id}/impersonate")
async def impersonate_customer(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
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
        "status": "impersonated",
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token")
    }

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

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(customer, "onboarding_form")

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
                
        # Cascade to sub-orgs (Risk 3)
        sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
        for sub_org in sub_orgs_res.scalars().all():
            sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
            sub_sub = sub_sub_res.scalar_one_or_none()
            if sub_sub:
                sub_sub.plan = data.tier
                sub_sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]
                
            sub_form = sub_org.onboarding_form or {}
            sub_form["approved_tier"] = data.tier
            sub_org.onboarding_form = dict(sub_form)
            flag_modified(sub_org, "onboarding_form")
            
            await db.commit()
            if sub_org.status == "active":
                try:
                    from services.provisioning_service import run_provisioning
                    await run_provisioning(sub_org.id, data.tier, db)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to cascade admin provisioning to sub-org {sub_org.id}: {e}")
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

    result = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == master_id)
        .values(balance_paise=Wallet.balance_paise + data.amount_paise)
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
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
    # Block calls in Dograh immediately
    if customer.dograh_org_id:
        from services import dograh_client
        try:
            await dograh_client.block_org_calls(customer.dograh_org_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to block calls for org {customer.dograh_org_id}: {e}")
    return {"status": "suspended"}

@router.post("/customers/{customer_id}/provision/retry")
async def retry_provisioning(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status not in ("approved", "agent_building", "active", "suspended", "pending_plan_selection"):
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
    
    from services.billing_service import get_billing_wallet
    wallet, _ = await get_billing_wallet(db, customer.id)
    
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    tier_name = sub.plan if sub else "starter"
    from config import TIER_CONFIG
    activation_min = TIER_CONFIG.get(tier_name, TIER_CONFIG["starter"]).get("activation_deposit_paise", 600000)
    is_funded = wallet and wallet.balance_paise >= activation_min

    if customer.billing_org_id:
        # Sub-org fast path
        if is_funded:
            customer.status = "active"
            await db.commit()
            
            # Sub-orgs inherit the tier, so just run provisioning
            from services.provisioning_service import run_provisioning
            await run_provisioning(customer.id, None, db)
            
            await notification_service.send_email(
                to_email=customer.contact_email,
                subject="Your New AI Agent is Live!",
                body=f"Hi {customer.contact_name}, your new Talkar AI agent is fully active!"
            )
            return {"status": "active"}
        else:
            # Fall back to pending_deposit if master wallet is drained
            customer.status = "pending_deposit"
            await db.commit()
            await notification_service.send_email(
                to_email=customer.contact_email,
                subject="Your New AI Agent is Ready! Top up required",
                body=f"Hi {customer.contact_name}, your new Talkar AI agent is built! Your master wallet balance is low. Please add credits to activate it."
            )
            return {"status": "pending_deposit"}

    # Normal master flow
    if is_funded:
        customer.status = "active"
        await db.commit()
        from services.provisioning_service import run_provisioning
        await run_provisioning(customer.id, None, db)
        
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your AI Agent is Live!",
            body=f"Hi {customer.contact_name}, your Talkar AI agent is fully live and ready to take calls!"
        )
        return {"status": "active"}
    else:
        customer.status = "pending_deposit"
        await db.commit()
        
        # Provision the agent so AI keys are injected and it's testable/configurable
        from services.provisioning_service import run_provisioning
        from services import dograh_client
        await run_provisioning(customer.id, None, db)
        
        # Immediately block calls (CONCURRENT_CALL_LIMIT=0) until they pay the deposit
        if customer.dograh_org_id:
            await dograh_client.block_org_calls(customer.dograh_org_id)
            
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your AI Agent is Ready! (Activation Deposit Required)",
            body=f"Hi {customer.contact_name}, your Talkar AI agent has been built! Please log in to your dashboard and add the minimum activation balance to take your agent live."
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
    if admin_id == current_admin.id:
        raise HTTPException(400, "Cannot delete your own admin account")
        
    count_result = await db.execute(select(func.count(TalkarAdmin.id)))
    admin_count = count_result.scalar_one()
    if admin_count <= 1:
        raise HTTPException(400, "Cannot delete the last admin account")

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


# ---------------------------------------------------------------------------
# PROFITABILITY DASHBOARD
# ---------------------------------------------------------------------------
# Cost constants — update these when provider rates change
USD_TO_INR = 95.7

# Plivo telephony: flat ₹0.60/min outbound India
PLIVO_COST_PER_MIN_INR = 0.60

# Deepgram STT: Nova-3 Mono PAYG $0.0048/min
DEEPGRAM_STT_RATE_USD_PER_MIN = 0.0048

# Deepgram Aura TTS: ~$0.015 per 1000 chars
DEEPGRAM_TTS_RATE_USD_PER_1K_CHARS = 0.015

# ElevenLabs TTS: ~$0.18 per 1000 chars (creator tier)
ELEVENLABS_TTS_RATE_USD_PER_1K_CHARS = 0.18

# Smallest AI Waves TTS: ~$0.004 per 1000 chars (lightning-v3.1)
# Ref: https://smallest.ai/pricing
SMALLEST_AI_TTS_RATE_USD_PER_1K_CHARS = 0.004

# AI speaking ratio — fraction of call time AI is synthesizing voice
TTS_SPEAKING_RATIO = 0.47   # ~47%, derived from real transcript analysis
# Average chars per minute of speech (from transcript counting)
TTS_AVG_CHARS_PER_MIN = 900

# OpenAI pricing (USD per 1M tokens)
OPENAI_RATES = {
    "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.60},
    "gpt-4o":      {"input": 2.50, "cached": 1.25,  "output": 10.00},
}
OPENAI_DEFAULT_RATES = OPENAI_RATES["gpt-4o-mini"]


def _estimate_call_cost_inr(
    duration_seconds: int,
    usage_info: dict | None,
    tts_provider: str = "deepgram",
    llm_model: str = "gpt-4o-mini",
) -> dict:
    """
    Estimate the real AI+telephony cost for a single call in INR.
    Returns a dict with per-service breakdown and a total.
    """
    minutes = duration_seconds / 60.0
    ui = usage_info or {}

    # --- 1. Plivo telephony ---
    plivo_inr = PLIVO_COST_PER_MIN_INR * minutes

    # --- 2. STT (Deepgram) ---
    stt_seconds = 0.0
    llm_data = ui.get("llm", {})
    stt_data = ui.get("stt", {})
    tts_data = ui.get("tts", {})

    for val in stt_data.values():
        if isinstance(val, (int, float)):
            stt_seconds += float(val)
        elif isinstance(val, dict):
            stt_seconds += float(val.get("audio_seconds", 0))

    stt_minutes = stt_seconds / 60.0 if stt_seconds else minutes  # fallback to call duration
    stt_inr = DEEPGRAM_STT_RATE_USD_PER_MIN * stt_minutes * USD_TO_INR

    # --- 3. TTS ---
    tts_chars = 0
    for val in tts_data.values():
        if isinstance(val, (int, float)):
            tts_chars += int(val)
        elif isinstance(val, dict):
            tts_chars += int(val.get("characters", 0))

    if tts_chars == 0:
        # Estimate from call duration + speaking ratio
        tts_chars = int(minutes * TTS_SPEAKING_RATIO * TTS_AVG_CHARS_PER_MIN)

    if "elevenlabs" in tts_provider.lower():
        tts_rate = ELEVENLABS_TTS_RATE_USD_PER_1K_CHARS
    elif "smallest" in tts_provider.lower():
        tts_rate = SMALLEST_AI_TTS_RATE_USD_PER_1K_CHARS
    else:
        tts_rate = DEEPGRAM_TTS_RATE_USD_PER_1K_CHARS

    tts_inr = (tts_chars / 1000.0) * tts_rate * USD_TO_INR

    # --- 4. LLM (OpenAI) ---
    rates = OPENAI_RATES.get(llm_model, OPENAI_DEFAULT_RATES)
    prompt_tokens = 0
    completion_tokens = 0
    cached_tokens = 0

    for key, val in llm_data.items():
        if key.startswith("QAAnalysis"):
            continue
        if isinstance(val, dict):
            prompt_tokens += val.get("prompt_tokens", 0)
            completion_tokens += val.get("completion_tokens", 0)
            cached_tokens += val.get("cache_read_input_tokens", 0)

    non_cached = max(prompt_tokens - cached_tokens, 0)
    llm_usd = (
        (non_cached / 1_000_000) * rates["input"]
        + (cached_tokens / 1_000_000) * rates["cached"]
        + (completion_tokens / 1_000_000) * rates["output"]
    )
    llm_inr = llm_usd * USD_TO_INR

    total_inr = plivo_inr + stt_inr + tts_inr + llm_inr

    return {
        "plivo_inr": round(plivo_inr, 4),
        "stt_inr": round(stt_inr, 4),
        "tts_inr": round(tts_inr, 4),
        "llm_inr": round(llm_inr, 4),
        "total_cost_inr": round(total_inr, 4),
        "tts_chars": tts_chars,
        "stt_seconds": round(stt_seconds, 1),
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
        "llm_cached_tokens": cached_tokens,
    }


@router.get("/profitability")
async def get_profitability(
    period: str = "month",   # "today" | "week" | "month" | "all"
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    """
    Returns platform-wide and per-customer profitability breakdown.
    Revenue = what we billed clients (cost_to_customer_paise in call_logs).
    Cost = estimated AI + telephony cost calculated from usage_info.
    Profit = Revenue - Cost.
    """
    import math
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text

    # --- Date filter ---
    now = datetime.now(timezone.utc)
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - timedelta(days=7)
    elif period == "month":
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        since = None

    from services.dograh_client import DograhSessionLocal
    import logging as _logging
    _log = _logging.getLogger(__name__)

    # --- Fetch call logs ---
    run_id_list_q = select(CallLog.dograh_run_id, CallLog.customer_id,
                           CallLog.duration_seconds, CallLog.cost_to_customer_paise,
                           CallLog.called_at)
    if since:
        run_id_list_q = run_id_list_q.where(CallLog.called_at >= since)
    call_rows = (await db.execute(run_id_list_q)).all()

    # --- Fetch usage_info from Dograh DB ---
    usage_map = {}
    if call_rows:
        try:
            run_ids = [r.dograh_run_id for r in call_rows if r.dograh_run_id]
            if run_ids:
                async with DograhSessionLocal() as ddb:
                    usage_rows = (await ddb.execute(
                        text("SELECT id, usage_info FROM workflow_runs WHERE id = ANY(:ids)"),
                        {"ids": run_ids},
                    )).all()
                    usage_map = {r.id: r.usage_info for r in usage_rows}
        except Exception as e:
            _log.warning(f"Could not fetch usage_info from Dograh DB: {e}")


    # --- Fetch subscriptions for tts_provider / llm_model per customer ---
    subs_res = await db.execute(select(Subscription))
    subs = {s.customer_id: s for s in subs_res.scalars().all()}

    # --- Fetch customers (all statuses — call logs may exist for pending/suspended too) ---
    cust_res = await db.execute(select(Customer))
    customers = {c.id: c for c in cust_res.scalars().all()}

    # --- Aggregate per customer ---
    from collections import defaultdict
    per_customer: dict = defaultdict(lambda: {
        "calls": 0,
        "total_minutes": 0.0,
        "revenue_inr": 0.0,
        "cost_inr": 0.0,
        "plivo_inr": 0.0,
        "stt_inr": 0.0,
        "tts_inr": 0.0,
        "llm_inr": 0.0,
    })

    total_revenue_inr = 0.0
    total_cost_inr = 0.0

    for row in call_rows:
        cid = row.customer_id
        sub = subs.get(cid)
        tts_provider = "deepgram"
        llm_model = "gpt-4o-mini"
        if sub:
            from config import TIER_CONFIG
            tier_cfg = TIER_CONFIG.get(sub.plan, {})
            tts_provider = tier_cfg.get("tts_provider", "deepgram")
            llm_model = tier_cfg.get("llm_model", "gpt-4o-mini")

        usage_info = usage_map.get(row.dograh_run_id)
        cost_breakdown = _estimate_call_cost_inr(
            row.duration_seconds, usage_info, tts_provider, llm_model
        )
        revenue_inr = row.cost_to_customer_paise / 100.0

        per_customer[cid]["calls"] += 1
        per_customer[cid]["total_minutes"] += row.duration_seconds / 60.0
        per_customer[cid]["revenue_inr"] += revenue_inr
        per_customer[cid]["cost_inr"] += cost_breakdown["total_cost_inr"]
        per_customer[cid]["plivo_inr"] += cost_breakdown["plivo_inr"]
        per_customer[cid]["stt_inr"] += cost_breakdown["stt_inr"]
        per_customer[cid]["tts_inr"] += cost_breakdown["tts_inr"]
        per_customer[cid]["llm_inr"] += cost_breakdown["llm_inr"]

        total_revenue_inr += revenue_inr
        total_cost_inr += cost_breakdown["total_cost_inr"]

    # --- Fetch topups for the period ---
    topup_q = select(func.sum(WalletTransaction.amount_paise)).where(
        WalletTransaction.type == "top_up"
    )
    if since:
        topup_q = topup_q.where(WalletTransaction.created_at >= since)
    topup_paise = float((await db.execute(topup_q)).scalar() or 0)
    total_topups_inr = topup_paise / 100.0

    # --- Build customer rows ---
    customer_rows = []
    for cid, data in sorted(per_customer.items(), key=lambda x: -x[1]["revenue_inr"]):
        c = customers.get(cid)
        profit = data["revenue_inr"] - data["cost_inr"]
        margin_pct = (profit / data["revenue_inr"] * 100) if data["revenue_inr"] > 0 else 0
        customer_rows.append({
            "customer_id": cid,
            "company_name": c.company_name if c else f"Customer #{cid}",
            "contact_email": c.contact_email if c else None,
            "status": c.status if c else None,
            "calls": data["calls"],
            "total_minutes": round(data["total_minutes"], 1),
            "revenue_inr": round(data["revenue_inr"], 2),
            "cost_inr": round(data["cost_inr"], 2),
            "profit_inr": round(profit, 2),
            "margin_pct": round(margin_pct, 1),
            "breakdown": {
                "plivo_inr": round(data["plivo_inr"], 2),
                "stt_inr": round(data["stt_inr"], 2),
                "tts_inr": round(data["tts_inr"], 2),
                "llm_inr": round(data["llm_inr"], 2),
            },
        })

    gross_profit = total_revenue_inr - total_cost_inr
    gross_margin = (gross_profit / total_revenue_inr * 100) if total_revenue_inr > 0 else 0

    return {
        "period": period,
        "summary": {
            "total_calls": len(call_rows),
            "total_topups_inr": round(total_topups_inr, 2),
            "total_revenue_inr": round(total_revenue_inr, 2),
            "total_cost_inr": round(total_cost_inr, 2),
            "gross_profit_inr": round(gross_profit, 2),
            "gross_margin_pct": round(gross_margin, 1),
        },
        "customers": customer_rows,
        "cost_assumptions": {
            "usd_to_inr": USD_TO_INR,
            "plivo_per_min_inr": PLIVO_COST_PER_MIN_INR,
            "deepgram_stt_usd_per_min": DEEPGRAM_STT_RATE_USD_PER_MIN,
            "deepgram_tts_usd_per_1k_chars": DEEPGRAM_TTS_RATE_USD_PER_1K_CHARS,
            "elevenlabs_tts_usd_per_1k_chars": ELEVENLABS_TTS_RATE_USD_PER_1K_CHARS,
            "smallest_ai_tts_usd_per_1k_chars": SMALLEST_AI_TTS_RATE_USD_PER_1K_CHARS,
            "tts_speaking_ratio": TTS_SPEAKING_RATIO,
            "openai_rates": OPENAI_RATES,
        },
    }
