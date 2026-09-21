from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from db.session import get_db
from db.models import Customer, Wallet, WalletTransaction, CallLog, Agent, TalkarAdmin, Subscription
from services import razorpay_client, notification_service
from services.admin_auth import get_current_admin, create_admin_access_token
from pydantic import BaseModel
from typing import Optional, List
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

class SuspendCustomerRequest(BaseModel):
    reason: str = "zero_balance"  # zero_balance | policy_violation | fraud | other
    custom_message: Optional[str] = None

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

class SetCustomPricingRequest(BaseModel):
    per_minute_rate_paise: int
    concurrent_call_limit: int
    max_call_duration_seconds: int
    activation_deposit_paise: int
    llm_model: str = "gpt-4o-mini"
    tts_provider: str = "deepgram"
    stt_provider: str = "deepgram"
    free_phone_numbers: int = 1
    custom_plan_label: str = "Custom"
    trigger_reprovisioning: bool = True


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
        .where(Customer.status.in_(["under_review", "pending_approval", "info_requested"]))
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
    if customer.status not in ("under_review", "pending_approval", "info_requested"): raise HTTPException(400, "Customer is not under review, pending approval, or info requested")
    
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
    if customer.status not in ("under_review", "pending_approval", "info_requested"): raise HTTPException(400, "Customer is not under review, pending approval, or info requested")
    
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
    customers = result.scalars().all()
    for c in customers:
        if not c.company_name or not c.company_name.strip():
            c.company_name = (
                (c.onboarding_form.get("businessName") if c.onboarding_form else None)
                or (c.onboarding_form.get("company_name") if c.onboarding_form else None)
                or c.contact_name
                or (c.contact_email.split("@")[0] if c.contact_email else "Customer")
            )
    return customers

@router.get("/customers/{customer_id}")
async def get_customer(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if not customer.company_name or not customer.company_name.strip():
        customer.company_name = (
            (customer.onboarding_form.get("businessName") if customer.onboarding_form else None)
            or (customer.onboarding_form.get("company_name") if customer.onboarding_form else None)
            or customer.contact_name
            or (customer.contact_email.split("@")[0] if customer.contact_email else "Customer")
        )
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

@router.post("/customers/{customer_id}/set-custom-pricing")
async def set_custom_pricing(customer_id: int, data: SetCustomPricingRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if customer.status not in ("active", "agent_building"):
        raise HTTPException(400, "Can only set custom pricing on active or building customers.")
    
    # EC-2: Only master orgs can have custom pricing. Sub-orgs inherit from master.
    if customer.billing_org_id:
        raise HTTPException(400, "Cannot set custom pricing on a sub-org. Set it on the master org and it will cascade.")
        
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
    sub = sub_res.scalar_one_or_none()
    if not sub:
        raise HTTPException(400, "Customer has no subscription record yet. Ensure provisioning has run first.")
    
    custom_config_data = {
        "concurrent_call_limit": data.concurrent_call_limit,
        "max_call_duration_seconds": data.max_call_duration_seconds,
        "activation_deposit_paise": data.activation_deposit_paise,
        "llm_model": data.llm_model,
        "tts_provider": data.tts_provider,
        "stt_provider": data.stt_provider,
        "free_phone_numbers": data.free_phone_numbers,
    }
    
    from sqlalchemy.orm.attributes import flag_modified
    sub.plan = "custom"
    sub.per_minute_rate_paise = data.per_minute_rate_paise
    sub.custom_config = custom_config_data
    sub.custom_plan_label = data.custom_plan_label
    flag_modified(sub, "custom_config")
        
    existing_form = customer.onboarding_form or {}
    existing_form["approved_tier"] = "custom"
    existing_form.pop("tier_upgrade_requested", None)
    existing_form.pop("tier_upgrade_requested_at", None)
    customer.onboarding_form = dict(existing_form)
    flag_modified(customer, "onboarding_form")
    await db.commit()

    
    if data.trigger_reprovisioning:
        from services.provisioning_service import run_provisioning
        try:
            await run_provisioning(customer.id, None, db)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to provision custom plan for customer {customer_id}: {e}")
            
    # Cascade to sub-orgs
    sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
    for sub_org in sub_orgs_res.scalars().all():
        sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
        sub_sub = sub_sub_res.scalar_one_or_none()
        if sub_sub:
            sub_sub.plan = "custom"
            sub_sub.per_minute_rate_paise = data.per_minute_rate_paise
            sub_sub.custom_config = custom_config_data
            sub_sub.custom_plan_label = data.custom_plan_label
            
        sub_form = sub_org.onboarding_form or {}
        sub_form["approved_tier"] = "custom"
        sub_org.onboarding_form = dict(sub_form)
        flag_modified(sub_org, "onboarding_form")
        
        await db.commit()
        if sub_org.status == "active" and data.trigger_reprovisioning:
            try:
                from services.provisioning_service import run_provisioning
                await run_provisioning(sub_org.id, None, db)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to cascade custom plan provisioning to sub-org {sub_org.id}: {e}")
                
    return {"status": "success"}

@router.patch("/customers/{customer_id}")
async def update_customer(customer_id: int, data: CustomerUpdateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if data.status:
        prev_status = customer.status
        customer.status = data.status
        await db.commit()
        # If admin is manually reactivating a suspended customer, restore their call capacity in Dograh
        if prev_status == "suspended" and data.status == "active" and customer.dograh_org_id:
            from services import dograh_client
            from config import resolve_tier_config
            sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
            sub = sub_res.scalar_one_or_none()
            tier_cfg = resolve_tier_config(sub)
            concurrent_limit = tier_cfg.get("concurrent_call_limit", 2)
            tier = sub.plan if sub else "starter"
            try:
                await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to restore calls after admin unsuspend for org {customer.dograh_org_id}: {e}")
    if data.tier:
        if data.tier == "custom":
            raise HTTPException(400, "Use POST /customers/{id}/set-custom-pricing to set a custom plan.")
        from config import TIER_CONFIG
        tier_cfg = TIER_CONFIG.get(data.tier)
        if not tier_cfg: raise HTTPException(400, "Invalid tier")

        # Update subscription record
        sub = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
        sub = sub.scalar_one_or_none()
        if sub:
            sub.plan = data.tier
            sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]
            sub.custom_config = None
            sub.custom_plan_label = None

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
        if not data.status:  # only commit if status block didn't already commit
            await db.commit()

    return {"status": "success"}

class UpdateAgentRateRequest(BaseModel):
    per_minute_rate_paise: Optional[int] = None

@router.get("/customers/{customer_id}/agents")
async def get_customer_agents(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # 1. Fetch customer to get dograh_org_id
    res = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = res.scalar_one_or_none()
    
    if customer and customer.dograh_org_id:
        from services.dograh_client import DograhSessionLocal
        from sqlalchemy import text
        
        # 2. Get existing Talkar agents
        existing_res = await db.execute(select(Agent).where(Agent.customer_id == customer_id))
        existing_agents = {a.dograh_workflow_id: a for a in existing_res.scalars().all() if a.dograh_workflow_id}
        
        # 3. Sync from Dograh workflows
        try:
            async with DograhSessionLocal() as ddb:
                w_res = await ddb.execute(
                    text("SELECT id, name, status, created_at FROM workflows WHERE organization_id = :org_id"),
                    {"org_id": customer.dograh_org_id}
                )
                workflows = w_res.fetchall()
                
                for w in workflows:
                    if w.id not in existing_agents:
                        new_ag = Agent(
                            customer_id=customer.id,
                            name=w.name,
                            dograh_workflow_id=w.id,
                            dograh_org_id=customer.dograh_org_id,
                            status=w.status,
                            built_at=w.created_at
                        )
                        db.add(new_ag)
                await db.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to sync workflows for customer {customer_id}: {e}")

    # 4. Return the (now synced) agents
    result = await db.execute(select(Agent).where(Agent.customer_id == customer_id))
    return result.scalars().all()

@router.get("/customers/{customer_id}/subscription")
async def get_customer_subscription(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    """Return the subscription record for a customer, including custom_config for the admin UI."""
    result = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "No subscription found for this customer")
    return {
        "id": sub.id,
        "plan": sub.plan,
        "per_minute_rate_paise": sub.per_minute_rate_paise,
        "custom_config": getattr(sub, "custom_config", None),
        "custom_plan_label": getattr(sub, "custom_plan_label", None),
    }

@router.patch("/customers/{customer_id}/agents/{agent_id}/rate")
async def update_agent_rate(customer_id: int, agent_id: int, data: UpdateAgentRateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.customer_id == customer_id))
    agent = result.scalar_one_or_none()
    if not agent: raise HTTPException(404, "Agent not found for this customer")
    agent.per_minute_rate_paise = data.per_minute_rate_paise
    await db.commit()
    return {"status": "ok", "per_minute_rate_paise": agent.per_minute_rate_paise}

class UpdateAgentCrmLinkRequest(BaseModel):
    crm_link: Optional[str] = None

@router.patch("/customers/{customer_id}/agents/{agent_id}/crm-link")
async def update_agent_crm_link(customer_id: int, agent_id: int, data: UpdateAgentCrmLinkRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.customer_id == customer_id))
    agent = result.scalar_one_or_none()
    if not agent: raise HTTPException(404, "Agent not found for this customer")
    agent.crm_link = data.crm_link
    await db.commit()
    return {"status": "ok", "crm_link": agent.crm_link}

class CrmLinkItem(BaseModel):
    name: str = ""
    url: str

class UpdateCustomerCrmLinksRequest(BaseModel):
    crm_links: List[CrmLinkItem]

@router.patch("/customers/{customer_id}/crm-links")
async def update_customer_crm_links(
    customer_id: int,
    data: UpdateCustomerCrmLinksRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    clean_links = []
    for item in data.crm_links:
        raw_url = (item.url or "").strip()
        if not raw_url:
            continue
        if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = f"https://{raw_url}"
        display_name = (item.name or "").strip() or "CRM"
        clean_links.append({"name": display_name, "url": raw_url})

    from sqlalchemy.orm.attributes import flag_modified
    customer.crm_links = clean_links
    flag_modified(customer, "crm_links")
    await db.commit()
    return {"status": "ok", "crm_links": customer.crm_links}

class AdminTestNotificationRequest(BaseModel):
    title: str = "Test Notification"
    body: str = "This is a test notification from Talkar Admin to verify your alert delivery."
    type: str = "info"  # "info" | "success" | "warning" | "billing"
    send_email: bool = True

@router.post("/customers/{customer_id}/test-notification")
async def send_admin_test_notification(
    customer_id: int,
    data: AdminTestNotificationRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    title = data.title.strip() or "Test Notification"
    body = data.body.strip() or "This is a test notification from Talkar Admin."
    notif_type = data.type if data.type in ("info", "success", "warning", "billing") else "info"

    # 1. Push in-app alert (appears in Dograh's NotificationBell)
    await notification_service.push_notification(
        customer_id=customer.id,
        title=title,
        body=body,
        notification_type=notif_type
    )

    # 2. Optionally also dispatch email
    if data.send_email and customer.contact_email:
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject=f"[Talkar Alert] {title}",
            body=(
                f"Hi {customer.contact_name or 'there'},\n\n"
                f"{body}\n\n"
                f"If you received this message, your Talkar notifications are working properly.\n\n"
                f"The Talkar Team"
            )
        )

    return {"status": "success", "message": "Notification dispatched successfully"}


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
    
    # --- Auto-activate / restore calls (mirrors topup webhook logic) ---
    # Re-fetch wallet after commit to get fresh balance
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
    wallet = wallet_res.scalar_one_or_none()

    customer_res = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = customer_res.scalar_one_or_none()

    if customer and wallet:
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
        sub = sub_res.scalar_one_or_none()
        from config import resolve_tier_config
        tier_cfg = resolve_tier_config(sub)
        activation_threshold = tier_cfg.get("activation_deposit_paise", 600000)

        from services import dograh_client
        from services.provisioning_service import run_provisioning

        if customer.status == "pending_deposit" and wallet.balance_paise >= activation_threshold:
            customer.status = "active"
            await db.commit()
            try:
                await run_provisioning(customer.id, None, db)
            except Exception as e:
                import logging; logging.getLogger(__name__).error(f"[AdminCredit] Provisioning failed for customer {customer.id}: {e}")
            if customer.dograh_org_id:
                try:
                    tier = sub.plan if sub else "starter"
                    concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                    await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)
                except Exception as e:
                    import logging; logging.getLogger(__name__).error(f"[AdminCredit] Failed to restore calls for org {customer.dograh_org_id}: {e}")

        elif customer.status == "suspended" and wallet.balance_paise >= activation_threshold:
            customer.status = "active"
            await db.commit()
            try:
                await run_provisioning(customer.id, None, db)
            except Exception as e:
                import logging; logging.getLogger(__name__).error(f"[AdminCredit] Re-provisioning failed for customer {customer.id}: {e}")
            if customer.dograh_org_id:
                try:
                    tier = sub.plan if sub else "starter"
                    concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                    await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)
                except Exception as e:
                    import logging; logging.getLogger(__name__).error(f"[AdminCredit] Failed to restore calls for org {customer.dograh_org_id}: {e}")

        elif customer.status == "pending_deposit" and wallet.balance_paise < activation_threshold:
            # Balance still insufficient — ensure calls remain blocked
            if customer.dograh_org_id:
                try:
                    await dograh_client.block_org_calls(customer.dograh_org_id)
                except Exception as e:
                    import logging; logging.getLogger(__name__).error(f"[AdminCredit] Failed to block calls for org {customer.dograh_org_id}: {e}")

    # Notify customer of manual credit grant
    import asyncio
    asyncio.create_task(notification_service.notify_customer_credit_granted(
        customer_id=customer_id,
        amount_paise=data.amount_paise,
        description=data.description
    ))
    
    return {"status": "success", "new_balance_paise": wallet.balance_paise if wallet else None}

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
    
    # Notify customer of tier upgrade denial
    import asyncio
    requested_tier = customer.onboarding_form.get("tier_upgrade_requested", "higher") if customer.onboarding_form else "higher"
    asyncio.create_task(notification_service.notify_customer_tier_upgrade_denied(customer_id, requested_tier))
    
    return {"status": "success"}

@router.post("/customers/{customer_id}/suspend")
async def suspend_customer(customer_id: int, data: SuspendCustomerRequest = SuspendCustomerRequest(), db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    customer.status = "suspended"
    # Store suspension reason in onboarding_form for audit trail
    existing_form = dict(customer.onboarding_form or {})
    existing_form["suspension_reason"] = data.reason
    existing_form["suspension_message"] = data.custom_message or ""
    customer.onboarding_form = existing_form
    await db.commit()
    # Block calls in Dograh immediately
    if customer.dograh_org_id:
        from services import dograh_client
        try:
            await dograh_client.block_org_calls(customer.dograh_org_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to block calls for org {customer.dograh_org_id}: {e}")

    # Notify customer of suspension
    import asyncio
    asyncio.create_task(notification_service.notify_customer_suspended(customer_id, data.reason, data.custom_message))

    return {"status": "suspended"}

@router.post("/customers/{customer_id}/unsuspend")
async def unsuspend_customer(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "suspended": raise HTTPException(400, "Customer is not suspended")
    customer.status = "active"
    # Clear suspension reason from onboarding_form
    existing_form = dict(customer.onboarding_form or {})
    existing_form.pop("suspension_reason", None)
    existing_form.pop("suspension_message", None)
    customer.onboarding_form = existing_form
    await db.commit()
    # Restore calls in Dograh
    if customer.dograh_org_id:
        from services import dograh_client
        from config import resolve_tier_config
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
        sub = sub_res.scalar_one_or_none()
        tier_cfg = resolve_tier_config(sub)
        concurrent_limit = tier_cfg.get("concurrent_call_limit", 2)
        tier = sub.plan if sub else "starter"
        try:
            await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to restore calls after unsuspend for org {customer.dograh_org_id}: {e}")
    return {"status": "active"}

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
async def get_all_support_requests(
    req_type: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    from db.models import SupportRequest
    query = select(SupportRequest, Customer).join(Customer, SupportRequest.customer_id == Customer.id).order_by(SupportRequest.created_at.desc())
    if req_type and req_type != "all":
        query = query.where(SupportRequest.type == req_type)
    if status and status != "all":
        query = query.where(SupportRequest.status == status)
    if search:
        s = f"%{search.strip()}%"
        query = query.where(
            (Customer.company_name.ilike(s)) |
            (Customer.contact_email.ilike(s)) |
            (SupportRequest.subject.ilike(s)) |
            (SupportRequest.description.ilike(s))
        )
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
            "resolved_by": req.resolved_by,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
            "customer": {
                "id": customer.id,
                "company_name": customer.company_name,
                "contact_email": customer.contact_email,
                "dograh_org_id": customer.dograh_org_id
            }
        }
        response.append(data)
    return response

@router.patch("/support-requests/{req_id}")
@router.patch("/support-requests/{req_id}/resolve")
async def update_support_request(req_id: int, data: SupportRequestUpdate, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    from db.models import SupportRequest
    result = await db.execute(select(SupportRequest).where(SupportRequest.id == req_id))
    req = result.scalar_one_or_none()
    if not req: raise HTTPException(404, "Support request not found")
    
    if data.status:
        req.status = data.status
        if data.status in ["resolved", "closed", "approved", "rejected"]:
            req.resolved_at = func.now()
            req.resolved_by = current_admin.id
    if data.admin_note is not None:
        req.admin_note = data.admin_note
        
    await db.commit()
    
    # Notify customer of support ticket update (if note added or status changed)
    if data.admin_note or (data.status and data.status != req.status):
        import asyncio
        asyncio.create_task(notification_service.notify_customer_support_replied(
            customer_id=req.customer_id,
            subject=req.subject,
            admin_note=data.admin_note or "",
            status=req.status
        ))
        
    return {"status": "success", "id": req.id, "status_value": req.status}

@router.delete("/support-requests/{req_id}")
async def delete_support_request(req_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    from db.models import SupportRequest
    result = await db.execute(select(SupportRequest).where(SupportRequest.id == req_id))
    req = result.scalar_one_or_none()
    if not req: raise HTTPException(404, "Support request not found")
    
    await db.delete(req)
    await db.commit()
    return {"status": "deleted", "id": req_id}

# --- BUILD QUEUE ---

@router.get("/build-queue")
async def get_build_queue(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.status == "agent_building").order_by(Customer.created_at.asc()))
    customers = result.scalars().all()
    customer_ids = [c.id for c in customers]
    phone_map = {}
    if customer_ids:
        from db.models import PhoneNumber
        pn_result = await db.execute(select(PhoneNumber).where(PhoneNumber.customer_id.in_(customer_ids)))
        for pn in pn_result.scalars().all():
            phone_map.setdefault(pn.customer_id, []).append(pn.number)
            
    return [
        {
            "id": c.id,
            "company_name": c.company_name,
            "industry": c.industry,
            "contact_name": c.contact_name,
            "contact_email": c.contact_email,
            "contact_phone": c.contact_phone,
            "status": c.status,
            "onboarding_form": c.onboarding_form,
            "documents": c.documents,
            "billing_org_id": c.billing_org_id,
            "dograh_org_id": c.dograh_org_id,
            "dograh_user_id": c.dograh_user_id,
            "setup_fee_order_id": c.setup_fee_order_id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "phone_numbers": phone_map.get(c.id, []),
        }
        for c in customers
    ]

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

class MarkReadyRequest(BaseModel):
    custom_message: Optional[str] = None
    override_agent_name: Optional[str] = None
    override_phone_number: Optional[str] = None

@router.patch("/build-queue/{customer_id}/ready")
async def mark_ready(customer_id: int, data: MarkReadyRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
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

    from db.models import PhoneNumber
    pn_res = await db.execute(select(PhoneNumber).where(PhoneNumber.customer_id == customer.id))
    phone_numbers = pn_res.scalars().all()
    default_phones_str = ", ".join([pn.number for pn in phone_numbers]) if phone_numbers else "No phone number assigned yet (Please check your dashboard)."
    
    phones_str = (data.override_phone_number or "").strip() or default_phones_str
    agent_name = (data.override_agent_name or "").strip() or customer.company_name
    admin_note = f"\n\nAdmin Note:\n{data.custom_message.strip()}" if data.custom_message and data.custom_message.strip() else ""

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
                subject="Your New AI Agent is Live! 🚀",
                body=f"Hi {customer.contact_name},\n\nGreat news! Your Talkar AI agent for {agent_name} is fully built and ready to take calls.\n\nYou can test your live agent right now by calling:\n📞 {phones_str}{admin_note}\n\nLog in to your dashboard at talkar.in to view call logs and configure settings.\n\nBest,\nThe Talkar Team"
            )
            return {"status": "active"}
        else:
            # Fall back to pending_deposit if master wallet is drained
            customer.status = "pending_deposit"
            await db.commit()
            await notification_service.send_email(
                to_email=customer.contact_email,
                subject="Your New AI Agent is Ready! 🚀 (Top up required)",
                body=f"Hi {customer.contact_name},\n\nGreat news! Your Talkar AI agent for {agent_name} is fully built. Your master wallet balance is low, please add credits to activate it.\n\nOnce activated, you can call:\n📞 {phones_str}{admin_note}\n\nBest,\nThe Talkar Team"
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
            subject="Your AI Agent is Live! 🚀",
            body=f"Hi {customer.contact_name},\n\nGreat news! Your Talkar AI agent for {agent_name} is fully live and ready to take calls.\n\nYou can test your live agent right now by calling:\n📞 {phones_str}{admin_note}\n\nLog in to your dashboard at talkar.in to view call logs and configure settings.\n\nBest,\nThe Talkar Team"
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
            subject="Your AI Agent is Ready! 🚀 (Activation Deposit Required)",
            body=f"Hi {customer.contact_name},\n\nGreat news! Your Talkar AI agent for {agent_name} has been built! Please log in to your dashboard and add the minimum activation balance to take your agent live.\n\nOnce activated, you can call:\n📞 {phones_str}{admin_note}\n\nBest,\nThe Talkar Team"
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
        select(Wallet, Customer).join(Customer, Wallet.customer_id == Customer.id).where(
            Wallet.balance_paise > 0,
            Wallet.balance_paise < 50000
        ).order_by(Wallet.balance_paise.asc())
    )
    yellow = []
    for w, c in yellow_result.all():
        d = {col.name: getattr(w, col.name) for col in w.__table__.columns}
        d["company_name"] = c.company_name
        d["contact_email"] = c.contact_email
        yellow.append(d)

    # Red/urgent alert: balance = 0, calls blocked
    red_result = await db.execute(
        select(Wallet, Customer).join(Customer, Wallet.customer_id == Customer.id).where(Wallet.balance_paise <= 0).order_by(Wallet.updated_at.asc())
    )
    red = []
    for w, c in red_result.all():
        d = {col.name: getattr(w, col.name) for col in w.__table__.columns}
        d["company_name"] = c.company_name
        d["contact_email"] = c.contact_email
        red.append(d)

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
    
    import asyncio
    asyncio.create_task(notification_service.notify_customer_phone_approved(req.customer_id, data.numbers))
    
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
    
    import asyncio
    asyncio.create_task(notification_service.notify_customer_phone_denied(req.customer_id, data.admin_note))
    
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

# Smallest AI Waves TTS: $0.175 per 10k chars = $0.0175 per 1k chars (Lightning v3.1)
# Ref: https://smallest.ai/pricing (Aug 2026)
SMALLEST_AI_TTS_RATE_USD_PER_1K_CHARS = 0.0175

# Smallest AI Pulse STT: ~$0.003/min (kept for reference — no current plan uses it;
# all plans including growth use Deepgram for STT)
SMALLEST_AI_STT_RATE_USD_PER_MIN = 0.003

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
    stt_provider: str = "deepgram",
    llm_model: str = "gpt-4o-mini",
    overrides: dict | None = None,
) -> dict:
    """
    Estimate the real AI+telephony cost for a single call in INR.
    Returns a dict with per-service breakdown and a total.
    """
    minutes = duration_seconds / 60.0
    ui = usage_info or {}
    overrides = overrides or {}

    # --- 1. Plivo telephony ---
    plivo_rate_inr = overrides.get("telephony_cost_per_min_inr", PLIVO_COST_PER_MIN_INR)
    if plivo_rate_inr is None or str(plivo_rate_inr) == "":
        plivo_rate_inr = PLIVO_COST_PER_MIN_INR
    plivo_inr = float(plivo_rate_inr) * minutes

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

    # --- 3b. STT rate — use the explicit stt_provider, not inferred from TTS ---
    if "smallest" in stt_provider.lower():
        stt_inr = SMALLEST_AI_STT_RATE_USD_PER_MIN * stt_minutes * USD_TO_INR
    else:
        stt_inr = DEEPGRAM_STT_RATE_USD_PER_MIN * stt_minutes * USD_TO_INR

    # --- 4. LLM (OpenAI) ---
    rates = OPENAI_RATES.get(llm_model, OPENAI_DEFAULT_RATES)
    prompt_tokens = 0
    completion_tokens = 0
    cached_tokens = 0

    for key, val in llm_data.items():
        if key.startswith("QAAnalysis"):
            continue
        if isinstance(val, dict):
            prompt_tokens += (val.get("prompt_tokens") or 0)
            completion_tokens += (val.get("completion_tokens") or 0)
            cached_tokens += (val.get("cache_read_input_tokens") or 0)

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
    from db.models import GlobalPlatformSettings

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
                           CallLog.called_at, CallLog.plan, CallLog.tts_provider)
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

    # --- Fetch global platform settings ---
    global_res = await db.execute(select(GlobalPlatformSettings).limit(1))
    global_settings_row = global_res.scalar_one_or_none()
    global_overrides = global_settings_row.settings.get("profitability_overrides", {}) if global_settings_row else {}

    # --- Fetch non-test customers ---
    cust_res = await db.execute(select(Customer).where(Customer.is_test_account == False))
    customers = {c.id: c for c in cust_res.scalars().all()}

    # --- Aggregate per customer AND per plan bucket ---
    from collections import defaultdict

    def empty_bucket():
        return {"calls": 0, "total_minutes": 0.0, "revenue_inr": 0.0,
                "cost_inr": 0.0, "plivo_inr": 0.0, "stt_inr": 0.0,
                "tts_inr": 0.0, "llm_inr": 0.0}

    # keyed by customer_id → totals
    per_customer: dict = defaultdict(empty_bucket)
    # keyed by (customer_id, plan, tts_provider) → per-plan bucket
    per_plan: dict = defaultdict(empty_bucket)

    total_revenue_inr = 0.0
    total_cost_inr = 0.0

    for row in call_rows:
        cid = row.customer_id
        if cid not in customers:
            continue
        c_obj = customers[cid]
        c_overrides = c_obj.report_settings.get("profitability_overrides", {})
        
        # Merge hierarchy: Base defaults -> Global Overrides -> Customer Overrides
        effective_overrides = {}
        effective_overrides.update(global_overrides)
        effective_overrides.update(c_overrides)

        # Use the plan stamped on the call_log at billing time (accurate for mid-month
        # plan switches). Fall back to current subscription if column is NULL (old rows).
        stamped_plan = getattr(row, "plan", None)
        stamped_tts = getattr(row, "tts_provider", None)

        if stamped_plan and stamped_tts:
            tts_provider = stamped_tts
            from config import TIER_CONFIG, resolve_tier_config
            if stamped_plan == "custom":
                sub = subs.get(cid)
                tier_cfg = resolve_tier_config(sub) if sub else {}
            else:
                tier_cfg = TIER_CONFIG.get(stamped_plan, {})
            llm_model = tier_cfg.get("llm_model", "gpt-4o-mini")
            stt_provider = tier_cfg.get("stt_provider", "deepgram")
            active_plan = stamped_plan
        else:
            # Legacy rows: fall back to current subscription
            sub = subs.get(cid)
            tts_provider = "deepgram"
            stt_provider = "deepgram"
            llm_model = "gpt-4o-mini"
            active_plan = "starter"
            if sub:
                from config import TIER_CONFIG
                tier_cfg = TIER_CONFIG.get(sub.plan, {})
                tts_provider = tier_cfg.get("tts_provider", "deepgram")
                stt_provider = tier_cfg.get("stt_provider", "deepgram")
                llm_model = tier_cfg.get("llm_model", "gpt-4o-mini")
                active_plan = sub.plan

        usage_info = usage_map.get(row.dograh_run_id)
        cost_breakdown = _estimate_call_cost_inr(
            row.duration_seconds, usage_info, tts_provider, stt_provider, llm_model, effective_overrides
        )
        revenue_inr = row.cost_to_customer_paise / 100.0

        plan_key = (cid, active_plan, tts_provider)

        for bucket in [per_customer[cid], per_plan[plan_key]]:
            bucket["calls"] += 1
            bucket["total_minutes"] += row.duration_seconds / 60.0
            bucket["revenue_inr"] += revenue_inr
            bucket["cost_inr"] += cost_breakdown["total_cost_inr"]
            bucket["plivo_inr"] += cost_breakdown["plivo_inr"]
            bucket["stt_inr"] += cost_breakdown["stt_inr"]
            bucket["tts_inr"] += cost_breakdown["tts_inr"]
            bucket["llm_inr"] += cost_breakdown["llm_inr"]

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

    # --- Build customer rows with per-plan breakdown ---
    customer_rows = []
    for cid, data in sorted(per_customer.items(), key=lambda x: -x[1]["revenue_inr"]):
        c = customers.get(cid)
        profit = data["revenue_inr"] - data["cost_inr"]
        margin_pct = (profit / data["revenue_inr"] * 100) if data["revenue_inr"] > 0 else 0

        # Collect all plan buckets for this customer, sorted by calls desc
        plan_buckets = [
            {
                "plan": pk[1],
                "tts_provider": pk[2],
                "calls": pdata["calls"],
                "total_minutes": round(pdata["total_minutes"], 1),
                "revenue_inr": round(pdata["revenue_inr"], 2),
                "cost_inr": round(pdata["cost_inr"], 2),
                "profit_inr": round(pdata["revenue_inr"] - pdata["cost_inr"], 2),
                "margin_pct": round(
                    (pdata["revenue_inr"] - pdata["cost_inr"]) / pdata["revenue_inr"] * 100
                    if pdata["revenue_inr"] > 0 else 0, 1
                ),
                "breakdown": {
                    "plivo_inr": round(pdata["plivo_inr"], 2),
                    "stt_inr": round(pdata["stt_inr"], 2),
                    "tts_inr": round(pdata["tts_inr"], 2),
                    "llm_inr": round(pdata["llm_inr"], 2),
                },
            }
            for pk, pdata in per_plan.items() if pk[0] == cid
        ]
        plan_buckets.sort(key=lambda x: -x["calls"])

        customer_rows.append({
            "customer_id": cid,
            "company_name": c.company_name if c else f"Customer #{cid}",
            "contact_email": c.contact_email if c else None,
            "status": c.status if c else None,
            "plan": subs.get(cid).plan if subs.get(cid) else "starter",
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
            "plan_breakdown": plan_buckets,
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
            "smallest_ai_stt_usd_per_min": SMALLEST_AI_STT_RATE_USD_PER_MIN,
            "tts_speaking_ratio": TTS_SPEAKING_RATIO,
            "openai_rates": OPENAI_RATES,
        },
    }


@router.put("/customers/{customer_id}/test-account")
async def toggle_test_account(
    customer_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    query = select(Customer).where(Customer.id == customer_id)
    customer = (await db.execute(query)).scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    customer.is_test_account = data.get("is_test_account", False)
    await db.commit()
    return {"message": "Success", "is_test_account": customer.is_test_account}

@router.put("/customers/{customer_id}/profitability-overrides")
async def update_customer_profitability_overrides(
    customer_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    query = select(Customer).where(Customer.id == customer_id)
    customer = (await db.execute(query)).scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    settings = dict(customer.report_settings or {})
    settings["profitability_overrides"] = data.get("overrides", {})
    customer.report_settings = settings
    await db.commit()
    return {"message": "Success", "overrides": settings["profitability_overrides"]}

@router.get("/platform-settings")
async def get_platform_settings(
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    from db.models import GlobalPlatformSettings
    global_res = await db.execute(select(GlobalPlatformSettings).limit(1))
    settings_row = global_res.scalar_one_or_none()
    if not settings_row:
        return {"settings": {}}
    return {"settings": settings_row.settings}

@router.put("/platform-settings")
async def update_platform_settings(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    from db.models import GlobalPlatformSettings
    global_res = await db.execute(select(GlobalPlatformSettings).limit(1))
    settings_row = global_res.scalar_one_or_none()
    if not settings_row:
        settings_row = GlobalPlatformSettings(settings=data.get("settings", {}))
        db.add(settings_row)
    else:
        settings_row.settings = data.get("settings", {})
    await db.commit()
    return {"message": "Success", "settings": settings_row.settings}

# --- AUTOMATION API KEY MANAGEMENT ---

class CreateAutomationKeyRequest(BaseModel):
    name: str
    scopes: List[str] = []
    rate_limit_per_minute: int = 60

@router.post('/automation-keys')
async def create_automation_key(
    data: CreateAutomationKeyRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    import secrets
    import hashlib
    
    raw_key = "tkr_auto_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
    
    from db.models import AutomationApiKey
    api_key = AutomationApiKey(
        key_hash=key_hash,
        name=data.name,
        created_by_admin_id=current_admin.id,
        scopes=data.scopes,
        rate_limit_per_minute=data.rate_limit_per_minute
    )
    db.add(api_key)
    await db.commit()
    
    return {
        "status": "success",
        "key_id": api_key.id,
        "raw_key": raw_key,
        "message": "Store this raw_key immediately. It will never be shown again."
    }

@router.get('/automation-keys')
async def list_automation_keys(
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    from db.models import AutomationApiKey
    res = await db.execute(select(AutomationApiKey).order_by(AutomationApiKey.created_at.desc()))
    keys = res.scalars().all()
    
    return [{
        "id": k.id,
        "name": k.name,
        "scopes": k.scopes,
        "rate_limit_per_minute": k.rate_limit_per_minute,
        "is_active": k.is_active,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at
    } for k in keys]

@router.delete('/automation-keys/{key_id}')
async def delete_automation_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    from db.models import AutomationApiKey
    res = await db.execute(select(AutomationApiKey).where(AutomationApiKey.id == key_id))
    k = res.scalar_one_or_none()
    if not k:
        raise HTTPException(404, "Key not found")
        
    await db.delete(k)
    await db.commit()
    return {"status": "deleted"}

@router.patch('/automation-keys/{key_id}')
async def update_automation_key(
    key_id: int,
    is_active: Optional[bool] = None,
    scopes: Optional[List[str]] = None,
    rate_limit_per_minute: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    from db.models import AutomationApiKey
    res = await db.execute(select(AutomationApiKey).where(AutomationApiKey.id == key_id))
    k = res.scalar_one_or_none()
    if not k:
        raise HTTPException(404, "Key not found")
        
    if is_active is not None: k.is_active = is_active
    if scopes is not None: k.scopes = scopes
    if rate_limit_per_minute is not None: k.rate_limit_per_minute = rate_limit_per_minute
    
    await db.commit()
    return {"status": "updated"}
