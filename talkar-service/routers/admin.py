from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, text, delete, case, bindparam
from db.session import get_db
from db.models import (
    Customer, Wallet, WalletTransaction, CallLog, Agent, TalkarAdmin, 
    Subscription, Notification, TransactionType, PhoneNumber, 
    PhoneNumberRequest, SupportRequest, Invoice, AutomationAuditLog
)
from services import razorpay_client, notification_service, dograh_client
from services.admin_auth import get_current_admin, create_admin_access_token
from pydantic import BaseModel
from typing import Optional, List, Any, Tuple, Dict
from datetime import datetime, timezone, date
from config import CALL_BLOCK_THRESHOLD_PAISE

import logging

logger = logging.getLogger(__name__)

def parse_iso_datetime(val: Any) -> Optional[datetime]:
    """Parse ISO datetime string, date, or datetime into a timezone-aware or valid datetime object."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return None
    return None

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
    type: Optional[str] = TransactionType.MANUAL_CREDIT.value

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
    
    # Store the integration fee and approved_tier in onboarding_form JSON
    existing_form = customer.onboarding_form or {}
    existing_form["integration_fee_paise"] = data.integration_fee_paise
    existing_form["integration_description"] = data.integration_description
    existing_form["approved_tier"] = data.approved_tier
    customer.onboarding_form = dict(existing_form)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(customer, "onboarding_form")

    # Update or create individual Subscription with approved tier
    from config import resolve_tier_config
    approved_tier_cfg = resolve_tier_config(data.approved_tier)
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    if sub:
        sub.plan = data.approved_tier
        sub.per_minute_rate_paise = approved_tier_cfg.get("per_minute_rate_paise", 600)

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
    from datetime import datetime, timezone
    existing_form["rejected_at"] = datetime.now(timezone.utc).isoformat()
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
    existing_form = dict(customer.onboarding_form or {})
    existing_form["info_requested_message"] = data.message
    from datetime import datetime, timezone
    existing_form["info_requested_at"] = datetime.now(timezone.utc).isoformat()
    customer.onboarding_form = existing_form
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
    sub_orgs_res = await db.execute(
        select(Customer.id, Customer.company_name, Customer.contact_email, Customer.status)
        .where(Customer.billing_org_id == customer.id)
    )
    customer.sub_organizations = [
        {"id": s.id, "company_name": s.company_name, "contact_email": s.contact_email, "status": s.status}
        for s in sub_orgs_res.all()
    ]
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
    
    if customer.status not in ("active", "agent_building", "pending_deposit"):
        raise HTTPException(400, "Can only set custom pricing on active, building, or pending-deposit customers.")
    
    # EC-2: Only master orgs can have custom pricing. Sub-orgs inherit from master.
    if customer.billing_org_id:
        raise HTTPException(400, "Cannot set custom pricing on a sub-org. Set it on the master org and it will cascade.")
        
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
    sub = sub_res.scalar_one_or_none()
    if not sub:
        raise HTTPException(400, "Customer has no subscription record yet. Ensure provisioning has run first.")
    
    from config import get_minimum_rate_floor_paise
    floor = get_minimum_rate_floor_paise(data.tts_provider)
    if data.per_minute_rate_paise < floor:
        raise HTTPException(
            status_code=400,
            detail=f"Rate of {data.per_minute_rate_paise} paise/min is below provider floor of {floor} paise/min (₹{floor/100:.2f}/min) for {data.tts_provider}."
        )

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
        if sub_org.dograh_org_id and data.trigger_reprovisioning:
            try:
                from services.provisioning_service import run_provisioning
                await run_provisioning(sub_org.id, None, db)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to cascade custom plan provisioning to sub-org {sub_org.id}: {e}")
                
    from services.billing_service import sync_wallet_block_policy
    try:
        await sync_wallet_block_policy(db, customer.id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync wallet block policy after custom plan update for {customer.id}: {e}")

    return {"status": "success"}

@router.patch("/customers/{customer_id}")
async def update_customer(customer_id: int, data: CustomerUpdateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if data.status:
        prev_status = customer.status
        customer.status = data.status
        if data.status == "suspended":
            customer.suspended_at = func.now()
        elif data.status == "active":
            customer.suspended_at = None
        await db.commit()
        # If admin is manually reactivating a suspended customer, restore their call capacity in Dograh
        if prev_status == "suspended" and data.status == "active":
            c_form = dict(customer.onboarding_form or {})
            c_form.pop("suspension_reason", None)
            c_form.pop("suspension_message", None)
            customer.onboarding_form = c_form

            # If master account is unsuspended, also restore child sub-orgs to active
            if customer.billing_org_id is None:
                sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id, Customer.status == "suspended"))
                for sub_org in sub_orgs_res.scalars().all():
                    s_form = dict(sub_org.onboarding_form or {})
                    if s_form.get("suspension_reason") in ("zero_balance", None, ""):
                        sub_org.status = "active"
                        sub_org.suspended_at = None
                        s_form.pop("suspension_reason", None)
                        s_form.pop("suspension_message", None)
                        sub_org.onboarding_form = s_form
            await db.commit()

            master_id = customer.billing_org_id or customer.id
            master_res = await db.execute(select(Customer).where(Customer.id == master_id))
            master = master_res.scalar_one_or_none()
            master_dograh_id = master.dograh_org_id if master else customer.dograh_org_id

            from config import resolve_tier_config
            sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
            sub = sub_res.scalar_one_or_none()
            tier_cfg = resolve_tier_config(sub)
            concurrent_limit = tier_cfg.get("concurrent_call_limit", 2)
            tier = sub.plan if sub else "starter"

            is_custom_cap = False
            effective_limit = concurrent_limit
            if customer.billing_org_id:
                form = customer.onboarding_form or {}
                if form.get("concurrent_call_limit"):
                    try:
                        custom_limit = int(form["concurrent_call_limit"])
                        if custom_limit > 0:
                            effective_limit = min(concurrent_limit, custom_limit)
                            is_custom_cap = True
                    except (ValueError, TypeError):
                        pass

            if customer.dograh_org_id:
                from services import dograh_client
                try:
                    await dograh_client.restore_org_calls(
                        customer.dograh_org_id,
                        tier,
                        effective_limit,
                        group_id=master_dograh_id,
                        is_custom_cap=is_custom_cap
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to restore calls after admin unsuspend for org {customer.dograh_org_id}: {e}")

            from services.billing_service import sync_wallet_block_policy
            try:
                await sync_wallet_block_policy(db, master_id)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to sync wallet block policy after admin unsuspend for {master_id}: {e}")
        elif data.status == "suspended":
            from services import dograh_client
            if customer.dograh_org_id:
                try:
                    await dograh_client.block_org_calls(customer.dograh_org_id)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to block calls for org {customer.dograh_org_id}: {e}")
            if customer.billing_org_id is None:
                sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
                for sub_org in sub_orgs_res.scalars().all():
                    sub_org.status = "suspended"
                    sub_org.suspended_at = func.now()
                    s_form = dict(sub_org.onboarding_form or {})
                    if customer.onboarding_form and "suspension_reason" in customer.onboarding_form:
                        s_form["suspension_reason"] = customer.onboarding_form["suspension_reason"]
                    if customer.onboarding_form and "suspension_message" in customer.onboarding_form:
                        s_form["suspension_message"] = customer.onboarding_form["suspension_message"]
                    sub_org.onboarding_form = s_form
                    if sub_org.dograh_org_id:
                        try:
                            await dograh_client.block_org_calls(sub_org.dograh_org_id)
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).error(f"Failed to block calls for sub-org {sub_org.dograh_org_id}: {e}")
                await db.commit()
            from services.billing_service import sync_wallet_block_policy
            try:
                await sync_wallet_block_policy(db, customer.id)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to sync wallet block policy after admin suspend for {customer.id}: {e}")
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
        if customer.status in ("active", "agent_building", "pending_deposit"):
            from services.provisioning_service import run_provisioning
            try:
                await run_provisioning(customer_id, None, db)
            except Exception as e:
                # Don't fail the whole request — tier is saved, provisioning can be retried
                import logging
                logging.getLogger(__name__).error(f"Re-provisioning failed after tier upgrade: {e}")
                
        # Cascade to sub-orgs (Risk 3)
        master_sub = sub
        sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
        for sub_org in sub_orgs_res.scalars().all():
            sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
            sub_sub = sub_sub_res.scalar_one_or_none()
            if sub_sub:
                sub_sub.plan = master_sub.plan if master_sub else data.tier
                sub_sub.per_minute_rate_paise = master_sub.per_minute_rate_paise if (master_sub and master_sub.per_minute_rate_paise is not None) else tier_cfg["per_minute_rate_paise"]
                sub_sub.custom_config = master_sub.custom_config if master_sub else None
                sub_sub.custom_plan_label = master_sub.custom_plan_label if master_sub else None
            else:
                sub_sub = Subscription(
                    customer_id=sub_org.id,
                    plan=master_sub.plan if master_sub else data.tier,
                    per_minute_rate_paise=master_sub.per_minute_rate_paise if (master_sub and master_sub.per_minute_rate_paise is not None) else tier_cfg["per_minute_rate_paise"],
                    status="active",
                    custom_config=master_sub.custom_config if master_sub else None,
                    custom_plan_label=master_sub.custom_plan_label if master_sub else None,
                )
                db.add(sub_sub)
                
            sub_form = sub_org.onboarding_form or {}
            sub_form["approved_tier"] = data.tier
            sub_org.onboarding_form = dict(sub_form)
            flag_modified(sub_org, "onboarding_form")
            
            await db.commit()
            if sub_org.dograh_org_id:
                try:
                    from services.provisioning_service import run_provisioning
                    await run_provisioning(sub_org.id, data.tier, db)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to cascade admin provisioning to sub-org {sub_org.id}: {e}")

        from services.billing_service import sync_wallet_block_policy
        try:
            await sync_wallet_block_policy(db, customer.id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to sync wallet block policy after admin tier update for {customer.id}: {e}")
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
        from services import dograh_client
        
        # 2. Get existing Talkar agents
        existing_res = await db.execute(select(Agent).where(Agent.customer_id == customer_id))
        existing_agents = {a.dograh_workflow_id: a for a in existing_res.scalars().all() if a.dograh_workflow_id}
        
        # 3. Sync from Dograh workflows
        try:
            workflows = await dograh_client.get_org_workflows(customer.dograh_org_id)
            for w in workflows:
                wf_id = w["id"] if isinstance(w, dict) else getattr(w, "id")
                wf_name = w["name"] if isinstance(w, dict) else getattr(w, "name")
                wf_status = w["status"] if isinstance(w, dict) else getattr(w, "status")
                wf_created = w.get("created_at") if isinstance(w, dict) else getattr(w, "created_at", None)
                if wf_id not in existing_agents:
                    new_ag = Agent(
                        customer_id=customer.id,
                        name=wf_name,
                        dograh_workflow_id=wf_id,
                        dograh_org_id=customer.dograh_org_id,
                        status=wf_status,
                        built_at=parse_iso_datetime(wf_created)
                    )
                    db.add(new_ag)
            await db.commit()
        except Exception as e:
            await db.rollback()
            import logging
            logging.getLogger(__name__).error(f"Failed to sync workflows for customer {customer_id}: {e}")

    # 4. Return the (now synced) agents
    result = await db.execute(select(Agent).where(Agent.customer_id == customer_id))
    return result.scalars().all()

@router.get("/customers/{customer_id}/subscription")
async def get_customer_subscription(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    """Return the subscription record for a customer, including custom_config for the admin UI."""
    cust_res = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = cust_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    result = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
    sub = result.scalar_one_or_none()

    # If sub-org has no direct subscription, inherit from master billing org
    if not sub and customer.billing_org_id:
        master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.billing_org_id))
        sub = master_sub_res.scalar_one_or_none()

    if not sub:
        raise HTTPException(404, "No subscription found for this customer")

    return {
        "id": sub.id,
        "plan": sub.plan,
        "per_minute_rate_paise": sub.per_minute_rate_paise,
        "custom_config": getattr(sub, "custom_config", None),
        "custom_plan_label": getattr(sub, "custom_plan_label", None),
        "is_inherited": bool(customer.billing_org_id and sub.customer_id == customer.billing_org_id),
        "billing_org_id": customer.billing_org_id,
    }

@router.get("/customers/{customer_id}/transactions")
async def get_customer_transactions(
    customer_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    """Return attributable wallet transactions for a customer or sub-org."""
    cust_res = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = cust_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    if customer.billing_org_id:
        # Sub-org: return transactions attributed directly to this sub-org
        base_query = select(WalletTransaction).where(
            (WalletTransaction.sub_org_id == customer.id) |
            (WalletTransaction.customer_id == customer.id)
        )
    else:
        # Master: return transactions for master and all sub-orgs
        base_query = select(WalletTransaction).where(
            (WalletTransaction.customer_id == customer.id) |
            (WalletTransaction.sub_org_id == customer.id)
        )

    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    page_val = int(page.default if hasattr(page, "default") and not isinstance(page, (int, float)) else page)
    limit_val = int(limit.default if hasattr(limit, "default") and not isinstance(limit, (int, float)) else limit)
    page_query = base_query.order_by(WalletTransaction.created_at.desc()).offset((page_val - 1) * limit_val).limit(limit_val)
    txn_res = await db.execute(page_query)
    txns = txn_res.scalars().all()

    return {
        "transactions": [
            {
                "id": t.id,
                "customer_id": t.customer_id,
                "sub_org_id": t.sub_org_id,
                "dograh_org_id": t.dograh_org_id,
                "type": t.type,
                "amount_paise": t.amount_paise,
                "description": t.description,
                "dograh_run_id": t.dograh_run_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txns
        ],
        "total": total,
        "page": page_val,
    }

async def _resolve_agent_tts_provider(agent: Agent, db: AsyncSession) -> str:
    """Helper to detect the agent's effective TTS provider from Dograh org config or subscription tier."""
    from services import dograh_client
    org_id = agent.dograh_org_id
    if not org_id:
        cust_res = await db.execute(select(Customer).where(Customer.id == agent.customer_id))
        cust = cust_res.scalar_one_or_none()
        if cust:
            org_id = cust.dograh_org_id

    if org_id:
        try:
            cfg = await dograh_client.get_org_config(org_id, "MODEL_CONFIGURATION_V2")
            if cfg:
                tts_cfg = cfg.get("byok", {}).get("pipeline", {}).get("tts", {})
                provider = tts_cfg.get("provider")
                if provider:
                    return str(provider)
        except Exception as e:
            logger.warning(f"Could not fetch MODEL_CONFIGURATION_V2 for org {org_id}: {e}")

    # Fallback to subscription tier's default TTS provider
    try:
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == agent.customer_id))
        sub = sub_res.scalar_one_or_none()
        if not sub:
            c_res = await db.execute(select(Customer).where(Customer.id == agent.customer_id))
            cust = c_res.scalar_one_or_none()
            if cust and cust.billing_org_id:
                master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == cust.billing_org_id))
                sub = master_sub_res.scalar_one_or_none()
        if not isinstance(sub, Subscription):
            sub = None
    except Exception:
        sub = None
    from config import resolve_tier_config
    return str(resolve_tier_config(sub).get("tts_provider", "deepgram"))


@router.patch("/customers/{customer_id}/agents/{agent_id}/rate")
@router.post("/customers/{customer_id}/agents/{agent_id}/rate")
async def update_agent_rate(customer_id: int, agent_id: int, data: UpdateAgentRateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.customer_id == customer_id))
    agent = result.scalar_one_or_none()
    if not agent: raise HTTPException(404, "Agent not found for this customer")

    if data.per_minute_rate_paise is not None:
        from config import get_minimum_rate_floor_paise
        tts_provider = await _resolve_agent_tts_provider(agent, db)
        floor = get_minimum_rate_floor_paise(tts_provider)
        if data.per_minute_rate_paise < floor:
            raise HTTPException(
                status_code=400,
                detail=f"Rate of {data.per_minute_rate_paise} paise/min is below provider floor of {floor} paise/min (₹{floor/100:.2f}/min) for {tts_provider}."
            )

    agent.per_minute_rate_paise = data.per_minute_rate_paise
    await db.commit()
    return {"status": "ok", "per_minute_rate_paise": agent.per_minute_rate_paise}

@router.patch("/agents/{agent_id}/rate")
@router.post("/agents/{agent_id}/rate")
async def update_agent_rate_direct(agent_id: int, data: UpdateAgentRateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent: raise HTTPException(404, "Agent not found")

    if data.per_minute_rate_paise is not None:
        from config import get_minimum_rate_floor_paise
        tts_provider = await _resolve_agent_tts_provider(agent, db)
        floor = get_minimum_rate_floor_paise(tts_provider)
        if data.per_minute_rate_paise < floor:
            raise HTTPException(
                status_code=400,
                detail=f"Rate of {data.per_minute_rate_paise} paise/min is below provider floor of {floor} paise/min (₹{floor/100:.2f}/min) for {tts_provider}."
            )

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
        .values(
            balance_paise=Wallet.balance_paise + data.amount_paise,
            zero_balance_since=case(
                (Wallet.balance_paise + data.amount_paise <= 0, func.coalesce(Wallet.zero_balance_since, func.now())),
                else_=None
            )
        )
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
    # Store transaction
    valid_credit_types = {
        TransactionType.MANUAL_CREDIT.value,
        TransactionType.GRANT.value,
        TransactionType.TOP_UP.value,
        TransactionType.CREDIT.value,
    }
    txn_type = data.type if data.type in valid_credit_types else TransactionType.MANUAL_CREDIT.value
    sub_org_id = customer_id if customer_id != master_id else None
    dograh_org_id = None
    if sub_org_id:
        try:
            c_res = await db.execute(select(Customer.dograh_org_id).where(Customer.id == sub_org_id))
            dograh_org_id = c_res.scalar_one_or_none()
        except Exception:
            pass

    txn = WalletTransaction(
        customer_id=master_id,
        sub_org_id=sub_org_id,
        dograh_org_id=dograh_org_id,
        type=txn_type,
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

        from services.provisioning_service import run_provisioning
        from services.billing_service import sync_wallet_block_policy

        if wallet.balance_paise >= activation_threshold:
            # 1. Activate master if pending_deposit
            master_res = await db.execute(select(Customer).where(Customer.id == master_id))
            master_cust = master_res.scalar_one_or_none()
            if master_cust and master_cust.status == "pending_deposit":
                master_cust.status = "active"
                m_form = dict(master_cust.onboarding_form or {})
                m_form.pop("suspension_reason", None)
                m_form.pop("suspension_message", None)
                master_cust.onboarding_form = m_form
                await db.commit()
                try:
                    await run_provisioning(master_cust.id, None, db)
                except Exception as e:
                    import logging; logging.getLogger(__name__).error(f"[AdminCredit] Provisioning failed for master {master_cust.id}: {e}")

            # 2. Activate caller customer if sub-org in pending_deposit
            if customer.id != master_id and customer.status == "pending_deposit":
                customer.status = "active"
                c_form = dict(customer.onboarding_form or {})
                c_form.pop("suspension_reason", None)
                c_form.pop("suspension_message", None)
                customer.onboarding_form = c_form
                await db.commit()
                try:
                    await run_provisioning(customer.id, None, db)
                except Exception as e:
                    import logging; logging.getLogger(__name__).error(f"[AdminCredit] Provisioning failed for customer {customer.id}: {e}")

            # 3. Cascade activation to all sub-orgs under this master that are in pending_deposit
            sub_pending_res = await db.execute(
                select(Customer).where(Customer.billing_org_id == master_id, Customer.status == "pending_deposit")
            )
            for sub_p in sub_pending_res.scalars().all():
                if sub_p.id != customer.id:
                    sub_p.status = "active"
                    s_form = dict(sub_p.onboarding_form or {})
                    s_form.pop("suspension_reason", None)
                    s_form.pop("suspension_message", None)
                    sub_p.onboarding_form = s_form
                    await db.commit()
                    try:
                        await run_provisioning(sub_p.id, None, db)
                    except Exception as e:
                        import logging; logging.getLogger(__name__).error(f"[AdminCredit] Provisioning failed for cascaded sub-org {sub_p.id}: {e}")

        if customer.status == "suspended":
            from config import CALL_BLOCK_THRESHOLD_PAISE
            c_form = customer.onboarding_form or {}
            if c_form.get("suspension_reason") in ("zero_balance", None, "") and wallet.balance_paise > CALL_BLOCK_THRESHOLD_PAISE:
                customer.status = "active"
                customer.suspended_at = None
                c_dict = dict(c_form)
                c_dict.pop("suspension_reason", None)
                c_dict.pop("suspension_message", None)
                customer.onboarding_form = c_dict

                # Cascade reactivation to zero-balance suspended sub-orgs
                sub_p_res = await db.execute(
                    select(Customer).where(Customer.billing_org_id == customer.id, Customer.status == "suspended")
                )
                reactivated_sub_orgs = []
                for sub_p in sub_p_res.scalars().all():
                    s_form = dict(sub_p.onboarding_form or {})
                    if s_form.get("suspension_reason") in ("zero_balance", None, ""):
                        sub_p.status = "active"
                        sub_p.suspended_at = None
                        s_form.pop("suspension_reason", None)
                        s_form.pop("suspension_message", None)
                        sub_p.onboarding_form = s_form
                        reactivated_sub_orgs.append(sub_p)

                await db.commit()
                try:
                    await run_provisioning(customer.id, None, db)
                except Exception as e:
                    import logging; logging.getLogger(__name__).error(f"[AdminCredit] Re-provisioning failed for customer {customer.id}: {e}")

                for s_org in reactivated_sub_orgs:
                    try:
                        await run_provisioning(s_org.id, None, db)
                    except Exception as e:
                        import logging; logging.getLogger(__name__).error(f"[AdminCredit] Provisioning failed for cascaded sub-org {s_org.id}: {e}")

        # Synchronize wallet block policy
        await sync_wallet_block_policy(db, customer.id)

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
    type: Optional[str] = TransactionType.MANUAL_DEDUCT.value

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
        .values(
            balance_paise=Wallet.balance_paise - data.amount_paise,
            zero_balance_since=case(
                (Wallet.balance_paise - data.amount_paise <= 0, func.coalesce(Wallet.zero_balance_since, func.now())),
                else_=None
            )
        )
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
    # Record transaction
    valid_deduct_types = {
        TransactionType.MANUAL_DEDUCT.value,
        TransactionType.CALL_DEDUCTION.value,
        TransactionType.DEDUCTION.value,
    }
    txn_type = data.type if data.type in valid_deduct_types else TransactionType.MANUAL_DEDUCT.value
    sub_org_id = customer_id if customer_id != master_id else None
    dograh_org_id = customer.dograh_org_id if customer else None

    transaction = WalletTransaction(
        customer_id=master_id,
        sub_org_id=sub_org_id,
        dograh_org_id=dograh_org_id,
        type=txn_type,
        amount_paise=-data.amount_paise,
        description=f"Admin deduction: {data.reason} (by {current_admin.email})"
    )
    db.add(transaction)
    await db.commit()

    if wallet.balance_paise < 0:
        import logging
        logging.getLogger(__name__).warning(f"Customer {customer_id} wallet went negative after admin deduct: {wallet.balance_paise}")

    # Synchronize wallet block policy with Dograh
    from services.billing_service import sync_wallet_block_policy
    if wallet.balance_paise <= CALL_BLOCK_THRESHOLD_PAISE:
        try:
            await notification_service.notify_customer_service_paused(customer.id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to send service paused notification: {e}")

    await sync_wallet_block_policy(db, customer.id)

    return {"status": "success", "new_balance_paise": wallet.balance_paise}

class AdminSetBalanceRequest(BaseModel):
    amount_paise: int
    reason: str = "Admin manual override"
    expected_balance_paise: Optional[int] = None

@router.post("/customers/{customer_id}/set-balance")
async def admin_set_balance(customer_id: int, data: AdminSetBalanceRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer_id)
    if not wallet: raise HTTPException(404, "Wallet not found")
    
    # Lock the wallet row exclusively to prevent concurrent call deduction or top-up race conditions
    wallet_res = await db.execute(
        select(Wallet).where(Wallet.customer_id == master_id).with_for_update()
    )
    wallet = wallet_res.scalar_one_or_none()
    if not wallet: raise HTTPException(404, "Wallet not found")

    if data.expected_balance_paise is not None and wallet.balance_paise != data.expected_balance_paise:
        raise HTTPException(
            status_code=409,
            detail=f"Concurrent modification detected. Current balance is {wallet.balance_paise} paise, but expected {data.expected_balance_paise} paise. Please refresh and retry."
        )

    diff = data.amount_paise - wallet.balance_paise
    
    result = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == master_id)
        .values(
            balance_paise=data.amount_paise,
            zero_balance_since=func.coalesce(Wallet.zero_balance_since, func.now()) if data.amount_paise <= 0 else None
        )
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
    sub_org_id = customer_id if customer_id != master_id else None
    dograh_org_id = customer.dograh_org_id if customer else None

    transaction = WalletTransaction(
        customer_id=master_id,
        sub_org_id=sub_org_id,
        dograh_org_id=dograh_org_id,
        type=TransactionType.MANUAL_SET_BALANCE.value,
        amount_paise=diff,
        description=f"Admin set balance: {data.reason} (by {current_admin.email})"
    )
    db.add(transaction)
    await db.commit()

    # ── Enforce Dograh call blocking based on new balance ─────────────────────
    from services.billing_service import sync_wallet_block_policy
    await sync_wallet_block_policy(db, customer_id)
    # ──────────────────────────────────────────────────────────────────────────

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
    customer.suspended_at = func.now()
    # Store suspension reason in onboarding_form for audit trail
    existing_form = dict(customer.onboarding_form or {})
    existing_form["suspension_reason"] = data.reason
    existing_form["suspension_message"] = data.custom_message or ""
    customer.onboarding_form = existing_form
    await db.commit()
    # Block calls in Dograh immediately
    from services import dograh_client
    if customer.dograh_org_id:
        try:
            await dograh_client.block_org_calls(customer.dograh_org_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to block calls for org {customer.dograh_org_id}: {e}")

    # If this is a master account, cascade call blocks to all sub-orgs in Dograh
    if customer.billing_org_id is None:
        sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
        for sub_org in sub_orgs_res.scalars().all():
            sub_org.status = "suspended"
            sub_org.suspended_at = func.now()
            s_form = dict(sub_org.onboarding_form or {})
            s_form["suspension_reason"] = data.reason
            s_form["suspension_message"] = data.custom_message or ""
            sub_org.onboarding_form = s_form
            if sub_org.dograh_org_id:
                try:
                    await dograh_client.block_org_calls(sub_org.dograh_org_id)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Failed to block calls for sub-org {sub_org.dograh_org_id}: {e}")
        await db.commit()

    from services.billing_service import sync_wallet_block_policy
    try:
        await sync_wallet_block_policy(db, customer.id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync wallet block policy after suspend for {customer.id}: {e}")

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
    customer.suspended_at = None
    # Clear suspension reason from onboarding_form
    existing_form = dict(customer.onboarding_form or {})
    existing_form.pop("suspension_reason", None)
    existing_form.pop("suspension_message", None)
    customer.onboarding_form = existing_form

    if customer.billing_org_id is None:
        sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id, Customer.status == "suspended"))
        for sub_org in sub_orgs_res.scalars().all():
            s_form = dict(sub_org.onboarding_form or {})
            if s_form.get("suspension_reason") in ("zero_balance", None, ""):
                sub_org.status = "active"
                sub_org.suspended_at = None
                s_form.pop("suspension_reason", None)
                s_form.pop("suspension_message", None)
                sub_org.onboarding_form = s_form
    await db.commit()

    # Determine master customer and subscription for accurate plan/limits and group pool linking
    master_id = customer.billing_org_id or customer.id
    master_res = await db.execute(select(Customer).where(Customer.id == master_id))
    master = master_res.scalar_one_or_none()
    master_dograh_id = master.dograh_org_id if master else customer.dograh_org_id

    from config import resolve_tier_config
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    tier_cfg = resolve_tier_config(sub)
    concurrent_limit = tier_cfg.get("concurrent_call_limit", 2)
    tier = sub.plan if sub else "starter"

    # Respect sub-org custom concurrency cap if configured
    is_custom_cap = False
    effective_limit = concurrent_limit
    if customer.billing_org_id:
        form = customer.onboarding_form or {}
        if form.get("concurrent_call_limit"):
            try:
                custom_limit = int(form["concurrent_call_limit"])
                if custom_limit > 0:
                    effective_limit = min(concurrent_limit, custom_limit)
                    is_custom_cap = True
            except (ValueError, TypeError):
                pass

    # Restore calls in Dograh with master group_id and custom cap annotation
    if customer.dograh_org_id:
        from services import dograh_client
        try:
            await dograh_client.restore_org_calls(
                customer.dograh_org_id,
                tier,
                effective_limit,
                group_id=master_dograh_id,
                is_custom_cap=is_custom_cap
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to restore calls after unsuspend for org {customer.dograh_org_id}: {e}")

    # Synchronize wallet block policy across the fleet (restores active sub-orgs if master was unsuspended)
    from services.billing_service import sync_wallet_block_policy
    try:
        await sync_wallet_block_policy(db, master_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync wallet block policy after unsuspend for {master_id}: {e}")

    return {"status": "active"}

@router.post("/customers/{customer_id}/provision/retry")
async def retry_provisioning(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status not in ("approved", "agent_building", "active", "suspended", "pending_deposit", "pending_plan_selection"):
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
    
    effective_sub_id = customer.billing_org_id if customer.billing_org_id else customer.id
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == effective_sub_id))
    sub = sub_res.scalar_one_or_none()
    tier_name = sub.plan if sub else "starter"
    from config import TIER_CONFIG, CALL_BLOCK_THRESHOLD_PAISE
    activation_min = TIER_CONFIG.get(tier_name, TIER_CONFIG["starter"]).get("activation_deposit_paise", 600000)
    is_funded = False
    if wallet:
        if customer.billing_org_id:
            master_cust_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
            master_cust = master_cust_res.scalar_one_or_none()
            if master_cust and master_cust.status == "active":
                is_funded = wallet.balance_paise > CALL_BLOCK_THRESHOLD_PAISE
            else:
                is_funded = wallet.balance_paise >= activation_min
        else:
            is_funded = wallet.balance_paise >= activation_min

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
            from services.billing_service import sync_wallet_block_policy
            try:
                await sync_wallet_block_policy(db, customer.id)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to sync wallet block policy on activating sub-org {customer.id}: {e}")
            
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

            # Provision the agent so AI keys are injected and it's testable/configurable
            from services.provisioning_service import run_provisioning
            from services import dograh_client
            await run_provisioning(customer.id, None, db)

            # Immediately block calls (CONCURRENT_CALL_LIMIT=0) until master pays deposit
            if customer.dograh_org_id:
                await dograh_client.block_org_calls(customer.dograh_org_id)

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
    plivo_number_id: Optional[str] = None
    monthly_cost_paise: Optional[int] = None

class ApprovePhoneNumberRequestBody(BaseModel):
    numbers: list[str]
    monthly_cost_paise: Optional[int] = None

class DenyPhoneNumberRequestBody(BaseModel):
    admin_note: str

@router.get("/customers/{customer_id}/phone-numbers")
async def get_customer_phone_numbers(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(PhoneNumber).where(PhoneNumber.customer_id == customer_id))
    return result.scalars().all()

@router.delete("/customers/{customer_id}/phone-numbers/{phone_number_id}")
async def release_customer_phone_number(
    customer_id: int,
    phone_number_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    result = await db.execute(
        select(PhoneNumber).where(PhoneNumber.id == phone_number_id, PhoneNumber.customer_id == customer_id)
    )
    pn = result.scalar_one_or_none()
    if not pn:
        raise HTTPException(404, "Phone number not found")
    pn.status = "released"
    await db.commit()

    if pn.number:
        try:
            cust_res = await db.execute(select(Customer).where(Customer.id == customer_id))
            cust = cust_res.scalar_one_or_none()
            if cust and cust.dograh_org_id:
                await dograh_client.release_phone_number_from_dograh(cust.dograh_org_id, pn.number)
        except Exception as e:
            logger.warning(f"Failed to deactivate released phone number {pn.number} in Dograh for customer {customer_id}: {e}")

    return {"status": "released"}

@router.post("/customers/{customer_id}/assign-phone-number")
async def assign_phone_number(customer_id: int, data: AssignPhoneNumberRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status not in ("active", "agent_building", "pending_deposit"):
        raise HTTPException(400, f"Cannot assign phone numbers to customer in '{customer.status}' state.")
    
    from config import DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE
    monthly_cost = data.monthly_cost_paise if (data.monthly_cost_paise is not None and data.monthly_cost_paise > 0) else DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE

    pn = PhoneNumber(
        customer_id=customer_id,
        number=data.number,
        plivo_number_id=data.plivo_number_id,
        monthly_cost_paise=monthly_cost,
    )
    db.add(pn)
    await db.commit()

    if customer.dograh_org_id:
        try:
            agent_res = await db.execute(
                select(Agent.dograh_workflow_id)
                .where(Agent.customer_id == customer_id, Agent.dograh_workflow_id.isnot(None))
                .order_by(Agent.id.desc())
                .limit(1)
            )
            wf_id = agent_res.scalar_one_or_none()
            await dograh_client.sync_phone_number_to_dograh(customer.dograh_org_id, data.number, workflow_id=wf_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to sync phone number {data.number} to Dograh org {customer.dograh_org_id}: {e}")

    try:
        from services.billing_service import charge_initial_phone_number_lease
        await charge_initial_phone_number_lease(db, customer_id, pn)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Initial lease charge bypassed or failed for phone {pn.number}: {e}")

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

    # Blocker 22: verify customer is active before approving number requests
    cust_res = await db.execute(select(Customer).where(Customer.id == req.customer_id))
    customer = cust_res.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status not in ("active", "agent_building", "pending_deposit"):
        raise HTTPException(400, f"Cannot approve phone number request for customer in '{customer.status}' state.")
    
    req.status = "approved"
    req.resolved_at = func.now()
    
    from config import DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE
    monthly_cost = data.monthly_cost_paise if (data.monthly_cost_paise is not None and data.monthly_cost_paise > 0) else DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE

    created_pns = []
    for number in data.numbers:
        pn = PhoneNumber(
            customer_id=req.customer_id,
            number=number,
            monthly_cost_paise=monthly_cost,
        )
        db.add(pn)
        created_pns.append(pn)
        
    await db.commit()

    # Sync approved numbers to Dograh
    if customer and customer.dograh_org_id:
        agent_res = await db.execute(
            select(Agent.dograh_workflow_id)
            .where(Agent.customer_id == req.customer_id, Agent.dograh_workflow_id.isnot(None))
            .order_by(Agent.id.desc())
            .limit(1)
        )
        wf_id = agent_res.scalar_one_or_none()
        for number in data.numbers:
            try:
                await dograh_client.sync_phone_number_to_dograh(customer.dograh_org_id, number, workflow_id=wf_id)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to sync phone number {number} to Dograh org {customer.dograh_org_id}: {e}")
    
    for pn in created_pns:
        try:
            from services.billing_service import charge_initial_phone_number_lease
            await charge_initial_phone_number_lease(db, req.customer_id, pn)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Initial lease charge bypassed or failed for phone {pn.number}: {e}")

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
# PROFITABILITY DASHBOARD & CALL COST AUDIT ENGINE
# ---------------------------------------------------------------------------
from services.call_audit_calculator import (
    VENDOR_PRICING_REGISTRY,
    calculate_call_unit_economics,
    estimate_call_cost_inr,
)

USD_TO_INR = VENDOR_PRICING_REGISTRY["forex"]["default"]
PLIVO_COST_PER_MIN_INR = VENDOR_PRICING_REGISTRY["telephony"]["plivo"]["outbound_india"]["rate_inr_per_min"]
DEEPGRAM_STT_RATE_USD_PER_MIN = VENDOR_PRICING_REGISTRY["stt"]["deepgram_nova_3"]["rate"]
DEEPGRAM_TTS_RATE_USD_PER_1K_CHARS = VENDOR_PRICING_REGISTRY["tts"]["deepgram_aura_1"]["rate_per_1k_chars"]
ELEVENLABS_TTS_RATE_USD_PER_1K_CHARS = VENDOR_PRICING_REGISTRY["tts"]["elevenlabs_flash"]["rate_per_1k_chars"]
SMALLEST_AI_TTS_RATE_USD_PER_1K_CHARS = VENDOR_PRICING_REGISTRY["tts"]["smallest_ai_lightning"]["rate_per_1k_chars"]
SMALLEST_AI_STT_RATE_USD_PER_MIN = VENDOR_PRICING_REGISTRY["stt"]["smallest_ai_pulse"]["rate"]

TTS_SPEAKING_RATIO = 0.47
TTS_AVG_CHARS_PER_MIN = 900


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
    Delegates to the unified calculation engine in call_audit_calculator.
    """
    return estimate_call_cost_inr(
        duration_seconds=duration_seconds,
        usage_info=usage_info,
        tts_provider=tts_provider,
        stt_provider=stt_provider,
        llm_model=llm_model,
        overrides=overrides,
    )


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
    from config import TIER_CONFIG, resolve_tier_config

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

    # Process call_rows in bounded batches (500 runs) to avoid allocating gigabytes of RAM
    # from tens of thousands of multi-kilobyte usage_info JSON blobs simultaneously.
    BATCH_SIZE = 500
    ddb_session_ctx = None
    ddb = None
    try:
        ddb_session_ctx = DograhSessionLocal()
        ddb = await ddb_session_ctx.__aenter__()
    except Exception as e:
        _log.warning(f"Could not open Dograh DB session for profitability: {e}")
        ddb = None
        ddb_session_ctx = None

    try:
        for i in range(0, len(call_rows), BATCH_SIZE):
            batch_rows = call_rows[i : i + BATCH_SIZE]
            batch_run_ids = [r.dograh_run_id for r in batch_rows if r.dograh_run_id]
            batch_usage_map = {}
            if ddb and batch_run_ids:
                try:
                    usage_rows = (await ddb.execute(
                        text("SELECT id, usage_info FROM workflow_runs WHERE id = ANY(:ids)"),
                        {"ids": batch_run_ids},
                    )).all()
                    batch_usage_map = {r.id: r.usage_info for r in usage_rows}
                except Exception as e:
                    _log.warning(f"Could not fetch usage_info batch from Dograh DB: {e}")
                    try:
                        await ddb.rollback()
                    except Exception:
                        pass

            for row in batch_rows:
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
                        tier_cfg = TIER_CONFIG.get(sub.plan, {})
                        tts_provider = tier_cfg.get("tts_provider", "deepgram")
                        stt_provider = tier_cfg.get("stt_provider", "deepgram")
                        llm_model = tier_cfg.get("llm_model", "gpt-4o-mini")
                        active_plan = sub.plan

                usage_info = batch_usage_map.get(row.dograh_run_id)
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

            batch_usage_map.clear()
    finally:
        if ddb_session_ctx:
            try:
                await ddb_session_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    # --- Fetch topups for the period ---
    topup_q = select(func.sum(WalletTransaction.amount_paise)).where(
        WalletTransaction.type.in_([
            TransactionType.TOP_UP.value,
            TransactionType.CREDIT.value,
            TransactionType.MANUAL_CREDIT.value,
        ])
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


# ---------------------------------------------------------------------------
# CALL COST AUDIT & MARGIN INSPECTOR (PHASE 3)
# ---------------------------------------------------------------------------

class SimulateCallAuditRequest(BaseModel):
    dograh_run_id: int
    override_tts_provider: Optional[str] = None
    override_tts_model: Optional[str] = None
    override_llm_model: Optional[str] = None
    override_stt_provider: Optional[str] = None
    override_stt_model: Optional[str] = None
    override_usd_inr: Optional[float] = None
    override_exchange_rate: Optional[float] = None
    override_plivo_rate_inr: Optional[float] = None
    override_telephony_cost_per_min_inr: Optional[float] = None


async def _fetch_run_audit_context(
    dograh_run_id: int,
    db: AsyncSession,
) -> Tuple[Optional[CallLog], Optional[Customer], Dict[str, Any]]:
    """Helper to load CallLog, Customer, and Dograh WorkflowRun usage metrics."""
    call_log = (await db.execute(
        select(CallLog).where(CallLog.dograh_run_id == dograh_run_id)
    )).scalar_one_or_none()

    customer = None
    if call_log:
        customer = (await db.execute(
            select(Customer).where(Customer.id == call_log.customer_id)
        )).scalar_one_or_none()

    workflow_run_data = {}
    try:
        from services.dograh_client import DograhSessionLocal, _has_dograh_db
        if _has_dograh_db():
            async with DograhSessionLocal() as ddb:
                row = (await ddb.execute(
                    text("SELECT id, usage_info, state, mode FROM workflow_runs WHERE id = :run_id"),
                    {"run_id": dograh_run_id}
                )).fetchone()
                if row:
                    u_info = row.usage_info or {}
                    if isinstance(u_info, str):
                        try:
                            import json
                            u_info = json.loads(u_info)
                        except Exception:
                            u_info = {}
                    workflow_run_data = {
                        "id": row.id,
                        "usage_info": u_info,
                        "status": row.state,
                        "mode": row.mode,
                    }
    except Exception as e:
        logger.debug(f"Direct Dograh DB fetch for run {dograh_run_id} failed: {e}")

    # Fallback to REST API if DB fetch failed or yielded empty usage_info
    if not workflow_run_data or not workflow_run_data.get("usage_info"):
        try:
            from services.dograh_client import _request_dograh_api
            resp = await _request_dograh_api("GET", f"/api/v1/internal/runs/{dograh_run_id}")
            if resp and resp.status_code == 200:
                run_json = resp.json()
                u_info = run_json.get("usage_info") or {}
                if isinstance(u_info, str):
                    try:
                        import json
                        u_info = json.loads(u_info)
                    except Exception:
                        u_info = {}
                workflow_run_data = {
                    "id": run_json.get("id", dograh_run_id),
                    "usage_info": u_info,
                    "status": run_json.get("state") or run_json.get("status"),
                    "mode": run_json.get("mode"),
                }
        except Exception as e:
            logger.debug(f"REST fetch for run {dograh_run_id} failed: {e}")

    return call_log, customer, workflow_run_data


@router.get("/call-audit/customers")
async def get_call_audit_customers(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    """
    Searchable, paginated list of customer accounts with total call counts, duration, and billed revenue.
    """
    count_query = select(func.count(Customer.id))
    if search:
        term = f"%{search.strip()}%"
        count_query = count_query.where(
            (Customer.company_name.ilike(term)) | (Customer.contact_email.ilike(term))
        )
    total_customers = (await db.execute(count_query)).scalar() or 0

    query = select(
        Customer.id,
        Customer.company_name,
        Customer.contact_email,
        Customer.contact_name,
        Customer.status,
        Customer.onboarding_form,
        Customer.report_settings,
        Subscription.plan.label("subscription_plan"),
        func.count(CallLog.id).label("total_calls"),
        func.coalesce(func.sum(CallLog.duration_seconds), 0).label("total_duration_seconds"),
        func.coalesce(func.sum(CallLog.cost_to_customer_paise), 0).label("total_revenue_paise"),
    ).outerjoin(
        Subscription, Subscription.customer_id == Customer.id
    ).outerjoin(
        CallLog, CallLog.customer_id == Customer.id
    )

    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            (Customer.company_name.ilike(term)) | (Customer.contact_email.ilike(term))
        )

    query = query.group_by(Customer.id, Subscription.plan)
    query = query.order_by(func.count(CallLog.id).desc(), Customer.company_name.asc())
    query = query.offset((page - 1) * limit).limit(limit)

    rows = (await db.execute(query)).all()
    customers_list = []
    for r in rows:
        settings = r.report_settings or {}
        currency = settings.get("currency", "INR")
        rev_paise = float(r.total_revenue_paise or 0)
        resolved_name = (
            r.company_name
            or (r.onboarding_form.get("businessName") if r.onboarding_form else None)
            or (r.onboarding_form.get("company_name") if r.onboarding_form else None)
            or r.contact_name
            or (r.contact_email.split("@")[0] if r.contact_email else f"Org #{r.id}")
        )
        customers_list.append({
            "id": r.id,
            "company_name": resolved_name,
            "contact_email": r.contact_email,
            "contact_name": r.contact_name,
            "status": r.status,
            "plan": r.subscription_plan or "starter",
            "currency": currency,
            "total_calls": int(r.total_calls or 0),
            "total_duration_seconds": int(r.total_duration_seconds or 0),
            "total_revenue_inr": round(rev_paise / 100.0, 2),
        })

    return {
        "customers": customers_list,
        "total": total_customers,
        "page": page,
        "limit": limit,
    }


@router.get("/call-audit/calls")
async def get_call_audit_calls(
    customer_id: int = Query(..., description="Customer ID"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    """
    Paginated list of call logs for a customer, filterable by date range or search.
    """
    query = select(CallLog).where(CallLog.customer_id == customer_id)
    count_query = select(func.count(CallLog.id)).where(CallLog.customer_id == customer_id)

    if search:
        clean_search = search.strip()
        if clean_search.isdigit():
            cond = (CallLog.dograh_run_id == int(clean_search)) | (CallLog.caller_number.ilike(f"%{clean_search}%"))
        else:
            cond = CallLog.caller_number.ilike(f"%{clean_search}%")
        query = query.where(cond)
        count_query = count_query.where(cond)

    if date_from:
        dt_from = parse_iso_datetime(date_from)
        if dt_from:
            query = query.where(CallLog.called_at >= dt_from)
            count_query = count_query.where(CallLog.called_at >= dt_from)

    if date_to:
        dt_to = parse_iso_datetime(date_to)
        if dt_to:
            query = query.where(CallLog.called_at <= dt_to)
            count_query = count_query.where(CallLog.called_at <= dt_to)

    total_calls = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(CallLog.called_at.desc(), CallLog.id.desc())
    query = query.offset((page - 1) * limit).limit(limit)
    call_logs = (await db.execute(query)).scalars().all()

    calls_list = []
    for c in call_logs:
        dur = int(c.duration_seconds or 0)
        rev_paise = int(c.cost_to_customer_paise or 0)
        calls_list.append({
            "id": c.id,
            "dograh_run_id": c.dograh_run_id,
            "customer_id": c.customer_id,
            "agent_id": c.agent_id,
            "called_at": c.called_at.isoformat() if c.called_at else None,
            "duration_seconds": dur,
            "cost_to_customer_paise": rev_paise,
            "revenue_inr": round(rev_paise / 100.0, 2),
            "caller_number": c.caller_number,
            "direction": c.call_type or "outbound",
            "plan": c.plan,
            "tts_provider": c.tts_provider,
            "status": "completed" if dur > 0 else "unanswered",
        })

    return {
        "calls": calls_list,
        "total": total_calls,
        "page": page,
        "limit": limit,
    }


@router.get("/call-audit/run/{dograh_run_id}")
async def get_call_audit_run(
    dograh_run_id: int,
    exchange_rate: Optional[float] = Query(None),
    plivo_rate_inr: Optional[float] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    """
    Comprehensive, itemized unit economics audit JSON for a single call run.
    """
    call_log, customer, workflow_run_data = await _fetch_run_audit_context(dograh_run_id, db)

    if not call_log and not workflow_run_data:
        raise HTTPException(404, f"Call run {dograh_run_id} not found")

    overrides = {}
    if customer and customer.report_settings:
        overrides.update(customer.report_settings.get("profitability_overrides", {}))
    if exchange_rate is not None:
        overrides["exchange_rate"] = exchange_rate
    if plivo_rate_inr is not None:
        overrides["plivo_rate_inr"] = plivo_rate_inr

    call_log_dict = {
        "dograh_run_id": dograh_run_id,
        "customer_id": call_log.customer_id if call_log else (customer.id if customer else None),
        "company_name": customer.company_name if customer else None,
        "duration_seconds": call_log.duration_seconds if call_log else workflow_run_data.get("duration_seconds", 0),
        "cost_to_customer_paise": call_log.cost_to_customer_paise if call_log else 0,
        "call_status": "completed" if (call_log and (call_log.duration_seconds or 0) > 0) else workflow_run_data.get("status", "completed"),
        "call_type": call_log.call_type if call_log else "outbound",
        "caller_number": call_log.caller_number if call_log else None,
        "customer_currency": (customer.report_settings or {}).get("currency", "INR") if customer else "INR",
    }

    audit = calculate_call_unit_economics(call_log_dict, workflow_run_data, overrides)

    return {
        "dograh_run_id": audit["dograh_run_id"],
        "customer_id": audit["customer_id"],
        "company_name": audit["company_name"],
        "call_duration_seconds": audit["call_duration_seconds"],
        "billed_minutes": audit["billed_minutes"],
        "revenue_inr": round(audit["revenue_inr"], 2),
        "effective_rate_per_min_inr": round(audit["effective_rate_per_min_inr"], 2),
        "exchange_rate_used": audit["exchange_rate_used"],
        "cost_breakdown": {
            "plivo": {
                "minutes": audit["cost_breakdown"]["plivo"]["minutes"],
                "rate_inr": round(audit["cost_breakdown"]["plivo"]["rate_inr"], 2),
                "cost_inr": round(audit["cost_breakdown"]["plivo"]["cost_inr"], 2),
            },
            "stt": {
                "provider": audit["cost_breakdown"]["stt"]["provider"],
                "model": audit["cost_breakdown"]["stt"]["model"],
                "seconds": round(audit["cost_breakdown"]["stt"]["seconds"], 1),
                "rate_usd_min": audit["cost_breakdown"]["stt"].get("rate_usd_min"),
                "cost_inr": round(audit["cost_breakdown"]["stt"]["cost_inr"], 2),
            },
            "llm_conversation": {
                "provider": audit["cost_breakdown"]["llm_conversation"]["provider"],
                "model": audit["cost_breakdown"]["llm_conversation"]["model"],
                "prompt_tokens": audit["cost_breakdown"]["llm_conversation"]["prompt_tokens"],
                "completion_tokens": audit["cost_breakdown"]["llm_conversation"]["completion_tokens"],
                "cached_tokens": audit["cost_breakdown"]["llm_conversation"]["cached_tokens"],
                "cost_inr": round(audit["cost_breakdown"]["llm_conversation"]["cost_inr"], 2),
            },
            "llm_extraction": {
                "provider": audit["cost_breakdown"]["llm_extraction"]["provider"],
                "model": audit["cost_breakdown"]["llm_extraction"]["model"],
                "prompt_tokens": audit["cost_breakdown"]["llm_extraction"]["prompt_tokens"],
                "completion_tokens": audit["cost_breakdown"]["llm_extraction"]["completion_tokens"],
                "cost_inr": round(audit["cost_breakdown"]["llm_extraction"]["cost_inr"], 2),
            },
            "tts": {
                "provider": audit["cost_breakdown"]["tts"]["provider"],
                "model": audit["cost_breakdown"]["tts"]["model"],
                "characters": audit["cost_breakdown"]["tts"]["characters"],
                "rate_usd_per_10k": audit["cost_breakdown"]["tts"].get("rate_usd_per_10k"),
                "cost_inr": round(audit["cost_breakdown"]["tts"]["cost_inr"], 2),
            },
        },
        "total_cost_inr": round(audit["total_cost_inr"], 2),
        "cost_per_minute_inr": round(audit["cost_per_minute_inr"], 2),
        "profit_inr": round(audit["profit_inr"], 2),
        "margin_pct": round(audit["margin_pct"], 1),
        "margin_status": audit["margin_status"],
        "post_call_breakdown": audit.get("post_call_breakdown", {}),
        "post_call_cost_inr": round(audit.get("post_call_cost_inr", 0.0), 2),
        "total_all_in_cost_inr": round(audit.get("total_all_in_cost_inr", 0.0), 2),
        "notices": audit.get("notices", []),
    }


@router.post("/call-audit/simulate")
async def simulate_call_audit(
    req: SimulateCallAuditRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    """
    Interactive 'What-If' simulator: calculates baseline economics and simulated overrides delta.
    """
    call_log, customer, workflow_run_data = await _fetch_run_audit_context(req.dograh_run_id, db)

    if not call_log and not workflow_run_data:
        raise HTTPException(404, f"Call run {req.dograh_run_id} not found")

    base_overrides = {}
    if customer and customer.report_settings:
        base_overrides.update(customer.report_settings.get("profitability_overrides", {}))

    call_log_dict = {
        "dograh_run_id": req.dograh_run_id,
        "customer_id": call_log.customer_id if call_log else (customer.id if customer else None),
        "company_name": customer.company_name if customer else None,
        "duration_seconds": call_log.duration_seconds if call_log else workflow_run_data.get("duration_seconds", 0),
        "cost_to_customer_paise": call_log.cost_to_customer_paise if call_log else 0,
        "call_status": "completed" if (call_log and (call_log.duration_seconds or 0) > 0) else workflow_run_data.get("status", "completed"),
        "call_type": call_log.call_type if call_log else "outbound",
        "caller_number": call_log.caller_number if call_log else None,
        "customer_currency": (customer.report_settings or {}).get("currency", "INR") if customer else "INR",
    }

    baseline = calculate_call_unit_economics(call_log_dict, workflow_run_data, base_overrides)

    sim_overrides = dict(base_overrides)
    tts_prov = req.override_tts_provider or req.override_tts_model
    if tts_prov:
        sim_overrides["tts_provider"] = tts_prov
    if req.override_llm_model:
        sim_overrides["llm_model"] = req.override_llm_model
    stt_prov = req.override_stt_provider or req.override_stt_model
    if stt_prov:
        sim_overrides["stt_provider"] = stt_prov
    fx = req.override_usd_inr or req.override_exchange_rate
    if fx:
        sim_overrides["exchange_rate"] = fx
    plivo_rate = req.override_plivo_rate_inr or req.override_telephony_cost_per_min_inr
    if plivo_rate is not None:
        sim_overrides["plivo_rate_inr"] = plivo_rate

    simulated = calculate_call_unit_economics(call_log_dict, workflow_run_data, sim_overrides)

    delta = {
        "total_cost_delta_inr": round(simulated["total_cost_inr"] - baseline["total_cost_inr"], 4),
        "cost_per_minute_delta_inr": round(simulated["cost_per_minute_inr"] - baseline["cost_per_minute_inr"], 4),
        "profit_delta_inr": round(simulated["profit_inr"] - baseline["profit_inr"], 4),
        "margin_pct_delta": round(simulated["margin_pct"] - baseline["margin_pct"], 2),
    }

    return {
        "dograh_run_id": req.dograh_run_id,
        "baseline": baseline,
        "simulated": simulated,
        "delta": delta,
    }


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

# --- FACTORY RESET ---

@router.post("/customers/{customer_id}/factory-reset-logs")
async def factory_reset_logs(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin)
):
    """
    Factory-reset call logs and wallet data for a single customer/org.
    
    Talkar DB (steps 5 & 6):
      - Deletes all CallLog rows for this customer.
      - Deletes all WalletTransaction rows for this customer.
      - Resets Wallet.balance_paise to 0.

    Voice Agent / Dograh DB (steps 1-4):
      - Deletes WorkflowRunModel (cascades webhooks, embed sessions, run text sessions).
      - Deletes child QueuedRunModel rows (parent_queued_run_id IS NOT NULL).
      - Deletes parent QueuedRunModel rows.
      - Deletes CampaignModel rows.
    
    All operations are scoped strictly to this customer's dograh_org_id.
    """
    import logging
    logger = logging.getLogger(__name__)

    # 1. Load customer
    res = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    dograh_org_id = customer.dograh_org_id
    results = {}

    # ── VOICE AGENT DB (Dograh) ──────────────────────────────────────
    if dograh_org_id:
        try:
            from services.dograh_client import DograhSessionLocal
            async with DograhSessionLocal() as ddb:
                # Step 1: WorkflowRunModel
                # workflow_runs has no organization_id — it links via workflow_id → workflows.organization_id
                r = await ddb.execute(
                    text("""
                        DELETE FROM workflow_runs
                        WHERE workflow_id IN (
                            SELECT id FROM workflows WHERE organization_id = :org_id
                        )
                    """),
                    {"org_id": dograh_org_id}
                )
                results["workflow_runs_deleted"] = r.rowcount

                # Step 2: Child QueuedRuns (parent_queued_run_id IS NOT NULL)
                # queued_runs has no organization_id — links via campaign_id → campaigns.organization_id
                r = await ddb.execute(
                    text("""
                        DELETE FROM queued_runs
                        WHERE campaign_id IN (
                            SELECT id FROM campaigns WHERE organization_id = :org_id
                        )
                        AND parent_queued_run_id IS NOT NULL
                    """),
                    {"org_id": dograh_org_id}
                )
                results["child_queued_runs_deleted"] = r.rowcount

                # Step 3: Parent QueuedRuns
                r = await ddb.execute(
                    text("""
                        DELETE FROM queued_runs
                        WHERE campaign_id IN (
                            SELECT id FROM campaigns WHERE organization_id = :org_id
                        )
                        AND parent_queued_run_id IS NULL
                    """),
                    {"org_id": dograh_org_id}
                )
                results["parent_queued_runs_deleted"] = r.rowcount

                # Step 4: Campaigns — campaigns DOES have organization_id directly
                r = await ddb.execute(
                    text("DELETE FROM campaigns WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                results["campaigns_deleted"] = r.rowcount

                await ddb.commit()
        except Exception as e:
            logger.error(f"[FactoryReset] Voice Agent DB wipe failed for org {dograh_org_id}: {e}")
            raise HTTPException(500, f"Voice Agent DB reset failed: {str(e)}")
    else:
        results["voice_agent_db"] = "skipped — no dograh_org_id"

    # ── TALKAR DB ─────────────────────────────────────────────────────
    try:
        # Step 5: Call Logs
        r = await db.execute(
            delete(CallLog).where(CallLog.customer_id == customer_id)
        )
        results["call_logs_deleted"] = r.rowcount

        # Step 6a: Wallet Transactions
        r = await db.execute(
            delete(WalletTransaction).where(WalletTransaction.customer_id == customer_id)
        )
        results["wallet_transactions_deleted"] = r.rowcount

        # Step 6b: Reset wallet balance to 0
        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer_id))
        wallet = wallet_res.scalar_one_or_none()
        if wallet:
            wallet.balance_paise = 0
            results["wallet_balance_reset"] = True
        else:
            results["wallet_balance_reset"] = False

        # Step 7: Clear notifications
        r = await db.execute(
            delete(Notification).where(Notification.customer_id == customer_id)
        )
        results["notifications_deleted"] = r.rowcount

        # Step 8: Suspend and block calls (Blocker 32: Cascade to child sub-orgs)
        customer.status = "suspended"
        customer.suspended_at = func.now()
        existing_form = dict(customer.onboarding_form or {})
        existing_form.setdefault("suspension_reason", "zero_balance")
        customer.onboarding_form = existing_form

        if customer.dograh_org_id:
            try:
                from services import dograh_client
                await dograh_client.block_org_calls(customer.dograh_org_id)
                results["calls_blocked"] = True
            except Exception as e:
                logger.error(f"[FactoryReset] block_org_calls failed: {e}")
                results["calls_blocked"] = False

        if customer.billing_org_id is None:
            sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
            for sub in sub_orgs_res.scalars().all():
                sub.status = "suspended"
                sub.suspended_at = func.now()
                s_form = dict(sub.onboarding_form or {})
                s_form.setdefault("suspension_reason", "zero_balance")
                sub.onboarding_form = s_form
                if sub.dograh_org_id:
                    try:
                        from services import dograh_client
                        await dograh_client.block_org_calls(sub.dograh_org_id)
                    except Exception as e:
                        logger.error(f"[FactoryReset] block_org_calls failed for sub-org {sub.dograh_org_id}: {e}")

        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"[FactoryReset] Talkar DB wipe failed for customer {customer_id}: {e}")
        raise HTTPException(500, f"Talkar DB reset failed: {str(e)}")

    logger.warning(
        f"[FactoryReset] Admin '{current_admin.email}' wiped logs for customer {customer_id} "
        f"(org={dograh_org_id}). Results: {results}"
    )
    return {"status": "reset_complete", "customer_id": customer_id, "dograh_org_id": dograh_org_id, **results}


# --- Billing Dead-Letter Queue (DLQ) Management ---

@router.get("/billing/dlq")
async def get_billing_dlq(
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    """Inspect unbilled calls parked in the Redis dead-letter queue after retries were exhausted."""
    import json
    from services import redis_client
    r = await redis_client.get_redis()
    if not r:
        return {"items": [], "count": 0}
    try:
        raw_items = await r.lrange("talkar:billing:dlq", 0, 99)
        items = [json.loads(i) for i in raw_items if i]
        total_count = await r.llen("talkar:billing:dlq")
        return {"items": items, "count": total_count}
    except Exception as e:
        logger.error(f"Error reading billing DLQ: {e}")
        return {"items": [], "count": 0, "error": str(e)}


@router.post("/billing/dlq/replay")
async def replay_billing_dlq(
    current_admin: TalkarAdmin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Replay and re-process parked DLQ runs through Talkar billing deduction."""
    import json
    from services import redis_client
    from routers.billing import deduct_for_run, DograhDeductRequest

    r = await redis_client.get_redis()
    if not r:
        return {"replayed": 0, "failed": 0, "remaining": 0}

    replayed = 0
    failed = 0
    while True:
        item_raw = await r.lpop("talkar:billing:dlq")
        if not item_raw:
            break
        try:
            item = json.loads(item_raw)
            payload = item.get("payload") or {}
            run_id = payload.get("workflow_run_id") or item.get("workflow_run_id")
            org_id = payload.get("dograh_org_id") or payload.get("organization_id")
            if not run_id or not org_id:
                logger.error(f"Skipping malformed DLQ item: {item}")
                failed += 1
                continue

            req = DograhDeductRequest(
                workflow_run_id=int(run_id),
                duration_seconds=int(payload.get("duration_seconds", 0)),
                organization_id=int(org_id),
                mode=payload.get("mode") or payload.get("call_type"),
                workflow_id=payload.get("workflow_id"),
            )
            await deduct_for_run(data=req, db=db, _token="admin_replay")
            replayed += 1
        except Exception as e:
            logger.error(f"Failed to replay DLQ item {item_raw}: {e}")
            failed += 1
            # Re-enqueue back into DLQ so it is not lost
            await r.rpush("talkar:billing:dlq", item_raw)
            break

    remaining = await r.llen("talkar:billing:dlq")
    return {"replayed": replayed, "failed": failed, "remaining": remaining}


# --- HARD CUSTOMER ACCOUNT DELETION (Level 5 Destructive Action) ---

async def _evict_customer_redis_keys(dograh_org_id: Optional[int], customer_id: int):
    """Evict cached keys, rate limiters, locks, and concurrency slots for this customer."""
    try:
        from services import redis_client
        r = await redis_client.get_redis()
        if not r:
            return
        keys_to_del = [
            f"auto_recharge_lock:{customer_id}",
            f"auto_recharge_cooldown:{customer_id}",
            f"suborg_concurrency:{customer_id}",
        ]
        if dograh_org_id:
            keys_to_del.extend([
                f"concurrent_calls:{dograh_org_id}",
                f"rate_limit:{dograh_org_id}",
                f"org_concurrent_limit:{dograh_org_id}",
            ])
            try:
                pool_keys = await r.keys(f"from_number_pool:{dograh_org_id}:*")
                if pool_keys:
                    keys_to_del.extend(pool_keys)
            except Exception:
                pass
            try:
                cb_keys = await r.keys(f"cb_*:{dograh_org_id}*")
                if cb_keys:
                    keys_to_del.extend(cb_keys)
            except Exception:
                pass

        for k in keys_to_del:
            try:
                await r.delete(k)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[AccountDelete] Non-fatal Redis eviction error for customer {customer_id}: {e}")


async def _execute_customer_purge(target_id: int, db: AsyncSession) -> dict:
    """
    Executes full topological purge for a single customer across Voice Agent DB, Redis, and Talkar DB.
    Zero cross-tenant data loss guarantee.
    """
    c_res = await db.execute(select(Customer).where(Customer.id == target_id))
    cust = c_res.scalar_one_or_none()
    if not cust:
        return {}

    dograh_org_id = cust.dograh_org_id
    dograh_user_id = cust.dograh_user_id
    is_sub_org = cust.billing_org_id is not None
    company_name = cust.company_name

    deleted_stats = {
        "customer_id": target_id,
        "company_name": company_name,
        "dograh_org_id": dograh_org_id,
        "dograh_user_id": dograh_user_id,
        "dograh_user_purged": False,
        "is_sub_org": is_sub_org,
        "voice_agent_db": {},
        "talkar_db": {},
    }

    # ── PHASE 2: VOICE AGENT (DOGRAH) DB PURGE ──────────────────────────
    if dograh_org_id:
        try:
            from services.dograh_client import DograhSessionLocal
            async with DograhSessionLocal() as ddb:
                vdb_stats = {}

                # 2.1 Cancel running campaigns so workers halt
                r = await ddb.execute(
                    text("UPDATE campaigns SET state = 'cancelled' WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["campaigns_cancelled"] = r.rowcount

                # 2.2 Delete webhook deliveries
                r = await ddb.execute(
                    text("DELETE FROM webhook_deliveries WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["webhook_deliveries"] = r.rowcount

                # 2.3 Delete workflow run text sessions
                r = await ddb.execute(
                    text("""
                        DELETE FROM workflow_run_text_sessions 
                        WHERE workflow_run_id IN (
                            SELECT wr.id FROM workflow_runs wr
                            JOIN workflows w ON w.id = wr.workflow_id
                            WHERE w.organization_id = :org_id
                        )
                    """),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["workflow_run_text_sessions"] = r.rowcount

                # 2.4 Delete embed sessions
                r = await ddb.execute(
                    text("""
                        DELETE FROM embed_sessions 
                        WHERE workflow_run_id IN (
                            SELECT wr.id FROM workflow_runs wr
                            JOIN workflows w ON w.id = wr.workflow_id
                            WHERE w.organization_id = :org_id
                        ) OR embed_token_id IN (
                            SELECT id FROM embed_tokens WHERE organization_id = :org_id
                        )
                    """),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["embed_sessions"] = r.rowcount

                # 2.5 Delete workflow runs (must delete before campaigns and workflows!)
                r = await ddb.execute(
                    text("""
                        DELETE FROM workflow_runs 
                        WHERE workflow_id IN (
                            SELECT id FROM workflows WHERE organization_id = :org_id
                        )
                    """),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["workflow_runs"] = r.rowcount

                # 2.6 Delete child queued runs
                r = await ddb.execute(
                    text("""
                        DELETE FROM queued_runs 
                        WHERE campaign_id IN (
                            SELECT id FROM campaigns WHERE organization_id = :org_id
                        ) AND parent_queued_run_id IS NOT NULL
                    """),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["child_queued_runs"] = r.rowcount

                # 2.7 Delete parent queued runs
                r = await ddb.execute(
                    text("""
                        DELETE FROM queued_runs 
                        WHERE campaign_id IN (
                            SELECT id FROM campaigns WHERE organization_id = :org_id
                        ) AND parent_queued_run_id IS NULL
                    """),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["parent_queued_runs"] = r.rowcount

                # 2.8 Delete campaigns
                r = await ddb.execute(
                    text("DELETE FROM campaigns WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["campaigns"] = r.rowcount

                # 2.9 Delete embed tokens
                r = await ddb.execute(
                    text("DELETE FROM embed_tokens WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["embed_tokens"] = r.rowcount

                # 2.10 Delete agent triggers
                r = await ddb.execute(
                    text("DELETE FROM agent_triggers WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["agent_triggers"] = r.rowcount

                # 2.11 Delete workflow recordings
                r = await ddb.execute(
                    text("DELETE FROM workflow_recordings WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["workflow_recordings"] = r.rowcount

                # 2.12 Delete telephony phone numbers & configurations
                r = await ddb.execute(
                    text("DELETE FROM telephony_phone_numbers WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["telephony_phone_numbers"] = r.rowcount

                r = await ddb.execute(
                    text("DELETE FROM telephony_configurations WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["telephony_configurations"] = r.rowcount

                # 2.13 Break circular FK between workflows and workflow_definitions
                await ddb.execute(
                    text("UPDATE workflows SET released_definition_id = NULL WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )

                # 2.14 Delete workflow definitions
                r = await ddb.execute(
                    text("""
                        DELETE FROM workflow_definitions 
                        WHERE workflow_id IN (
                            SELECT id FROM workflows WHERE organization_id = :org_id
                        )
                    """),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["workflow_definitions"] = r.rowcount

                # 2.15 Delete workflows
                r = await ddb.execute(
                    text("DELETE FROM workflows WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["workflows"] = r.rowcount

                # 2.16 Delete folders
                r = await ddb.execute(
                    text("DELETE FROM folders WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["folders"] = r.rowcount

                # 2.17 Delete knowledge base chunks & documents
                r = await ddb.execute(
                    text("""
                        DELETE FROM knowledge_base_chunks 
                        WHERE document_id IN (
                            SELECT id FROM knowledge_base_documents WHERE organization_id = :org_id
                        )
                    """),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["knowledge_base_chunks"] = r.rowcount

                r = await ddb.execute(
                    text("DELETE FROM knowledge_base_documents WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["knowledge_base_documents"] = r.rowcount

                # 2.18 Delete tools & integrations
                r = await ddb.execute(
                    text("DELETE FROM tools WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["tools"] = r.rowcount

                r = await ddb.execute(
                    text("DELETE FROM integrations WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["integrations"] = r.rowcount

                # 2.19 Delete credentials, api keys, configurations, usage cycles
                r = await ddb.execute(
                    text("DELETE FROM api_keys WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["api_keys"] = r.rowcount

                r = await ddb.execute(
                    text("DELETE FROM organization_configurations WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["organization_configurations"] = r.rowcount

                r = await ddb.execute(
                    text("DELETE FROM organization_usage_cycles WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["organization_usage_cycles"] = r.rowcount

                r = await ddb.execute(
                    text("DELETE FROM external_credentials WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["external_credentials"] = r.rowcount

                # 2.20 & 2.21 User Reclamation
                users_in_org_res = await ddb.execute(
                    text("SELECT user_id FROM organization_users WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                org_user_ids = [row[0] for row in users_in_org_res.all()]
                if dograh_user_id and dograh_user_id not in org_user_ids:
                    org_user_ids.append(dograh_user_id)

                await ddb.execute(
                    text("UPDATE users SET selected_organization_id = NULL WHERE selected_organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                await ddb.execute(
                    text("DELETE FROM organization_users WHERE organization_id = :org_id"),
                    {"org_id": dograh_org_id}
                )

                purged_users_count = 0
                for uid in org_user_ids:
                    rem_res = await ddb.execute(
                        text("SELECT COUNT(*) FROM organization_users WHERE user_id = :uid"),
                        {"uid": uid}
                    )
                    rem_count = rem_res.scalar() or 0
                    if rem_count == 0:
                        await ddb.execute(
                            text("DELETE FROM user_configurations WHERE user_id = :uid"),
                            {"uid": uid}
                        )
                        await ddb.execute(
                            text("DELETE FROM users WHERE id = :uid"),
                            {"uid": uid}
                        )
                        purged_users_count += 1
                        if uid == dograh_user_id:
                            deleted_stats["dograh_user_purged"] = True

                vdb_stats["users_purged"] = purged_users_count

                # 2.22 Delete organizations row itself
                r = await ddb.execute(
                    text("DELETE FROM organizations WHERE id = :org_id"),
                    {"org_id": dograh_org_id}
                )
                vdb_stats["organizations"] = r.rowcount

                await ddb.commit()
                deleted_stats["voice_agent_db"] = vdb_stats
        except Exception as e:
            logger.error(f"[AccountDelete] Voice Agent DB wipe failed for customer {target_id} (org {dograh_org_id}): {e}")
            raise HTTPException(500, f"Voice Agent DB reset failed: {str(e)}")
    else:
        deleted_stats["voice_agent_db"] = "skipped — unprovisioned customer"

    # ── PHASE 3: REDIS STATE EVICTION ────────────────────────────────────
    await _evict_customer_redis_keys(dograh_org_id, target_id)

    # ── PHASE 4: TALKAR DB WIPE ──────────────────────────────────────────
    try:
        tdb_stats = {}

        # 4.1 Delete call logs
        r = await db.execute(delete(CallLog).where(CallLog.customer_id == target_id))
        tdb_stats["call_logs"] = r.rowcount

        # 4.2 Delete support requests
        r = await db.execute(delete(SupportRequest).where(SupportRequest.customer_id == target_id))
        tdb_stats["support_requests"] = r.rowcount

        # 4.3 Delete phone number requests
        r = await db.execute(delete(PhoneNumberRequest).where(PhoneNumberRequest.customer_id == target_id))
        tdb_stats["phone_number_requests"] = r.rowcount

        # 4.4 Delete phone numbers (before agents)
        r = await db.execute(delete(PhoneNumber).where(PhoneNumber.customer_id == target_id))
        tdb_stats["phone_numbers"] = r.rowcount

        # 4.5 Delete agents
        r = await db.execute(delete(Agent).where(Agent.customer_id == target_id))
        tdb_stats["agents"] = r.rowcount

        # 4.6 Delete invoices
        r = await db.execute(
            delete(Invoice).where(
                (Invoice.customer_id == target_id) | (Invoice.sub_org_id == target_id)
            )
        )
        tdb_stats["invoices"] = r.rowcount

        # 4.7 Wallet transactions
        if is_sub_org:
            r = await db.execute(
                update(WalletTransaction)
                .where(WalletTransaction.sub_org_id == target_id)
                .values(sub_org_id=None)
            )
            tdb_stats["wallet_transactions_unlinked"] = r.rowcount
            tdb_stats["wallet_transactions"] = 0
        else:
            r = await db.execute(
                delete(WalletTransaction).where(WalletTransaction.customer_id == target_id)
            )
            tdb_stats["wallet_transactions"] = r.rowcount

        # 4.8 Delete wallets
        r = await db.execute(delete(Wallet).where(Wallet.customer_id == target_id))
        tdb_stats["wallets"] = r.rowcount

        # 4.9 Delete subscriptions
        r = await db.execute(delete(Subscription).where(Subscription.customer_id == target_id))
        tdb_stats["subscriptions"] = r.rowcount

        # 4.10 Delete notifications
        r = await db.execute(delete(Notification).where(Notification.customer_id == target_id))
        tdb_stats["notifications"] = r.rowcount

        # 4.11 Delete automation audit log
        r = await db.execute(delete(AutomationAuditLog).where(AutomationAuditLog.customer_id == target_id))
        tdb_stats["automation_audit_log"] = r.rowcount

        # 4.13 Delete customer
        r = await db.execute(delete(Customer).where(Customer.id == target_id))
        tdb_stats["customers"] = r.rowcount

        await db.commit()
        deleted_stats["talkar_db"] = tdb_stats
        return deleted_stats
    except Exception as e:
        await db.rollback()
        logger.error(f"[AccountDelete] Talkar DB wipe failed for customer {target_id}: {e}")
        raise HTTPException(500, f"Talkar DB deletion failed: {str(e)}")


@router.delete("/customers/{customer_id}")
async def delete_customer_account(
    customer_id: int,
    cascade_suborgs: bool = Query(False, description="Cascade delete child sub-organizations if master"),
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    """
    Permanently delete a customer account across both Talkar DB and Voice Agent (Dograh) DB.
    Level 5 Destructive Action with strict multi-tenant isolation and zero cross-tenant data loss.
    """
    if customer_id <= 0:
        raise HTTPException(400, "Invalid customer_id")

    # 1. Fetch customer
    res = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    # 2. Check child sub-organizations
    suborgs_res = await db.execute(
        select(Customer).where(Customer.billing_org_id == customer_id)
    )
    suborgs = suborgs_res.scalars().all()
    if suborgs and not cascade_suborgs:
        sub_names = ", ".join([f"'{s.company_name}' (#{s.id})" for s in suborgs])
        raise HTTPException(
            400,
            f"Cannot delete account: This customer is a master billing account with {len(suborgs)} "
            f"active sub-organizations ({sub_names}). Please unlink or delete the sub-organizations "
            "first, or enable cascade deletion."
        )

    # 3. Check for active calls across master and any sub-orgs to be deleted
    target_org_ids = []
    if customer.dograh_org_id:
        target_org_ids.append(customer.dograh_org_id)
    if cascade_suborgs and suborgs:
        for s in suborgs:
            if s.dograh_org_id:
                target_org_ids.append(s.dograh_org_id)

    if target_org_ids:
        try:
            from services.dograh_client import DograhSessionLocal
            async with DograhSessionLocal() as ddb:
                active_calls_res = await ddb.execute(
                    text("""
                        SELECT COUNT(wr.id)
                        FROM workflow_runs wr
                        JOIN workflows w ON w.id = wr.workflow_id
                        WHERE w.organization_id IN :org_ids
                          AND wr.is_completed IS FALSE
                    """).bindparams(bindparam("org_ids", expanding=True)),
                    {"org_ids": target_org_ids}
                )
                active_count = active_calls_res.scalar() or 0
                if active_count > 0:
                    raise HTTPException(
                        409,
                        f"Cannot delete account: {active_count} active calls currently in progress. "
                        "Please wait for calls to terminate or end them before deleting."
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[AccountDelete] Error checking active calls: {e}")

    # 4. Soft lock customer(s) to prevent top-ups or new calls during wipe
    customer.status = "suspended"
    customer.suspended_at = func.now()
    if cascade_suborgs and suborgs:
        for s in suborgs:
            s.status = "suspended"
            s.suspended_at = func.now()
    await db.commit()

    # 5. Execute deletion
    deleted_suborgs = []
    if cascade_suborgs and suborgs:
        for s in suborgs:
            sub_res = await _execute_customer_purge(s.id, db)
            deleted_suborgs.append(sub_res)

    master_res = await _execute_customer_purge(customer_id, db)

    logger.warning(
        f"[AccountDelete] Admin '{current_admin.email}' PERMANENTLY deleted customer {customer_id} "
        f"('{customer.company_name}', org={customer.dograh_org_id}, cascade={cascade_suborgs}). "
        f"Master stats: {master_res}, Suborgs deleted: {len(deleted_suborgs)}"
    )

    return {
        "success": True,
        "message": f"Customer '{customer.company_name}' (#{customer_id}) and all associated resources permanently deleted.",
        "cascade_suborgs": cascade_suborgs,
        "deleted_suborgs_count": len(deleted_suborgs),
        "deleted_entities": master_res,
        "suborg_entities": deleted_suborgs,
    }


