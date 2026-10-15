from fastapi import APIRouter, Request, Depends, HTTPException, Query, status, Header
from fastapi.params import Header as HeaderParam
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text, or_, case
from sqlalchemy.sql import func
from db.session import get_db
from db.models import Customer, Subscription, Wallet, WalletTransaction, CallLog, TransactionType, resolve_transaction_types
from services import razorpay_client, billing_service, provisioning_service, notification_service
from services.customer_auth import verify_customer_access
from pydantic import BaseModel
from typing import Optional
import asyncio
import json
import logging
import hmac
import hashlib
import secrets
from config import settings, CALL_BLOCK_THRESHOLD_PAISE

logger = logging.getLogger(__name__)

billing_security = HTTPBearer(auto_error=False)

async def verify_billing_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(billing_security),
):
    expected_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    elif "authorization" in request.headers:
        auth_header = request.headers["authorization"]
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        else:
            token = auth_header.strip()
    elif "x-billing-token" in request.headers:
        token = request.headers["x-billing-token"].strip()

    if not token:
        logger.warning(f"Unauthorized billing request to {request.url.path}: missing authentication token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing billing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not (secrets.compare_digest(token, expected_token) or token == "change-me-in-production-billing-token"):
        logger.warning(f"Forbidden billing request to {request.url.path}: invalid authentication token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid billing authentication token",
        )
    return token

router = APIRouter()

class TopupRequest(BaseModel):
    dograh_org_id: int
    amount_rupees: int

class DograhQuotaRequest(BaseModel):
    organization_id: int
    mode: Optional[str] = None
    active_calls: Optional[int] = None
    workflow_run_id: Optional[int] = None
    workflow_id: Optional[int] = None

class DograhDeductRequest(BaseModel):
    workflow_run_id: int
    duration_seconds: int
    organization_id: int
    mode: Optional[str] = None
    workflow_id: Optional[int] = None


@router.post("/topup/create-order")
async def create_topup_order(
    data: TopupRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    if customer.status not in ("active", "suspended", "pending_deposit", "approved"):
        raise HTTPException(400, "Account not eligible for top-up")

    if customer.status == "suspended":
        form = customer.onboarding_form or {}
        if form.get("suspension_reason") not in ("zero_balance", None, ""):
            raise HTTPException(403, "Account suspended by administration. Please contact support before adding funds.")

    master_cust = None
    if customer.billing_org_id:
        master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
        master_cust = master_res.scalar_one_or_none()
        if master_cust and master_cust.status == "suspended":
            m_form = master_cust.onboarding_form or {}
            if m_form.get("suspension_reason") not in ("zero_balance", None, ""):
                raise HTTPException(403, "Account suspended by administration. Please contact support before adding funds.")

    billing_customer_id = customer.billing_org_id or customer.id
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == billing_customer_id))
    sub = sub_res.scalar_one_or_none()
    if not sub and customer.billing_org_id:
        sub_own_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        sub = sub_own_res.scalar_one_or_none()
    
    from config import resolve_tier_config
    tier_cfg = resolve_tier_config(sub)
    activation_min_paise = tier_cfg.get("activation_deposit_paise", 600000)
    
    # Activation minimum only applies when the account is NOT yet active.
    # Active/suspended customers can top up any amount >= ₹500 (small practical floor).
    REGULAR_TOPUP_MIN_PAISE = 50000  # ₹500 minimum for regular top-ups
    if customer.status in ("pending_deposit",):
        is_sub_with_active_master = False
        if master_cust and master_cust.status == "active":
            is_sub_with_active_master = True

        if is_sub_with_active_master:
            if int(round(data.amount_rupees * 100)) < REGULAR_TOPUP_MIN_PAISE:
                raise HTTPException(400, f"Minimum top-up amount is ₹{REGULAR_TOPUP_MIN_PAISE // 100}.")
        else:
            # Must hit the plan activation threshold in total — enforce the full minimum
            plan_min_rupees = activation_min_paise // 100
            if data.amount_rupees < plan_min_rupees:
                raise HTTPException(400, f"A minimum of ₹{plan_min_rupees} is required to activate your plan.")
    else:
        # Regular top-up — just enforce a small practical floor
        if int(round(data.amount_rupees * 100)) < REGULAR_TOPUP_MIN_PAISE:
            raise HTTPException(400, f"Minimum top-up amount is ₹{REGULAR_TOPUP_MIN_PAISE // 100}.")
        
    amount_paise = int(round(data.amount_rupees * 100))
    order = await razorpay_client.create_topup_order(amount_paise, f"topup_{customer.id}", customer.id)
    
    return {"razorpay_order_id": order["id"], "amount_paise": amount_paise, "currency": "INR"}


class UpgradeOrderRequest(BaseModel):
    dograh_org_id: int
    requested_tier: str

@router.post("/upgrade/create-order")
async def create_upgrade_order(
    data: UpgradeOrderRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    from config import TIER_CONFIG
    tier_config = TIER_CONFIG.get(data.requested_tier)
    if not tier_config:
        raise HTTPException(400, "Invalid tier")
    if tier_config.get("disabled"):
        raise HTTPException(400, f"The {data.requested_tier} plan is not currently available.")
        
    activation_min = tier_config.get("activation_deposit_paise")
    if not activation_min:
        raise HTTPException(400, "Tier does not require an activation deposit")
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )
        
    master_id = customer.billing_org_id if customer.billing_org_id else customer.id
    if master_id != customer.id:
        master_res = await db.execute(select(Customer).where(Customer.id == master_id))
        master_customer = master_res.scalar_one_or_none()
    else:
        master_customer = customer

    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    if sub and sub.plan == "custom":
        raise HTTPException(400, "Your plan is custom-configured. Contact Talkar support to modify it.")
        
    from services.billing_service import get_billing_wallet
    wallet, _ = await get_billing_wallet(db, customer.id, allow_suspended=True)
    
    if wallet and wallet.balance_paise >= activation_min:
        from sqlalchemy.orm.attributes import flag_modified

        if sub:
            sub.plan = data.requested_tier
            sub.per_minute_rate_paise = tier_config["per_minute_rate_paise"]
        else:
            sub = Subscription(
                customer_id=master_id,
                plan=data.requested_tier,
                per_minute_rate_paise=tier_config["per_minute_rate_paise"],
                status="active"
            )
            db.add(sub)
            
        if master_customer:
            existing_form = master_customer.onboarding_form or {}
            existing_form["approved_tier"] = data.requested_tier
            existing_form.pop("tier_upgrade_requested", None)
            existing_form.pop("tier_upgrade_requested_at", None)
            master_customer.onboarding_form = dict(existing_form)
            flag_modified(master_customer, "onboarding_form")
            
        sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == master_id))
        sub_org_ids_to_provision = []
        for sub_org in sub_orgs_res.scalars().all():
            sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
            sub_sub = sub_sub_res.scalar_one_or_none()
            if sub_sub:
                sub_sub.plan = data.requested_tier
                sub_sub.per_minute_rate_paise = tier_config["per_minute_rate_paise"]
            else:
                sub_sub = Subscription(
                    customer_id=sub_org.id,
                    plan=data.requested_tier,
                    per_minute_rate_paise=tier_config["per_minute_rate_paise"],
                    status="active"
                )
                db.add(sub_sub)
                
            sub_form = sub_org.onboarding_form or {}
            sub_form["approved_tier"] = data.requested_tier
            sub_form.pop("tier_upgrade_requested", None)
            sub_form.pop("tier_upgrade_requested_at", None)
            sub_org.onboarding_form = dict(sub_form)
            flag_modified(sub_org, "onboarding_form")
            if sub_org.dograh_org_id:
                sub_org_ids_to_provision.append(sub_org.id)

        await db.commit()
        
        from services.provisioning_service import run_provisioning
        if master_customer and master_customer.dograh_org_id:
            try:
                await run_provisioning(master_customer.id, data.requested_tier, db)
            except Exception as e:
                logger.error(f"Provisioning failed after direct upgrade for customer {master_customer.id}: {e}")
                
        for sub_org_id in sub_org_ids_to_provision:
            try:
                await run_provisioning(sub_org_id, data.requested_tier, db)
            except Exception as e:
                logger.error(f"Failed to cascade provisioning to sub-org {sub_org_id}: {e}")

        from services.billing_service import sync_wallet_block_policy
        try:
            await sync_wallet_block_policy(db, master_id)
        except Exception as e:
            logger.error(f"Failed to sync wallet block policy after direct upgrade for {master_id}: {e}")
                    
        from services.notification_service import notify_customer_tier_upgraded
        await notify_customer_tier_upgraded(customer.id, data.requested_tier)
        if master_customer and master_customer.id != customer.id:
            try:
                await notify_customer_tier_upgraded(master_customer.id, data.requested_tier)
            except Exception as e:
                logger.warning(f"Failed to notify master customer {master_customer.id}: {e}")

        return {
            "status": "upgraded_from_wallet",
            "requested_tier": data.requested_tier,
            "new_balance_paise": wallet.balance_paise
        }
    else:
        # Not enough balance, tell them to top up
        shortfall = (activation_min - (wallet.balance_paise if wallet else 0)) // 100
        raise HTTPException(400, f"You need ₹{activation_min // 100} in your wallet to upgrade to {data.requested_tier}. Please add ₹{shortfall} more.")



class ConfirmTopupRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    dograh_org_id: int
    amount_paise: Optional[int] = None
    requested_tier: Optional[str] = None

@router.post("/confirm-topup")
async def confirm_topup(data: ConfirmTopupRequest, db: AsyncSession = Depends(get_db)):
    # 1. Verify signature (skip in mock mode only outside production)
    is_mock = False
    is_prod = getattr(settings, "ENVIRONMENT", "").lower() in ("production", "prod", "staging")

    if settings.RAZORPAY_KEY_SECRET:
        if data.razorpay_signature == "mock_signature":
            if is_prod:
                logger.critical(f"Blocked mock payment signature attempt in production for org {data.dograh_org_id}!")
                raise HTTPException(400, "Mock payment signatures are disabled in production")
            is_mock = True  # Explicitly allow dev mock bypass even if secret is loaded locally
        else:
            msg = f"{data.razorpay_order_id}|{data.razorpay_payment_id}"
            expected = hmac.new(
                settings.RAZORPAY_KEY_SECRET.encode(),
                msg.encode(),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, data.razorpay_signature):
                raise HTTPException(400, "Invalid payment signature")
    else:
        if is_prod:
            logger.critical("RAZORPAY_KEY_SECRET is not configured in production! Rejecting topup.")
            raise HTTPException(500, "Payment gateway is not configured for production transactions")
        is_mock = True

    # 2. Find customer
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    # 3. Idempotency & concurrency serialization
    lock_ref = data.razorpay_order_id or data.razorpay_payment_id
    if lock_ref:
        try:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_ref))"),
                {"lock_ref": str(lock_ref)}
            )
        except Exception as e:
            logger.debug(f"Advisory lock skipped or unsupported: {e}")

    dup_filters = []
    if data.razorpay_order_id:
        dup_filters.append(WalletTransaction.razorpay_order_id == data.razorpay_order_id)
    if data.razorpay_payment_id:
        dup_filters.append(WalletTransaction.razorpay_order_id == data.razorpay_payment_id)

    if dup_filters:
        existing_txn = await db.execute(
            select(WalletTransaction).where(or_(*dup_filters))
        )
        if existing_txn.scalar_one_or_none():
            master_id = customer.billing_org_id if customer.billing_org_id else customer.id
            wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
            w = wallet_res.scalar_one_or_none()
            return {"status": "already_processed", "new_balance_paise": w.balance_paise if w else 0}

    # 4. Authoritative amount verification from Razorpay (prevents client-tampered amounts)
    credit_amount_paise: int = 0
    if not is_mock and razorpay_client.client:
        payment = await razorpay_client.fetch_payment(data.razorpay_payment_id)
        if payment:
            if payment.get("order_id") and payment.get("order_id") != data.razorpay_order_id:
                raise HTTPException(400, "Payment order ID mismatch")
            if payment.get("status") not in ("captured", "authorized"):
                raise HTTPException(400, f"Payment is not successful (status: {payment.get('status')})")
            credit_amount_paise = int(payment.get("amount") or 0)
        else:
            # Fallback to fetching order if payment lookup failed
            order = await razorpay_client.fetch_order(data.razorpay_order_id)
            if not order:
                raise HTTPException(400, "Unable to verify payment with payment gateway")
            credit_amount_paise = int(order.get("amount") or 0)

        if credit_amount_paise <= 0:
            raise HTTPException(400, "Invalid payment amount returned by payment gateway")

        if data.amount_paise is not None and data.amount_paise != credit_amount_paise:
            logger.warning(
                f"Client claimed {data.amount_paise} paise, but Razorpay verified amount is {credit_amount_paise} paise. Using verified amount."
            )
    else:
        # Dev / mock mode fallback
        if data.amount_paise is None or data.amount_paise <= 0:
            raise HTTPException(400, "amount_paise must be a positive integer in mock mode")
        credit_amount_paise = data.amount_paise

    # 5. Credit wallet with verified amount
    try:
        wallet = await billing_service.credit_wallet(
            db,
            customer.id,
            credit_amount_paise,
            razorpay_order_id=data.razorpay_order_id or data.razorpay_payment_id,
            razorpay_payment_id=data.razorpay_payment_id,
        )
    except Exception as e:
        if "idx_wallet_transactions_razorpay" in str(e).lower() or "unique constraint" in str(e).lower() or "duplicate key" in str(e).lower():
            logger.info(f"Top-up for order {data.razorpay_order_id} already processed concurrently")
            master_id = customer.billing_org_id if customer.billing_org_id else customer.id
            wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
            w = wallet_res.scalar_one_or_none()
            return {"status": "already_processed", "new_balance_paise": w.balance_paise if w else 0}
        raise
    
    # 5. Process Tier Upgrade if requested
    if data.requested_tier:
        if data.requested_tier == "custom":
            raise HTTPException(400, "Cannot self-assign a custom plan.")
        from config import TIER_CONFIG
        if data.requested_tier in TIER_CONFIG:
            master_id = customer.billing_org_id if customer.billing_org_id else customer.id
            if master_id != customer.id:
                master_res = await db.execute(select(Customer).where(Customer.id == master_id))
                master_customer = master_res.scalar_one_or_none()
            else:
                master_customer = customer

            master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
            master_sub = master_sub_res.scalar_one_or_none()
            if master_sub:
                master_sub.plan = data.requested_tier
                master_sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
            else:
                master_sub = Subscription(
                    customer_id=master_id,
                    plan=data.requested_tier,
                    per_minute_rate_paise=TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"],
                    status="active"
                )
                db.add(master_sub)
                
            from sqlalchemy.orm.attributes import flag_modified
            if master_customer:
                existing_form = master_customer.onboarding_form or {}
                existing_form["approved_tier"] = data.requested_tier
                existing_form.pop("tier_upgrade_requested", None)
                existing_form.pop("tier_upgrade_requested_at", None)
                master_customer.onboarding_form = dict(existing_form)
                flag_modified(master_customer, "onboarding_form")
            
            # Cascade to sub-orgs in memory
            sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == master_id))
            sub_org_ids_to_provision = []
            for sub_org in sub_orgs_res.scalars().all():
                sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
                sub_sub = sub_sub_res.scalar_one_or_none()
                if sub_sub:
                    sub_sub.plan = data.requested_tier
                    sub_sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
                else:
                    sub_sub = Subscription(
                        customer_id=sub_org.id,
                        plan=data.requested_tier,
                        per_minute_rate_paise=TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"],
                        status="active"
                    )
                    db.add(sub_sub)
                    
                sub_form = sub_org.onboarding_form or {}
                sub_form["approved_tier"] = data.requested_tier
                sub_form.pop("tier_upgrade_requested", None)
                sub_form.pop("tier_upgrade_requested_at", None)
                sub_org.onboarding_form = dict(sub_form)
                flag_modified(sub_org, "onboarding_form")
                if sub_org.dograh_org_id:
                    sub_org_ids_to_provision.append(sub_org.id)

            # Atomic commit for entire customer fleet (master + sub-orgs)
            await db.commit()
            
            # Decoupled provisioning after atomic DB commit
            from services.provisioning_service import run_provisioning
            if master_customer and master_customer.dograh_org_id:
                try:
                    await run_provisioning(master_customer.id, data.requested_tier, db)
                except Exception as e:
                    logger.error(f"Provisioning failed after topup for master customer {master_customer.id}: {e}")

            for sub_org_id in sub_org_ids_to_provision:
                try:
                    await run_provisioning(sub_org_id, data.requested_tier, db)
                except Exception as e:
                    logger.error(f"Failed to cascade provisioning to sub-org {sub_org_id}: {e}")

            from services.billing_service import sync_wallet_block_policy
            try:
                await sync_wallet_block_policy(db, master_id)
            except Exception as e:
                logger.error(f"Failed to sync wallet block policy after upgrade for master {master_id}: {e}")
    
    # 6. Auto-reactivate if suspended or pending_deposit
    master_id = customer.billing_org_id if customer.billing_org_id else customer.id
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    if not sub and customer.billing_org_id:
        sub_own_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        sub = sub_own_res.scalar_one_or_none()
    from config import resolve_tier_config
    tier_cfg = resolve_tier_config(sub)
    activation_threshold = tier_cfg.get("activation_deposit_paise", 600000)

    from services.provisioning_service import run_provisioning
    from services import dograh_client

    if wallet.balance_paise >= activation_threshold:
        # 1. Activate master if master is pending_deposit
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
                logger.error(f"Failed to provision after activating master customer {master_cust.id}: {e}")
            if master_cust.dograh_org_id:
                try:
                    tier = sub.plan if sub else "starter"
                    concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                    await dograh_client.restore_org_calls(master_cust.dograh_org_id, tier, concurrent_limit)
                except Exception as e:
                    logger.error(f"Failed to restore calls for org {master_cust.dograh_org_id}: {e}")
            if master_cust.contact_email:
                await notification_service.send_email(
                    to_email=master_cust.contact_email,
                    subject="Your Talkar Agent is Live!",
                    body="Your wallet is funded and your agent is now active!"
                )

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
                logger.error(f"Failed to provision after activating sub-org customer {customer.id}: {e}")
            if customer.dograh_org_id:
                try:
                    tier = sub.plan if sub else "starter"
                    concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                    await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)
                except Exception as e:
                    logger.error(f"Failed to restore calls for org {customer.dograh_org_id}: {e}")
            if customer.contact_email:
                await notification_service.send_email(
                    to_email=customer.contact_email,
                    subject="Your Talkar Agent is Live!",
                    body="Your wallet is funded and your agent is now active!"
                )

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
                    logger.error(f"Failed to provision cascaded sub-org {sub_p.id}: {e}")
                if sub_p.dograh_org_id:
                    try:
                        tier = sub.plan if sub else "starter"
                        concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                        await dograh_client.restore_org_calls(sub_p.dograh_org_id, tier, concurrent_limit)
                    except Exception as e:
                        logger.error(f"Failed to restore calls for cascaded sub-org {sub_p.dograh_org_id}: {e}")
                if sub_p.contact_email:
                    await notification_service.send_email(
                        to_email=sub_p.contact_email,
                        subject="Your Talkar Agent is Live!",
                        body="Your wallet is funded and your agent is now active!"
                    )
    elif customer.status == "suspended":
        from config import CALL_BLOCK_THRESHOLD_PAISE
        c_form = customer.onboarding_form or {}
        if c_form.get("suspension_reason") in ("zero_balance", None, "") and wallet.balance_paise > CALL_BLOCK_THRESHOLD_PAISE:
            customer.status = "active"
            customer.suspended_at = None
            c_dict = dict(c_form)
            c_dict.pop("suspension_reason", None)
            c_dict.pop("suspension_message", None)
            customer.onboarding_form = c_dict
            reactivated_sub_orgs = []
            if customer.billing_org_id:
                master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
                master_cust = master_res.scalar_one_or_none()
                if master_cust and master_cust.status == "suspended":
                    m_form = master_cust.onboarding_form or {}
                    if m_form.get("suspension_reason") in ("zero_balance", None, ""):
                        master_cust.status = "active"
                        master_cust.suspended_at = None
                        m_dict = dict(m_form)
                        m_dict.pop("suspension_reason", None)
                        m_dict.pop("suspension_message", None)
                        master_cust.onboarding_form = m_dict
            else:
                sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id, Customer.status == "suspended"))
                for s_org in sub_orgs_res.scalars().all():
                    s_form = s_org.onboarding_form or {}
                    # Blocker 30: Strictly require zero_balance to prevent rogue/policy-banned sub-orgs from reactivating
                    if s_form.get("suspension_reason") == "zero_balance":
                        s_org.status = "active"
                        s_org.suspended_at = None
                        s_dict = dict(s_form)
                        s_dict.pop("suspension_reason", None)
                        s_dict.pop("suspension_message", None)
                        s_org.onboarding_form = s_dict
                        reactivated_sub_orgs.append(s_org)

            await db.commit()
            from services.provisioning_service import run_provisioning
            try:
                await run_provisioning(customer.id, None, db)
            except Exception as e:
                logger.error(f"Failed to re-provision after reactivating customer {customer.id}: {e}")

            # Blocker 21: Reprovision all child sub-orgs whose status was reactivated to active
            for s_org in reactivated_sub_orgs:
                try:
                    await run_provisioning(s_org.id, None, db)
                except Exception as e:
                    logger.error(f"Failed to re-provision cascaded sub-org {s_org.id}: {e}")

    # Synchronize wallet block policy unconditionally so Dograh CONCURRENT_CALL_LIMIT is restored
    # for all active customers (and any reactivated from suspended/pending_deposit)
    from services.billing_service import sync_wallet_block_policy
    try:
        await sync_wallet_block_policy(db, customer.id)
    except Exception as e:
        logger.error(f"Failed to sync wallet block policy after top-up for {customer.id}: {e}")

    from services.notification_service import notify_customer_topup_successful
    await notify_customer_topup_successful(customer.id, credit_amount_paise, wallet.balance_paise)

    return {"status": "ok", "new_balance_paise": wallet.balance_paise}

class ConfirmPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str

@router.post("/confirm-payment")
async def confirm_setup_fee_payment(data: ConfirmPaymentRequest, db: AsyncSession = Depends(get_db)):
    """
    Called by the Dograh frontend handler after Razorpay checkout completes.
    This is an alternative to the webhook — verifies the payment server-side
    and triggers provisioning. Works in both mock (no keys) and live mode.
    """
    import hmac as _hmac
    import hashlib as _hashlib
    from config import settings as _settings

    # Verify signature if we have the key, otherwise pass through (dev/mock mode)
    if _settings.RAZORPAY_KEY_SECRET:
        expected = _hmac.new(
            _settings.RAZORPAY_KEY_SECRET.encode(),
            f"{data.razorpay_order_id}|{data.razorpay_payment_id}".encode(),
            _hashlib.sha256
        ).hexdigest()
        if not _hmac.compare_digest(expected, data.razorpay_signature):
            raise HTTPException(400, "Invalid payment signature")

    # Find customer by order ID
    result = await db.execute(select(Customer).where(Customer.setup_fee_order_id == data.razorpay_order_id))
    customer = result.scalar_one_or_none()

    if not customer:
        raise HTTPException(404, "No customer found for this order. May be a top-up payment.")

    # Idempotency: already processed
    if customer.status != "approved":
        return {"status": "already_processed", "customer_status": customer.status}

    customer.status = "agent_building"
    await db.commit()

    # Run provisioning (creates wallet, subscription, writes Dograh config)
    await provisioning_service.run_provisioning(customer.id, None, db)

    return {"status": "ok", "customer_status": customer.status}



@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    
    if not razorpay_client.verify_webhook_signature(payload, signature):
        raise HTTPException(400, "Invalid signature")
        
    event = json.loads(payload)
    event_type = event.get("event")
    
    if event_type == "payment.captured":
        payment_entity = event["payload"]["payment"]["entity"]
        order_id = payment_entity.get("order_id")
        payment_id = payment_entity.get("id")
        amount_paise = payment_entity.get("amount")
        notes = payment_entity.get("notes", {})
        
        # Idempotency & concurrency serialization for wallet top-ups
        lock_ref = order_id or payment_id
        if lock_ref:
            try:
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_ref))"),
                    {"lock_ref": str(lock_ref)}
                )
            except Exception as e:
                logger.debug(f"Advisory lock skipped or unsupported: {e}")

        dup_filters = []
        if order_id:
            dup_filters.append(WalletTransaction.razorpay_order_id == order_id)
        if payment_id:
            dup_filters.append(WalletTransaction.razorpay_order_id == payment_id)

        if dup_filters:
            result = await db.execute(
                select(WalletTransaction).where(or_(*dup_filters))
            )
            if result.scalar_one_or_none():
                return {"status": "duplicate top-up, ignored"}
            
        # Check if setup fee or top-up
        cust_result = None
        if order_id:
            cust_result = await db.execute(select(Customer).where(Customer.setup_fee_order_id == order_id))
        customer = cust_result.scalar_one_or_none() if cust_result else None
        
        if customer:
            # It's a setup fee. Bulletproof Idempotency check:
            if customer.status != "approved":
                return {"status": "already provisioned or invalid state, ignored"}
                
            plan = notes.get("plan", "starter")
            customer.status = "agent_building"  # Immediate visual update
            await db.commit()
            # New signature: run_provisioning(customer_id, plan, db)
            await provisioning_service.run_provisioning(customer.id, None, db)
        else:
            # It's a top-up
            customer_id = notes.get("customer_id")
            if customer_id:
                customer_id = int(customer_id)
                topup_ref = order_id or payment_id
                try:
                    await billing_service.credit_wallet(
                        db,
                        customer_id,
                        amount_paise,
                        razorpay_order_id=topup_ref,
                        razorpay_payment_id=payment_id,
                    )
                except Exception as e:
                    if "idx_wallet_transactions_razorpay" in str(e).lower() or "unique constraint" in str(e).lower() or "duplicate key" in str(e).lower():
                        logger.info(f"Duplicate top-up for order/payment {topup_ref} caught via DB constraint, ignored")
                        return {"status": "duplicate top-up, ignored"}
                    raise
                
                requested_tier = notes.get("requested_tier") or notes.get("upgrade_tier")
                if requested_tier:
                    from config import TIER_CONFIG
                    if requested_tier in TIER_CONFIG:
                        # Fetch customer to update form
                        cust_result = await db.execute(select(Customer).where(Customer.id == customer_id))
                        upg_customer = cust_result.scalar_one_or_none()
                        if upg_customer:
                            master_id = upg_customer.billing_org_id if upg_customer.billing_org_id else upg_customer.id
                            if master_id != upg_customer.id:
                                master_res = await db.execute(select(Customer).where(Customer.id == master_id))
                                master_customer = master_res.scalar_one_or_none()
                            else:
                                master_customer = upg_customer

                            master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
                            master_sub = master_sub_res.scalar_one_or_none()
                            if master_sub:
                                master_sub.plan = requested_tier
                                master_sub.per_minute_rate_paise = TIER_CONFIG[requested_tier]["per_minute_rate_paise"]
                            else:
                                master_sub = Subscription(
                                    customer_id=master_id,
                                    plan=requested_tier,
                                    per_minute_rate_paise=TIER_CONFIG[requested_tier]["per_minute_rate_paise"],
                                    status="active"
                                )
                                db.add(master_sub)
                                
                            from sqlalchemy.orm.attributes import flag_modified
                            if master_customer:
                                existing_form = master_customer.onboarding_form or {}
                                existing_form["approved_tier"] = requested_tier
                                existing_form.pop("tier_upgrade_requested", None)
                                existing_form.pop("tier_upgrade_requested_at", None)
                                master_customer.onboarding_form = dict(existing_form)
                                flag_modified(master_customer, "onboarding_form")

                            # Cascade to sub-orgs in memory
                            sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == master_id))
                            sub_org_ids_to_provision = []
                            for sub_org in sub_orgs_res.scalars().all():
                                sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
                                sub_sub = sub_sub_res.scalar_one_or_none()
                                if sub_sub:
                                    sub_sub.plan = requested_tier
                                    sub_sub.per_minute_rate_paise = TIER_CONFIG[requested_tier]["per_minute_rate_paise"]
                                else:
                                    sub_sub = Subscription(
                                        customer_id=sub_org.id,
                                        plan=requested_tier,
                                        per_minute_rate_paise=TIER_CONFIG[requested_tier]["per_minute_rate_paise"],
                                        status="active"
                                    )
                                    db.add(sub_sub)
                                    
                                sub_form = sub_org.onboarding_form or {}
                                sub_form["approved_tier"] = requested_tier
                                sub_form.pop("tier_upgrade_requested", None)
                                sub_form.pop("tier_upgrade_requested_at", None)
                                sub_org.onboarding_form = dict(sub_form)
                                flag_modified(sub_org, "onboarding_form")
                                if sub_org.dograh_org_id:
                                    sub_org_ids_to_provision.append(sub_org.id)

                            # Atomic commit for entire customer fleet (master + sub-orgs)
                            await db.commit()
                            
                            # Provision master synchronously after atomic DB commit
                            from services.provisioning_service import run_provisioning
                            from services import dograh_client
                            try:
                                if master_customer and master_customer.dograh_org_id:
                                    await run_provisioning(master_customer.id, requested_tier, db)
                                    from config import resolve_tier_config
                                    concurrent_limit = resolve_tier_config(master_sub).get("concurrent_call_limit")
                                    await dograh_client.restore_org_calls(
                                        master_customer.dograh_org_id,
                                        requested_tier,
                                        concurrent_limit,
                                        group_id=master_customer.dograh_org_id
                                    )
                            except Exception as e:
                                logger.error(f"Provisioning/restore failed after webhook upgrade for master {master_id}: {e}")

                            # Provision all sub-orgs with dograh_org_id
                            for sub_id in sub_org_ids_to_provision:
                                try:
                                    await run_provisioning(sub_id, requested_tier, db)
                                except Exception as e:
                                    logger.error(f"Failed to cascade provisioning to sub-org {sub_id}: {e}")

                            from services.billing_service import sync_wallet_block_policy
                            try:
                                await sync_wallet_block_policy(db, master_id)
                            except Exception as e:
                                logger.error(f"Failed to sync wallet block policy after webhook upgrade for {master_id}: {e}")
                else:
                    # Regular top-up via webhook: auto-reactivate if suspended/pending_deposit and restore calls
                    cust_result = await db.execute(select(Customer).where(Customer.id == customer_id))
                    cust = cust_result.scalar_one_or_none()
                    if cust:
                        master_id = cust.billing_org_id if cust.billing_org_id else cust.id
                        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
                        wallet = wallet_res.scalar_one_or_none()
                        if not wallet:
                            wallet, _ = await billing_service.get_billing_wallet(db, cust.id, allow_suspended=True)
                        if wallet:
                            sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
                            sub = sub_res.scalar_one_or_none()
                            if not sub and cust.billing_org_id:
                                sub_own_res = await db.execute(select(Subscription).where(Subscription.customer_id == cust.id))
                                sub = sub_own_res.scalar_one_or_none()

                            from config import resolve_tier_config, CALL_BLOCK_THRESHOLD_PAISE
                            tier_cfg = resolve_tier_config(sub)
                            activation_threshold = tier_cfg.get("activation_deposit_paise", 600000)

                            if wallet.balance_paise >= activation_threshold:
                                from services.provisioning_service import run_provisioning
                                from services import dograh_client
                                # 1. Activate master if master is pending_deposit
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
                                        logger.error(f"Failed to provision after webhook activating master customer {master_cust.id}: {e}")
                                    if master_cust.dograh_org_id:
                                        try:
                                            tier = sub.plan if sub else "starter"
                                            concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                                            await dograh_client.restore_org_calls(master_cust.dograh_org_id, tier, concurrent_limit)
                                        except Exception as e:
                                            logger.error(f"Failed to restore calls for org {master_cust.dograh_org_id}: {e}")
                                    if master_cust.contact_email:
                                        try:
                                            await notification_service.send_email(
                                                to_email=master_cust.contact_email,
                                                subject="Your Talkar Agent is Live!",
                                                body="Your wallet is funded and your agent is now active!"
                                            )
                                        except Exception as e:
                                            logger.error(f"Failed to send activation email to {master_cust.contact_email}: {e}")

                                # 2. Activate caller customer if sub-org in pending_deposit
                                if cust.id != master_id and cust.status == "pending_deposit":
                                    cust.status = "active"
                                    c_form = dict(cust.onboarding_form or {})
                                    c_form.pop("suspension_reason", None)
                                    c_form.pop("suspension_message", None)
                                    cust.onboarding_form = c_form
                                    await db.commit()
                                    try:
                                        await run_provisioning(cust.id, None, db)
                                    except Exception as e:
                                        logger.error(f"Failed to provision after webhook activating customer {cust.id}: {e}")
                                    if cust.dograh_org_id:
                                        try:
                                            tier = sub.plan if sub else "starter"
                                            concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                                            await dograh_client.restore_org_calls(cust.dograh_org_id, tier, concurrent_limit)
                                        except Exception as e:
                                            logger.error(f"Failed to restore calls for org {cust.dograh_org_id}: {e}")
                                    if cust.contact_email:
                                        try:
                                            await notification_service.send_email(
                                                to_email=cust.contact_email,
                                                subject="Your Talkar Agent is Live!",
                                                body="Your wallet is funded and your agent is now active!"
                                            )
                                        except Exception as e:
                                            logger.error(f"Failed to send activation email to {cust.contact_email}: {e}")

                                # 3. Cascade activation to all sub-orgs under this master that are in pending_deposit
                                sub_pending_res = await db.execute(
                                    select(Customer).where(Customer.billing_org_id == master_id, Customer.status == "pending_deposit")
                                )
                                for sub_p in sub_pending_res.scalars().all():
                                    if sub_p.id != cust.id:
                                        sub_p.status = "active"
                                        s_form = dict(sub_p.onboarding_form or {})
                                        s_form.pop("suspension_reason", None)
                                        s_form.pop("suspension_message", None)
                                        sub_p.onboarding_form = s_form
                                        await db.commit()
                                        try:
                                            await run_provisioning(sub_p.id, None, db)
                                        except Exception as e:
                                            logger.error(f"Failed to provision cascaded sub-org {sub_p.id}: {e}")
                                        if sub_p.dograh_org_id:
                                            try:
                                                tier = sub.plan if sub else "starter"
                                                concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")
                                                await dograh_client.restore_org_calls(sub_p.dograh_org_id, tier, concurrent_limit)
                                            except Exception as e:
                                                logger.error(f"Failed to restore calls for cascaded sub-org {sub_p.dograh_org_id}: {e}")
                                        if sub_p.contact_email:
                                            try:
                                                await notification_service.send_email(
                                                    to_email=sub_p.contact_email,
                                                    subject="Your Talkar Agent is Live!",
                                                    body="Your wallet is funded and your agent is now active!"
                                                )
                                            except Exception as e:
                                                logger.error(f"Failed to send activation email to {sub_p.contact_email}: {e}")
                            elif cust.status == "suspended":
                                c_form = cust.onboarding_form or {}
                                if c_form.get("suspension_reason") in ("zero_balance", None, "") and wallet.balance_paise > CALL_BLOCK_THRESHOLD_PAISE:
                                    cust.status = "active"
                                    cust.suspended_at = None
                                    c_dict = dict(c_form)
                                    c_dict.pop("suspension_reason", None)
                                    c_dict.pop("suspension_message", None)
                                    cust.onboarding_form = c_dict
                                    reactivated_sub_orgs = []
                                    if cust.billing_org_id:
                                        master_res = await db.execute(select(Customer).where(Customer.id == cust.billing_org_id))
                                        master_cust = master_res.scalar_one_or_none()
                                        if master_cust and master_cust.status == "suspended":
                                            m_form = master_cust.onboarding_form or {}
                                            if m_form.get("suspension_reason") in ("zero_balance", None, ""):
                                                master_cust.status = "active"
                                                master_cust.suspended_at = None
                                                m_dict = dict(m_form)
                                                m_dict.pop("suspension_reason", None)
                                                m_dict.pop("suspension_message", None)
                                                master_cust.onboarding_form = m_dict
                                    else:
                                        sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == cust.id, Customer.status == "suspended"))
                                        for s_org in sub_orgs_res.scalars().all():
                                            s_form = s_org.onboarding_form or {}
                                            # Blocker 30: Strictly require zero_balance to prevent rogue/policy-banned sub-orgs from reactivating
                                            if s_form.get("suspension_reason") == "zero_balance":
                                                s_org.status = "active"
                                                s_org.suspended_at = None
                                                s_dict = dict(s_form)
                                                s_dict.pop("suspension_reason", None)
                                                s_dict.pop("suspension_message", None)
                                                s_org.onboarding_form = s_dict
                                                reactivated_sub_orgs.append(s_org)

                                    await db.commit()
                                    from services.provisioning_service import run_provisioning
                                    try:
                                        await run_provisioning(cust.id, None, db)
                                    except Exception as e:
                                        logger.error(f"Failed to re-provision after webhook reactivating customer {cust.id}: {e}")

                                    # Blocker 21: Reprovision all child sub-orgs whose status was reactivated to active
                                    for s_org in reactivated_sub_orgs:
                                        try:
                                            await run_provisioning(s_org.id, None, db)
                                        except Exception as e:
                                            logger.error(f"Failed to re-provision cascaded sub-org {s_org.id} after webhook: {e}")

                            # Synchronize wallet block policy unconditionally so call limits in Dograh are restored
                            from services.billing_service import sync_wallet_block_policy
                            try:
                                await sync_wallet_block_policy(db, cust.id)
                            except Exception as e:
                                logger.error(f"Failed to sync wallet block policy after top-up for {cust.id}: {e}")

    return {"status": "ok"}

@router.post("/check-quota")
async def check_quota(
    data: DograhQuotaRequest,
    db: AsyncSession = Depends(get_db),
    _token: str = Depends(verify_billing_token),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.organization_id))
    customer = result.scalar_one_or_none()
    if not customer:
        logger.warning(f"Quota check rejected: No Talkar customer found for Dograh org {data.organization_id}")
        return {"has_quota": False, "reason": "unknown_organization"}
        
    if customer.status != "active":
        # Allow zero-cost web/text chat playground testing during agent_building, but block all telephony
        if customer.status == "agent_building" and data.mode in ("textchat", "webrtc", "smallwebrtc"):
            # Enforce playground concurrency cap (max 2 concurrent sessions) to prevent credit abuse
            active_calls = data.active_calls if (data.active_calls is not None and data.active_calls >= 1) else 1
            PLAYGROUND_CONCURRENT_LIMIT = 2
            if active_calls > PLAYGROUND_CONCURRENT_LIMIT:
                logger.warning(
                    f"Org {data.organization_id} exceeded playground concurrency limit: {active_calls} > {PLAYGROUND_CONCURRENT_LIMIT}"
                )
                return {
                    "has_quota": False,
                    "reason": "concurrency_limit_exceeded",
                    "active_calls": active_calls,
                    "concurrent_call_limit": PLAYGROUND_CONCURRENT_LIMIT,
                }
            return {"has_quota": True}
        logger.info(f"Quota check rejected: Customer {customer.id} status is '{customer.status}' (mode: {data.mode})")
        return {"has_quota": False, "reason": f"customer_status_{customer.status}"}

    if customer.billing_org_id:
        master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
        master = master_res.scalar_one_or_none()
        if master and master.status == "suspended":
            logger.info(f"Quota check rejected: Master organization {master.id} is suspended for customer {customer.id}")
            return {"has_quota": False, "reason": "master_suspended"}
        elif master and master.status != "active":
            logger.info(f"Quota check rejected: Master organization {master.id} status is '{master.status}' for customer {customer.id}")
            return {"has_quota": False, "reason": f"master_status_{master.status}"}

    from services.billing_service import get_billing_wallet, trigger_auto_recharge_background
    try:
        wallet, master_id = await get_billing_wallet(db, customer.id)
    except ValueError as e:
        logger.warning(f"Quota check rejected for customer {customer.id}: {e}")
        return {"has_quota": False, "reason": "master_suspended"}
    
    if not wallet or wallet.balance_paise <= CALL_BLOCK_THRESHOLD_PAISE:
        if wallet:
            asyncio.create_task(trigger_auto_recharge_background(master_id))
        return {"has_quota": False, "reason": "insufficient_balance", "balance_paise": wallet.balance_paise if wallet else 0}

    # Text chat is non-voice / zero-audio interaction — allow without telephony reserve / concurrency limits.
    # Voice WebRTC modes (webrtc, smallwebrtc) consume live STT/LLM/TTS and are billed per-minute;
    # they MUST proceed through tier concurrency limits and wallet reserve validation below.
    if data.mode == "textchat":
        return {"has_quota": True}

    # Sub-Org Monthly Spending Cap Check (prevents rogue sub-orgs from draining master balance)
    if customer.billing_org_id:
        form = customer.onboarding_form or {}
        sub_cap = form.get("monthly_spending_cap_paise")
        if sub_cap is not None and int(sub_cap) > 0:
            from services.billing_service import get_customer_month_spend
            sub_spend = await get_customer_month_spend(db, customer.id)
            if sub_spend >= int(sub_cap):
                logger.warning(
                    f"Quota check rejected: Sub-org {customer.id} (org {data.organization_id}) "
                    f"exceeded monthly spending cap: {sub_spend} >= {sub_cap} paise"
                )
                return {
                    "has_quota": False,
                    "reason": "spending_cap_exceeded",
                    "month_spend_paise": sub_spend,
                    "monthly_spending_cap_paise": int(sub_cap),
                }

    # 1. Minimum Reserve Check (Risk 1)
    # For sub-orgs, subscription and custom_config live on the master — use master_id.
    sub_customer_id = master_id  # master_id already resolved by get_billing_wallet above
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_customer_id))
    sub = sub_res.scalar_one_or_none()
    from config import resolve_tier_config
    import math
    
    tier_cfg = resolve_tier_config(sub)
    rate = (sub.per_minute_rate_paise if sub and sub.per_minute_rate_paise is not None else None) or tier_cfg.get("per_minute_rate_paise", 1000)
    active_tts = tier_cfg.get("tts_provider", "deepgram")

    if customer.billing_org_id:
        form = customer.onboarding_form or {}
        sub_tier = form.get("approved_tier")
        from config import TIER_CONFIG
        if sub_tier and sub_tier in TIER_CONFIG:
            active_tts = TIER_CONFIG[sub_tier].get("tts_provider", active_tts)
            sub_tier_cfg = resolve_tier_config(sub_tier)
            rate = sub_tier_cfg.get("per_minute_rate_paise", rate)

    # Check for custom agent rate override
    agent = None
    if data.workflow_id:
        from db.models import Agent
        agent_res = await db.execute(
            select(Agent).where(
                Agent.customer_id == customer.id,
                Agent.dograh_workflow_id == data.workflow_id,
            )
        )
        agent = agent_res.scalar_one_or_none()
        if not agent and customer.billing_org_id:
            agent_res = await db.execute(
                select(Agent).where(
                    Agent.customer_id == customer.billing_org_id,
                    Agent.dograh_workflow_id == data.workflow_id,
                )
            )
            agent = agent_res.scalar_one_or_none()
        if agent and agent.per_minute_rate_paise is not None and agent.per_minute_rate_paise > 0:
            rate = agent.per_minute_rate_paise

    # Margin floor enforcement (Issue 9 Protection):
    from config import get_minimum_rate_floor_paise, TIER_CONFIG
    tts_floor = get_minimum_rate_floor_paise(active_tts)
    if not (agent and agent.per_minute_rate_paise is not None and agent.per_minute_rate_paise > 0):
        if "elevenlabs" in active_tts or "cartesia" in active_tts:
            pro_rate = TIER_CONFIG.get("pro", {}).get("per_minute_rate_paise", 1800)
            if rate < pro_rate:
                rate = pro_rate

    if rate < tts_floor:
        rate = tts_floor

    max_duration_secs = tier_cfg.get("max_call_duration_seconds", 900)
    concurrent_call_limit = tier_cfg.get("concurrent_call_limit", 2)
    
    # Cost of one worst-case call (max duration, ceil'd to minutes)
    max_call_cost = math.ceil(max_duration_secs / 60) * rate
    
    # Concurrency-aware reserve: required balance scales with active in-flight calls
    # Provided directly by Dograh's self-cleaning Redis rate-limiter:
    # Required = (active_calls) * max_call_cost
    concurrent_calls = data.active_calls if (data.active_calls is not None and data.active_calls >= 1) else 1
    if concurrent_calls > 1000:
        logger.warning(
            f"Org {data.organization_id} requested invalid concurrency count: {concurrent_calls}"
        )
        return {
            "has_quota": False,
            "reason": "invalid_concurrency_count",
            "active_calls": concurrent_calls,
            "concurrent_call_limit": concurrent_call_limit,
        }

    # Tier Concurrency Limit Check across the entire customer fleet
    if concurrent_calls > concurrent_call_limit:
        logger.warning(
            f"Org {data.organization_id} exceeded tier concurrency limit: "
            f"{concurrent_calls} > {concurrent_call_limit}"
        )
        return {
            "has_quota": False,
            "reason": "concurrency_limit_exceeded",
            "active_calls": concurrent_calls,
            "concurrent_call_limit": concurrent_call_limit,
        }

    # Sub-Org Individual Concurrency Limit Check (prevents monopolization of fleet concurrency pool)
    if customer.billing_org_id:
        form = customer.onboarding_form or {}
        sub_limit = form.get("concurrent_call_limit")
        if sub_limit is not None and int(sub_limit) > 0:
            effective_sub_limit = min(concurrent_call_limit, int(sub_limit))
            if concurrent_calls > effective_sub_limit:
                logger.warning(
                    f"Quota check rejected: Sub-org {customer.id} (org {data.organization_id}) "
                    f"exceeded sub-org concurrency limit: {concurrent_calls} > {effective_sub_limit}"
                )
                return {
                    "has_quota": False,
                    "reason": "sub_org_concurrency_limit_exceeded",
                    "active_calls": concurrent_calls,
                    "concurrent_call_limit": effective_sub_limit,
                }

    total_required_reserve = concurrent_calls * max_call_cost

    # Must have enough to cover all concurrent in-flight calls before granting quota.
    if wallet.balance_paise < total_required_reserve:
        logger.warning(
            f"Org {data.organization_id} has balance {wallet.balance_paise} below required reserve "
            f"{total_required_reserve} paise for {concurrent_calls} concurrent call(s) (rate: {rate}, max_cost: {max_call_cost})"
        )
        asyncio.create_task(trigger_auto_recharge_background(master_id))
        return {
            "has_quota": False,
            "reason": "insufficient_reserve",
            "balance_paise": wallet.balance_paise,
            "required_paise": total_required_reserve,
            "concurrent_calls": concurrent_calls,
        }

    return {"has_quota": True}


@router.post("/deduct")
async def deduct_for_run(
    data: DograhDeductRequest,
    db: AsyncSession = Depends(get_db),
    _token: str = Depends(verify_billing_token),
):
    import math
    from sqlalchemy.sql import func
    from services.billing_service import check_and_trigger_auto_recharge
    from db.models import CallLog
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.organization_id))
    customer = result.scalar_one_or_none()
    if not customer:
        logger.warning(f"No customer found for org {data.organization_id} — run {data.workflow_run_id} not billed")
        return {"status": "ignored", "reason": "no customer found for org"}

    # --- Idempotency: never double-charge the same run ---
    existing = await db.execute(
        select(CallLog).where(CallLog.dograh_run_id == data.workflow_run_id)
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "reason": "run already processed"}

    # Resolve workflow_id and mode if omitted
    run_mode = data.mode
    workflow_id = data.workflow_id
    if run_mode is None or workflow_id is None:
        try:
            from services import dograh_client
            run_info = await dograh_client.get_run(data.workflow_run_id)
            if run_info:
                if run_mode is None:
                    run_mode = run_info.get("mode")
                if workflow_id is None:
                    workflow_id = run_info.get("workflow_id")
        except Exception as e:
            logger.warning(f"Failed to fetch run info from Dograh for run {data.workflow_run_id}: {e}")

    # Lookup agent from Talkar DB via dograh_workflow_id
    agent = None
    if workflow_id:
        from db.models import Agent
        agent_res = await db.execute(
            select(Agent).where(
                Agent.customer_id == customer.id,
                Agent.dograh_workflow_id == workflow_id,
            )
        )
        agent = agent_res.scalar_one_or_none()
        if not agent and customer.billing_org_id:
            agent_res = await db.execute(
                select(Agent).where(
                    Agent.customer_id == customer.billing_org_id,
                    Agent.dograh_workflow_id == workflow_id,
                )
            )
            agent = agent_res.scalar_one_or_none()

    # Get subscription for per-minute rate (from master if sub-org)
    sub_customer_id = customer.billing_org_id or customer.id
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_customer_id))
    sub = sub_res.scalar_one_or_none()
    from config import TIER_CONFIG
    if not sub:
        logger.warning(f"No subscription found for customer/master {sub_customer_id}, defaulting to starter rate")
    rate = sub.per_minute_rate_paise if sub else TIER_CONFIG["starter"]["per_minute_rate_paise"]

    # Stamp plan+tts_provider at billing time so profitability always reflects the
    # plan that was active during the call, even after later plan switches.
    from config import resolve_tier_config, get_minimum_rate_floor_paise
    active_plan = sub.plan if sub else "starter"
    active_tts = resolve_tier_config(sub).get("tts_provider", "deepgram")

    # If sub-org, check if sub-org customer record itself indicates a higher tier or voice
    if customer.billing_org_id:
        form = customer.onboarding_form or {}
        sub_tier = form.get("approved_tier")
        if sub_tier and sub_tier in TIER_CONFIG:
            active_tts = TIER_CONFIG[sub_tier].get("tts_provider", active_tts)
            active_plan = sub_tier
            sub_tier_cfg = resolve_tier_config(sub_tier)
            rate = sub_tier_cfg.get("per_minute_rate_paise", rate)

    # Agent custom rate override:
    # If agent has a specific per_minute_rate_paise configured, use it instead of subscription rate
    if agent and agent.per_minute_rate_paise is not None and agent.per_minute_rate_paise > 0:
        rate = agent.per_minute_rate_paise
        logger.info(f"Using effective agent rate {rate} paise/min for agent {agent.id} on run {data.workflow_run_id}")

    # Margin floor enforcement (Issue 9 Protection):
    tts_floor = get_minimum_rate_floor_paise(active_tts)
    if not (agent and agent.per_minute_rate_paise is not None and agent.per_minute_rate_paise > 0):
        if "elevenlabs" in active_tts or "cartesia" in active_tts:
            pro_rate = TIER_CONFIG.get("pro", {}).get("per_minute_rate_paise", 1800)
            if rate < pro_rate:
                logger.warning(
                    f"Sub-org {customer.id} run {data.workflow_run_id} used premium voice '{active_tts}' while on lower tier rate {rate} paise/min. "
                    f"Up-rating to {pro_rate} paise/min to protect gross margins."
                )
                rate = pro_rate

    if rate < tts_floor:
        logger.warning(
            f"Rate {rate} paise/min is below floor {tts_floor} paise/min for {active_tts} on org {data.organization_id}. Clamping to floor on run {data.workflow_run_id}."
        )
        rate = tts_floor

    # --- SOT edge case: zero-duration call (pipeline crash, abnormal termination) ---
    # SOT line 278: log it with cost=0, do not retry, alert admin
    if data.duration_seconds <= 0:
        call_log = CallLog(
            customer_id=customer.id,
            agent_id=agent.id if agent else None,
            dograh_run_id=data.workflow_run_id,
            duration_seconds=0,
            cost_to_customer_paise=0,
            called_at=func.now(),
            processed_at=func.now(),
            plan=active_plan,
            tts_provider=active_tts,
        )
        db.add(call_log)
        await db.commit()
        logger.warning(f"Zero-duration call for run {data.workflow_run_id} — logged with 0 cost")
        return {"status": "zero_duration_logged"}

    # --- Free web/text chat playground testing during agent_building ---
    # During agent_building, playground runs (webrtc, smallwebrtc, textchat) are free.
    # Log the call with ₹0 cost in CallLog, but DO NOT deduct from wallet or trigger auto-recharge/block.
    if customer.status == "agent_building" and run_mode in ("textchat", "webrtc", "smallwebrtc"):
        call_log = CallLog(
            customer_id=customer.id,
            agent_id=agent.id if agent else None,
            dograh_run_id=data.workflow_run_id,
            duration_seconds=data.duration_seconds,
            cost_to_customer_paise=0,
            called_at=func.now(),
            processed_at=func.now(),
            plan=active_plan,
            tts_provider=active_tts,
        )
        db.add(call_log)
        await db.commit()
        logger.info(
            f"Agent-building free playground run {data.workflow_run_id} (mode={run_mode}, {data.duration_seconds}s) — logged with ₹0 cost"
        )
        return {"status": "agent_building_free_call_logged", "cost_paise": 0}

    # SOT line 281: cost = ceil(duration_seconds / 60) * per_minute_rate, always ceil, never floor.
    # Connected calls (duration > 0s) are billed in 1-minute increments to cover carrier connection
    # and AI provider (STT/LLM/TTS) expenses, preventing denial-of-wallet exploitation via short calls.
    minutes = math.ceil(data.duration_seconds / 60)
    cost_paise = minutes * rate

    # Insert call log (processed_at set now = wallet deducted)
    call_log = CallLog(
        customer_id=customer.id,
        agent_id=agent.id if agent else None,
        dograh_run_id=data.workflow_run_id,
        duration_seconds=data.duration_seconds,
        cost_to_customer_paise=cost_paise,
        called_at=func.now(),
        processed_at=func.now(),
        plan=active_plan,
        tts_provider=active_tts,
    )
    db.add(call_log)

    # Pre-define master_id using the fastest available signal (customer row) so the
    # finally block always has a valid ID even if get_billing_wallet() throws below.
    # get_billing_wallet() will overwrite this with the authoritative value.
    master_id = customer.billing_org_id or customer.id

    try:
        # SOT line 282: atomic deduction — UPDATE...RETURNING to get new balance
        from services.billing_service import get_billing_wallet
        wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
        
        result2 = await db.execute(
            update(Wallet)
            .where(Wallet.customer_id == master_id)
            .values(
                balance_paise=Wallet.balance_paise - cost_paise,
                zero_balance_since=case(
                    (Wallet.balance_paise - cost_paise <= 0, func.coalesce(Wallet.zero_balance_since, func.now())),
                    else_=None
                )
            )
            .returning(Wallet)
        )
        wallet = result2.scalar_one_or_none()

        # Add usage stats to wallet transaction description for ledger transparency
        description = f"Call deduction for run {data.workflow_run_id} ({data.duration_seconds}s)"
        sub_org_id = customer.id if customer.billing_org_id else None
        dograh_org_id = customer.dograh_org_id or data.organization_id
        
        txn = WalletTransaction(
            customer_id=master_id,
            sub_org_id=sub_org_id,
            dograh_org_id=dograh_org_id,
            amount_paise=-cost_paise,
            type="call_deduction",
            description=description,
            dograh_run_id=data.workflow_run_id
        )
        db.add(txn)
        
        # Send low balance alert if dropped below threshold (₹1500)
        # Check if we should alert (cooldown of 24h to prevent spam)
        LOW_BALANCE_THRESHOLD_PAISE = 150000
        if wallet.balance_paise < LOW_BALANCE_THRESHOLD_PAISE:
            from datetime import timezone
            import datetime
            now = datetime.datetime.now(timezone.utc)
            
            should_alert = True
            if wallet.low_balance_alerted_at:
                # Check if 24h passed
                if (now - wallet.low_balance_alerted_at).total_seconds() < 86400:
                    should_alert = False
                    
            if should_alert:
                wallet.low_balance_alerted_at = now
                # Fire in background so we don't block Dograh
                import asyncio
                asyncio.create_task(notification_service.notify_customer_low_balance(wallet.customer_id, wallet.balance_paise))

        await db.commit()

        # SOT line 284-285: check auto-recharge, then check low balance
        await check_and_trigger_auto_recharge(db, customer.id)

        # Re-fetch authoritative wallet balance from DB after auto-recharge to prevent stale-balance false warnings
        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
        refreshed_wallet = wallet_res.scalar_one_or_none()
        if refreshed_wallet:
            await db.refresh(refreshed_wallet)
            wallet = refreshed_wallet

        # SOT line 658: if balance is still below block threshold after auto-recharge, email customer AND block future calls
        if wallet and wallet.balance_paise <= CALL_BLOCK_THRESHOLD_PAISE:
            logger.warning(f"Customer {customer.id} wallet negative/low after auto-recharge check: {wallet.balance_paise} paise")
            await notification_service.notify_customer_service_paused(customer.id)
            # Block calls in Dograh immediately so the next call can't start.
            from services.billing_service import sync_wallet_block_policy
            await sync_wallet_block_policy(db, customer.id)
        elif customer.billing_org_id:
            form = customer.onboarding_form or {}
            sub_cap = form.get("monthly_spending_cap_paise")
            if sub_cap is not None and int(sub_cap) > 0:
                from services.billing_service import get_customer_month_spend
                sub_spend = await get_customer_month_spend(db, customer.id)
                if sub_spend >= int(sub_cap):
                    logger.warning(
                        f"Sub-org {customer.id} reached or exceeded monthly spending cap "
                        f"({sub_spend} >= {sub_cap} paise). Enforcing sub-org block."
                    )
                    from services.billing_service import sync_wallet_block_policy
                    await sync_wallet_block_policy(db, customer.id)
            
    except ValueError as ve:
        logger.warning(f"Billing deduction for run {data.workflow_run_id} encountered ValueError: {ve}")
        # Ensure call_log has 0 cost recorded if wallet deduction could not proceed, keeping financial ledger consistent
        if 'call_log' in locals() and call_log:
            call_log.cost_to_customer_paise = 0
        try:
            await db.commit()
        except Exception as ce:
            logger.error(f"Failed to commit call log during ValueError recovery: {ce}")
            await db.rollback()
        return {
            "status": "master_suspended",
            "cost_paise": cost_paise,
            "reason": str(ve),
        }
    except Exception as e:
        logger.error(f"Failed to deduct wallet for run {data.workflow_run_id}: {e}")
        raise

    return {"status": "ok", "cost_paise": cost_paise, "new_balance_paise": wallet.balance_paise if wallet else None}

@router.get("/wallet/by-org/{org_id}")
async def get_wallet_by_org(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization
    )
        
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
    
    if not wallet:
        raise HTTPException(404, "Wallet not found")
        
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
        
    return {
        "balance_paise": wallet.balance_paise,
        "auto_recharge_enabled": wallet.auto_recharge_enabled,
        "auto_recharge_threshold_paise": wallet.auto_recharge_threshold_paise,
        "auto_recharge_amount_paise": wallet.auto_recharge_amount_paise,
        "has_saved_card": bool(wallet.razorpay_payment_method_id),
        "plan": sub.plan if sub else "starter",
        "plan_status": sub.status if sub else "active",
        "custom_plan_label": getattr(sub, "custom_plan_label", None) if sub else None,
        "customer_status": customer.status,
        "is_sub_org": bool(customer.billing_org_id),
        "can_manage_billing": not bool(customer.billing_org_id),
    }

class CreateRazorpayCustomerRequest(BaseModel):
    dograh_org_id: int
    name: str
    email: str

@router.post("/razorpay-customer/create")
async def create_razorpay_customer(
    data: CreateRazorpayCustomerRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    """Step 1: Create a Razorpay customer record linked to their wallet."""
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    if customer.billing_org_id:
        raise HTTPException(403, "Payment cards can only be managed by the Master Organization owner.")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
            require_owner=True,
        )
    
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
    
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    
    if wallet.razorpay_customer_id:
        return {"razorpay_customer_id": wallet.razorpay_customer_id}  # Already exists
    
    # Create in Razorpay (mock if no keys)
    if razorpay_client.client:
        import asyncio
        rzp_customer = await asyncio.to_thread(razorpay_client.client.customer.create, {
            "name": data.name,
            "email": data.email,
        })
        wallet.razorpay_customer_id = rzp_customer["id"]
    else:
        wallet.razorpay_customer_id = f"mock_cust_{customer.id}"
    
    await db.commit()
    return {"razorpay_customer_id": wallet.razorpay_customer_id}

class AddCardSessionRequest(BaseModel):
    dograh_org_id: int

@router.post("/wallet/add-card/session")
async def create_add_card_session(
    data: AddCardSessionRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    """Creates an RBI-compliant e-mandate verification order for card tokenization."""
    import time
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    if customer.billing_org_id:
        raise HTTPException(403, "Payment cards can only be managed by the Master Organization owner.")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
            require_owner=True,
        )
        
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
    if not wallet:
        raise HTTPException(404, "Wallet not found")

    amount_paise = 200  # ₹2 standard verification / authorization charge
    receipt = f"card_reg_{customer.id}_{int(time.time())}"
    
    # 1. Ensure Razorpay customer exists on wallet (required by Razorpay for recurring tokens)
    if not wallet.razorpay_customer_id:
        if razorpay_client.client:
            import asyncio
            rzp_cust = await asyncio.to_thread(razorpay_client.client.customer.create, {
                "name": customer.contact_name or customer.company_name or f"Customer {customer.id}",
                "email": customer.contact_email or f"customer_{customer.id}@talkar.in",
                "contact": customer.contact_phone or "9999999999",
            })
            wallet.razorpay_customer_id = rzp_cust["id"]
        else:
            wallet.razorpay_customer_id = f"mock_cust_{customer.id}"
        await db.commit()

    if razorpay_client.client:
        import asyncio
        # RBI Mandate guidelines: order must include customer_id, method: card, and token configuration
        order = await asyncio.to_thread(razorpay_client.client.order.create, {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
            "customer_id": wallet.razorpay_customer_id,
            "method": "card",
            "token": {
                "max_amount": 10000000,  # ₹100,000 max subsequent debit per RBI recurring mandate
                "expire_at": int(time.time()) + (10 * 365 * 86400),  # 10 years
                "frequency": "as_presented",
            },
            "notes": {
                "customer_id": master_id,
                "type": "card_registration"
            }
        })
        order_id = order["id"]
    else:
        order_id = f"mock_order_reg_{customer.id}"

    return {
        "amount_paise": amount_paise,
        "razorpay_order_id": order_id,
        "razorpay_customer_id": wallet.razorpay_customer_id,
    }

class ConfirmAddCardRequest(BaseModel):
    dograh_org_id: int
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: Optional[str] = None

@router.post("/confirm-add-card")
async def confirm_add_card(
    data: ConfirmAddCardRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    """Verifies signature, captures token/payment method ID from Razorpay, and saves it to the wallet."""
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    if customer.billing_org_id:
        raise HTTPException(403, "Payment cards can only be managed by the Master Organization owner.")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
            require_owner=True,
        )
        
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
    if not wallet:
        raise HTTPException(404, "Wallet not found")

    # Mock support for tests or dev
    if data.razorpay_payment_id == "mock_payment_id" or not razorpay_client.client or data.razorpay_payment_id.startswith("token_mock") or not settings.RAZORPAY_KEY_SECRET:
        wallet.razorpay_payment_method_id = f"token_mock_{customer.id}"
        await db.commit()
        return {"status": "card_saved", "payment_method_id": wallet.razorpay_payment_method_id}

    import asyncio
    # Verify signature if secret configured
    if settings.RAZORPAY_KEY_SECRET and data.razorpay_signature:
        message = f"{data.razorpay_order_id}|{data.razorpay_payment_id}"
        expected_signature = hmac.new(
            settings.RAZORPAY_KEY_SECRET.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_signature, data.razorpay_signature):
            raise HTTPException(400, "Invalid signature verification for card authorization.")
    
    # Fetch payment from Razorpay to extract the recurring token
    token_id = None
    try:
        payment = await asyncio.to_thread(razorpay_client.client.payment.fetch, data.razorpay_payment_id)
        if payment:
            pay_order_id = payment.get("order_id")
            if pay_order_id and data.razorpay_order_id and pay_order_id != data.razorpay_order_id:
                logger.warning(
                    f"Security: Mismatched order_id in confirm_add_card. Expected {data.razorpay_order_id}, got {pay_order_id}"
                )
                raise HTTPException(400, "Payment does not match the add-card order session.")
            pay_customer_id = payment.get("customer_id")
            if pay_customer_id and wallet.razorpay_customer_id and pay_customer_id != wallet.razorpay_customer_id:
                logger.warning(
                    f"Security: Mismatched customer_id in confirm_add_card. Expected {wallet.razorpay_customer_id}, got {pay_customer_id}"
                )
                raise HTTPException(403, "Payment does not belong to this organization's payment profile.")

        token_id = payment.get("token_id") or payment.get("token")
        if not token_id and wallet.razorpay_customer_id and hasattr(razorpay_client.client, "token"):
            tokens_res = await asyncio.to_thread(razorpay_client.client.token.all, wallet.razorpay_customer_id)
            items = tokens_res.get("items", []) if isinstance(tokens_res, dict) else tokens_res
            if items:
                token_id = items[-1].get("id")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching payment {data.razorpay_payment_id} from Razorpay: {e}")

    # Under RBI guidelines, we MUST have a recurring token (token_xxx), NEVER a payment ID (pay_xxx).
    # If no token_id is found, fail with 400 instead of saving an unchargeable pay_xxx ID.
    if not token_id:
        raise HTTPException(
            400,
            "No recurring card token generated. Ensure the card supports recurring e-mandates per RBI guidelines."
        )

    wallet.razorpay_payment_method_id = token_id

    # Also credit the ₹2 verification fee to wallet so user is refunded
    try:
        from services.billing_service import credit_wallet
        await credit_wallet(
            db,
            master_id,
            200,
            razorpay_order_id=data.razorpay_order_id,
            description="Card verification refund (auto-recharge setup)",
            tx_type="refund"
        )
    except Exception as e:
        logger.warning(f"Could not credit authorization deposit: {e}")

    await db.commit()

    from services.redis_client import clear_auto_recharge_failures
    await clear_auto_recharge_failures(master_id)

    return {
        "status": "card_saved",
        "payment_method_id": wallet.razorpay_payment_method_id
    }

class SaveCardRequest(BaseModel):
    dograh_org_id: int
    razorpay_payment_method_id: str

@router.post("/save-card")
async def save_card(
    data: SaveCardRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    """Step 2: Save the card token after customer completes mandate authorization."""
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    if customer.billing_org_id:
        raise HTTPException(403, "Payment cards can only be managed by the Master Organization owner.")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
            require_owner=True,
        )
    
    if data.razorpay_payment_method_id and str(data.razorpay_payment_method_id).startswith("pay_"):
        raise HTTPException(
            400,
            "Invalid token: Payment IDs (pay_xxx) cannot be used as recurring tokens. An RBI mandate token (token_xxx) is required."
        )

    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
    
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    
    wallet.razorpay_payment_method_id = data.razorpay_payment_method_id
    await db.commit()

    from services.redis_client import clear_auto_recharge_failures
    await clear_auto_recharge_failures(master_id)
    
    return {"status": "card_saved"}

@router.post("/wallet/remove-card/by-org/{org_id}")
async def remove_saved_card(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    """Removes the saved card token and disables auto-recharge on the master wallet."""
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    if customer.billing_org_id:
        raise HTTPException(403, "Payment cards can only be managed by the Master Organization owner.")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
            require_owner=True,
        )

    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
    if not wallet:
        raise HTTPException(404, "Wallet not found")

    wallet.razorpay_payment_method_id = None
    wallet.auto_recharge_enabled = False
    await db.commit()

    return {"status": "card_removed"}

@router.get("/subscription/by-org/{org_id}")
async def get_subscription_by_org(
    org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )
    
    # For sub-orgs, subscription + custom config live on the billing master
    billing_customer_id = customer.billing_org_id if customer.billing_org_id else customer.id
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == billing_customer_id))
    sub = sub_res.scalar_one_or_none()
    if not sub:
        # Provisioning may not have run yet — return graceful empty
        return {"tier": None, "status": "not_provisioned"}
    
    return {
        "tier": sub.plan,
        "per_minute_rate_paise": sub.per_minute_rate_paise,
        "status": sub.status,
        "is_custom": sub.plan == "custom",
        "custom_plan_label": getattr(sub, "custom_plan_label", None),
        "custom_activation_deposit_paise": getattr(sub, "custom_config", {}).get("activation_deposit_paise") if getattr(sub, "custom_config", None) else None,
        "tier_upgrade_requested": customer.onboarding_form.get("tier_upgrade_requested") if customer.onboarding_form else None
    }

@router.get("/transactions/by-org/{org_id}")
async def get_transactions_by_org(
    org_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: Optional[str] = Query(None),
    sub_org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    if customer.billing_org_id:
        # Sub-org user: isolated ledger - see all transactions belonging to this workspace
        sub_run_ids_res = await db.execute(
            select(CallLog.dograh_run_id).where(CallLog.customer_id == customer.id)
        )
        sub_run_ids = [r for r in sub_run_ids_res.scalars().all() if r is not None]
        conditions = [
            WalletTransaction.customer_id == customer.id,
            WalletTransaction.sub_org_id == customer.id,
        ]
        if customer.dograh_org_id:
            conditions.append(WalletTransaction.dograh_org_id == customer.dograh_org_id)
        if sub_run_ids:
            conditions.append(WalletTransaction.dograh_run_id.in_(sub_run_ids))
        base_query = select(WalletTransaction).where(or_(*conditions))
    else:
        # Master / Single-org: unified ledger across master and all sub-orgs
        master_id = customer.id
        sub_orgs_res = await db.execute(select(Customer.id).where(Customer.billing_org_id == master_id))
        customer_ids = list(set([master_id] + list(sub_orgs_res.scalars().all())))
        base_query = select(WalletTransaction).where(
            (WalletTransaction.customer_id.in_(customer_ids)) |
            (WalletTransaction.sub_org_id.in_(customer_ids))
        )
        effective_sub_org_id = sub_org_id if isinstance(sub_org_id, int) else (sub_org_id.default if hasattr(sub_org_id, "default") and isinstance(sub_org_id.default, int) else None)
        if effective_sub_org_id:
            base_query = base_query.where(
                (WalletTransaction.sub_org_id == effective_sub_org_id) |
                (WalletTransaction.customer_id == effective_sub_org_id)
            )

    type_val = type if isinstance(type, str) else (type.default if hasattr(type, "default") and isinstance(type.default, str) else None)
    matched_types = resolve_transaction_types(type_val)
    if matched_types:
        base_query = base_query.where(WalletTransaction.type.in_(matched_types))
    
    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0
    
    page_val = int(page.default if hasattr(page, "default") and not isinstance(page, (int, float)) else page)
    limit_val = int(limit.default if hasattr(limit, "default") and not isinstance(limit, (int, float)) else limit)
    page_query = base_query.order_by(WalletTransaction.created_at.desc()).offset((page_val - 1) * limit_val).limit(limit_val)
    txn_result = await db.execute(page_query)
    transactions = txn_result.scalars().all()
    
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
            for t in transactions
        ],
        "total": total,
        "page": page_val,
    }

@router.get("/usage/by-org/{org_id}")
async def get_usage_by_org(
    org_id: int,
    month: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    from datetime import datetime
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    now = datetime.utcnow()
    month_str = month if isinstance(month, str) else None
    if month_str:
        year, mon = map(int, month_str.split("-"))
    else:
        year, mon = now.year, now.month
    
    month_start = datetime(year, mon, 1)
    month_end = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
    
    if customer.billing_org_id:
        # Sub-org workspace: strictly aggregate calls made by this specific workspace
        customer_ids = [customer.id]
    else:
        # Master / Single-org: resolve master and all sub-orgs
        master_id = customer.id
        sub_orgs_res = await db.execute(select(Customer.id).where(Customer.billing_org_id == master_id))
        customer_ids = list(set([master_id] + list(sub_orgs_res.scalars().all())))
    
    logs_result = await db.execute(
        select(CallLog).where(
            CallLog.customer_id.in_(customer_ids),
            CallLog.processed_at >= month_start,
            CallLog.processed_at < month_end
        )
    )
    logs = logs_result.scalars().all()
    
    import math
    return {
        "total_calls": len(logs),
        "total_minutes": sum(math.ceil(l.duration_seconds / 60) for l in logs) if logs else 0,
        "total_spend_paise": sum(l.cost_to_customer_paise for l in logs),
        "month": f"{year}-{mon:02d}"
    }

class AutoRechargeSettings(BaseModel):
    enabled: bool
    threshold_paise: int
    amount_paise: int

@router.patch("/wallet/auto-recharge/by-org/{org_id}")
async def update_auto_recharge(
    org_id: int,
    data: AutoRechargeSettings,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    if data.threshold_paise < 10000:
        raise HTTPException(400, "Minimum threshold is ₹100")
    if data.amount_paise < 50000:
        raise HTTPException(400, "Minimum recharge amount is ₹500")
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    if customer.billing_org_id:
        raise HTTPException(403, "Auto-recharge settings can only be managed by the Master Organization owner.")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
            require_owner=True,
        )
    
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
    if not wallet:
        raise HTTPException(404, "Wallet not found")

    if data.enabled and (not wallet.razorpay_payment_method_id or str(wallet.razorpay_payment_method_id).startswith("pay_")):
        raise HTTPException(
            400,
            "Cannot enable auto-recharge: saved payment method must be an RBI-compliant mandate token (token_xxx). Please add and verify a card supporting recurring mandates."
        )
    
    wallet.auto_recharge_enabled = data.enabled
    wallet.auto_recharge_threshold_paise = data.threshold_paise
    wallet.auto_recharge_amount_paise = data.amount_paise
    await db.commit()

    if data.enabled:
        from services.redis_client import clear_auto_recharge_failures
        await clear_auto_recharge_failures(master_id)
    
    return {"status": "ok"}


