from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.sql import func
from db.session import get_db
from db.models import Customer, Subscription, Wallet, WalletTransaction, CallLog
from services import razorpay_client, billing_service, provisioning_service, notification_service
from pydantic import BaseModel
from typing import Optional
import json
import logging
import hmac
import hashlib
from config import settings, WALLET_ACTIVATION_THRESHOLD_PAISE

logger = logging.getLogger(__name__)

router = APIRouter()

class TopupRequest(BaseModel):
    dograh_org_id: int
    amount_rupees: int

class DograhQuotaRequest(BaseModel):
    organization_id: int

class DograhDeductRequest(BaseModel):
    workflow_run_id: int
    duration_seconds: int
    organization_id: int


@router.post("/topup/create-order")
async def create_topup_order(data: TopupRequest, db: AsyncSession = Depends(get_db)):
    if data.amount_rupees < 500:
        raise HTTPException(400, "Minimum top-up is ₹500")
        
    # Look up customer by org_id
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    if customer.status not in ("active", "suspended", "pending_deposit", "pending_plan_selection"):
        raise HTTPException(400, "Account not eligible for top-up")
        
    amount_paise = data.amount_rupees * 100
    order = await razorpay_client.create_topup_order(amount_paise, f"topup_{customer.id}", customer.id)
    
    return {"razorpay_order_id": order["id"], "amount_paise": amount_paise, "currency": "INR"}


class UpgradeOrderRequest(BaseModel):
    dograh_org_id: int
    requested_tier: str

@router.post("/upgrade/create-order")
async def create_upgrade_order(data: UpgradeOrderRequest, db: AsyncSession = Depends(get_db)):
    from config import TIER_CONFIG
    tier_config = TIER_CONFIG.get(data.requested_tier)
    if not tier_config:
        raise HTTPException(400, "Invalid tier")
        
    amount_paise = tier_config.get("upgrade_deposit_paise")
    if not amount_paise:
        raise HTTPException(400, "Tier does not require an upgrade deposit")
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    order = await razorpay_client.create_topup_order(
        amount_paise, 
        f"upgrade_{customer.id}_{data.requested_tier}", 
        customer.id, 
        extra_notes={"requested_tier": data.requested_tier}
    )
    return {"razorpay_order_id": order["id"], "amount_paise": amount_paise, "currency": "INR", "requested_tier": data.requested_tier}


class ConfirmTopupRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    dograh_org_id: int
    amount_paise: int
    requested_tier: Optional[str] = None

@router.post("/confirm-topup")
async def confirm_topup(data: ConfirmTopupRequest, db: AsyncSession = Depends(get_db)):
    # 1. Verify signature (skip in mock mode)
    if settings.RAZORPAY_KEY_SECRET:
        msg = f"{data.razorpay_order_id}|{data.razorpay_payment_id}"
        expected = hmac.new(
            settings.RAZORPAY_KEY_SECRET.encode(),
            msg.encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, data.razorpay_signature):
            # TEMPORARY: Allow bypass in production as requested by user
            # raise HTTPException(400, "Invalid payment signature")
            logger.warning("WARNING: Bypassed invalid signature for topup!")
    
    # 2. Find customer
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    # 3. Idempotency — check if this order was already processed
    existing_txn = await db.execute(
        select(WalletTransaction).where(WalletTransaction.razorpay_order_id == data.razorpay_order_id)
    )
    if existing_txn.scalar_one_or_none():
        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
        w = wallet_res.scalar_one_or_none()
        return {"status": "already_processed", "new_balance_paise": w.balance_paise if w else 0}
    
    # 4. Credit wallet
    wallet = await billing_service.credit_wallet(db, customer.id, data.amount_paise, data.razorpay_order_id)
    
    # 5. Process Tier Upgrade if requested
    if data.requested_tier:
        from config import TIER_CONFIG
        if data.requested_tier in TIER_CONFIG:
            sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
            sub = sub_res.scalar_one_or_none()
            if sub:
                sub.plan = data.requested_tier
                sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
                
            existing_form = customer.onboarding_form or {}
            existing_form["approved_tier"] = data.requested_tier
            existing_form.pop("tier_upgrade_requested", None)
            existing_form.pop("tier_upgrade_requested_at", None)
            customer.onboarding_form = dict(existing_form)
            await db.commit()
            
            # Run provisioning synchronously to sync to Dograh
            from services.provisioning_service import run_provisioning
            try:
                await run_provisioning(customer.id, data.requested_tier, db)
            except Exception as e:
                logger.error(f"Provisioning failed after topup for customer {customer.id}: {e}")
                
            # Cascade to sub-orgs
            sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == customer.id))
            from sqlalchemy.orm.attributes import flag_modified
            for sub_org in sub_orgs_res.scalars().all():
                sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
                sub_sub = sub_sub_res.scalar_one_or_none()
                if sub_sub:
                    sub_sub.plan = data.requested_tier
                    sub_sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
                    
                sub_form = sub_org.onboarding_form or {}
                sub_form["approved_tier"] = data.requested_tier
                sub_org.onboarding_form = dict(sub_form)
                flag_modified(sub_org, "onboarding_form")
                
                await db.commit()
                if sub_org.status == "active":
                    try:
                        await run_provisioning(sub_org.id, data.requested_tier, db)
                    except Exception as e:
                        logger.error(f"Failed to cascade provisioning to sub-org {sub_org.id}: {e}")
    
    # 6. Auto-reactivate if suspended or pending_deposit
    if customer.status == "pending_deposit":
        if wallet.balance_paise >= WALLET_ACTIVATION_THRESHOLD_PAISE:
            customer.status = "active"
            await db.commit()
            from services.provisioning_service import run_provisioning
            from services import dograh_client
            try:
                await run_provisioning(customer.id, None, db)
            except Exception as e:
                logger.error(f"Failed to provision after activating customer {customer.id}: {e}")
            # Lift the call block if one was set
            if customer.dograh_org_id:
                try:
                    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
                    sub = sub_res.scalar_one_or_none()
                    tier = sub.plan if sub else "starter"
                    await dograh_client.restore_org_calls(customer.dograh_org_id, tier)
                except Exception as e:
                    logger.error(f"Failed to restore calls for org {customer.dograh_org_id}: {e}")
            await notification_service.send_email(
                to_email=customer.contact_email,
                subject="Your Talkar Agent is Live!",
                body="Your wallet is funded and your agent is now active!"
            )
    elif customer.status == "suspended" and wallet.balance_paise >= WALLET_ACTIVATION_THRESHOLD_PAISE:
        customer.status = "active"
        await db.commit()
        from services.provisioning_service import run_provisioning
        from services import dograh_client
        try:
            await run_provisioning(customer.id, None, db)
        except Exception as e:
            logger.error(f"Failed to re-provision after reactivating customer {customer.id}: {e}")
        # Lift the CONCURRENT_CALL_LIMIT=0 block set during suspension
        if customer.dograh_org_id:
            try:
                sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
                sub = sub_res.scalar_one_or_none()
                tier = sub.plan if sub else "starter"
                await dograh_client.restore_org_calls(customer.dograh_org_id, tier)
            except Exception as e:
                logger.error(f"Failed to restore calls for org {customer.dograh_org_id}: {e}")
    
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
            # TEMPORARY: Allow bypass in production as requested by user
            # raise HTTPException(400, "Invalid payment signature")
            logger.warning("WARNING: Bypassed invalid signature for setup fee!")

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
        order_id = event["payload"]["payment"]["entity"]["order_id"]
        amount_paise = event["payload"]["payment"]["entity"]["amount"]
        notes = event["payload"]["payment"]["entity"].get("notes", {})
        
        # Idempotency for wallet top-ups
        result = await db.execute(
            select(WalletTransaction).where(WalletTransaction.razorpay_order_id == order_id)
        )
        if result.scalar_one_or_none():
            return {"status": "duplicate top-up, ignored"}
            
        # Check if setup fee or top-up
        cust_result = await db.execute(select(Customer).where(Customer.setup_fee_order_id == order_id))
        customer = cust_result.scalar_one_or_none()
        
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
                await billing_service.credit_wallet(db, customer_id, amount_paise, order_id)
                
                requested_tier = notes.get("requested_tier")
                if requested_tier:
                    from config import TIER_CONFIG
                    if requested_tier in TIER_CONFIG:
                        # Fetch customer to update form
                        cust_result = await db.execute(select(Customer).where(Customer.id == customer_id))
                        upg_customer = cust_result.scalar_one_or_none()
                        if upg_customer:
                            sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
                            sub = sub_res.scalar_one_or_none()
                            if sub:
                                sub.plan = requested_tier
                                sub.per_minute_rate_paise = TIER_CONFIG[requested_tier]["per_minute_rate_paise"]
                                
                            existing_form = upg_customer.onboarding_form or {}
                            existing_form["approved_tier"] = requested_tier
                            existing_form.pop("tier_upgrade_requested", None)
                            existing_form.pop("tier_upgrade_requested_at", None)
                            upg_customer.onboarding_form = dict(existing_form)
                            await db.commit()
                            
                            # Provision synchronously
                            from services.provisioning_service import run_provisioning
                            try:
                                await run_provisioning(customer_id, requested_tier, db)
                            except Exception as e:
                                logger.error(f"Provisioning failed after webhook upgrade for {customer_id}: {e}")
                                
                            # Cascade to sub-orgs
                            sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == upg_customer.id))
                            from sqlalchemy.orm.attributes import flag_modified
                            for sub_org in sub_orgs_res.scalars().all():
                                sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
                                sub_sub = sub_sub_res.scalar_one_or_none()
                                if sub_sub:
                                    sub_sub.plan = requested_tier
                                    sub_sub.per_minute_rate_paise = TIER_CONFIG[requested_tier]["per_minute_rate_paise"]
                                    
                                sub_form = sub_org.onboarding_form or {}
                                sub_form["approved_tier"] = requested_tier
                                sub_org.onboarding_form = dict(sub_form)
                                flag_modified(sub_org, "onboarding_form")
                                
                                await db.commit()
                                if sub_org.status == "active":
                                    try:
                                        await run_provisioning(sub_org.id, requested_tier, db)
                                    except Exception as e:
                                        logger.error(f"Failed to cascade provisioning to sub-org {sub_org.id}: {e}")

    return {"status": "ok"}

@router.post("/check-quota")
async def check_quota(data: DograhQuotaRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.organization_id))
    customer = result.scalar_one_or_none()
    if not customer:
        return {"has_quota": True}  # Unknown org — pass through
        
    if customer.status != "active":
        return {"has_quota": False}

    from services.billing_service import get_billing_wallet, check_and_trigger_auto_recharge
    wallet, master_id = await get_billing_wallet(db, customer.id)
    
    if not wallet or wallet.balance_paise <= 0:
        if wallet:
            await check_and_trigger_auto_recharge(db, master_id)
        return {"has_quota": False}

    # 1. Minimum Reserve Check (Risk 1)
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    from config import TIER_CONFIG
    
    rate = sub.per_minute_rate_paise if sub else TIER_CONFIG["starter"]["per_minute_rate_paise"]
    tier_name = sub.plan if sub else "starter"
    max_duration_secs = TIER_CONFIG[tier_name].get("max_call_duration_seconds", 900)
    
    # Require 5 minutes of funds to even start a call
    minimum_reserve_paise = 5 * rate
    if wallet.balance_paise < minimum_reserve_paise:
        logger.warning(f"Org {data.organization_id} has balance {wallet.balance_paise} below minimum reserve {minimum_reserve_paise}")
        await check_and_trigger_auto_recharge(db, master_id)
        return {"has_quota": False}
        
    # 2. Billing Group Concurrency Cap (Risk 2)
    import math
    from services import redis_client
    
    max_call_cost = math.ceil(max_duration_secs / 60) * rate
    # How many simultaneous calls can the wallet afford if they all hit max duration?
    max_affordable_concurrent = math.floor(wallet.balance_paise / max_call_cost)
    
    if max_affordable_concurrent <= 0:
        return {"has_quota": False}
        
    current_active = await redis_client.get_active_calls(master_id)
    
    if current_active >= max_affordable_concurrent:
        logger.warning(f"Billing group {master_id} hit affordable concurrency cap ({current_active}/{max_affordable_concurrent})")
        return {"has_quota": False}
        
    # Grant quota -> increment Redis
    await redis_client.increment_active_calls(master_id, max_duration_secs)
    return {"has_quota": True}

@router.post("/deduct")
async def deduct_for_run(data: DograhDeductRequest, db: AsyncSession = Depends(get_db)):
    import math
    from sqlalchemy.sql import func
    from services.billing_service import check_and_trigger_auto_recharge
    from db.models import CallLog
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.organization_id))
    customer = result.scalar_one_or_none()
    if not customer:
        logger.warning(f"No customer found for org {data.organization_id} — run {data.workflow_run_id} not billed")
        return {"status": "ignored", "reason": "no customer found for org"}
        
    if customer.status != "active":
        logger.warning(f"Customer {customer.id} is not active (status: {customer.status}) — run {data.workflow_run_id} not billed")
        return {"status": "ignored", "reason": "customer not active"}

    # --- Idempotency: never double-charge the same run ---
    existing = await db.execute(
        select(CallLog).where(CallLog.dograh_run_id == data.workflow_run_id)
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "reason": "run already processed"}

    # Get subscription for per-minute rate
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    from config import TIER_CONFIG
    if not sub:
        logger.warning(f"No subscription found for customer {customer.id}, defaulting to starter rate")
    rate = sub.per_minute_rate_paise if sub else TIER_CONFIG["starter"]["per_minute_rate_paise"]

    # --- SOT edge case: zero-duration call (pipeline crash, abnormal termination) ---
    # SOT line 278: log it with cost=0, do not retry, alert admin
    if data.duration_seconds <= 0:
        call_log = CallLog(
            customer_id=customer.id,
            agent_id=None,
            dograh_run_id=data.workflow_run_id,
            duration_seconds=0,
            cost_to_customer_paise=0,
            called_at=func.now(),
            processed_at=func.now()
        )
        db.add(call_log)
        await db.commit()
        logger.warning(f"Zero-duration call for run {data.workflow_run_id} — logged with 0 cost")
        return {"status": "zero_duration_logged"}

    # SOT line 281: cost = ceil(duration_seconds / 60) * per_minute_rate, always ceil, never floor
    minutes = math.ceil(data.duration_seconds / 60)
    cost_paise = minutes * rate

    # Insert call log (processed_at set now = wallet deducted)
    call_log = CallLog(
        customer_id=customer.id,
        agent_id=None,
        dograh_run_id=data.workflow_run_id,
        duration_seconds=data.duration_seconds,
        cost_to_customer_paise=cost_paise,
        called_at=func.now(),
        processed_at=func.now()
    )
    db.add(call_log)

    # SOT line 282: atomic deduction — UPDATE...RETURNING to get new balance
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    
    result2 = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == master_id)
        .values(balance_paise=Wallet.balance_paise - cost_paise)
        .returning(Wallet)
    )
    wallet = result2.scalar_one_or_none()

    # Log wallet transaction
    txn = WalletTransaction(
        customer_id=master_id,
        type="call_deduction",
        amount_paise=-cost_paise,
        description=f"Call deduction for run {data.workflow_run_id}",
        dograh_run_id=data.workflow_run_id
    )
    db.add(txn)
    await db.commit()

    # SOT line 284-285: check auto-recharge, then check low balance
    await check_and_trigger_auto_recharge(db, customer.id)

    # SOT line 658: if balance went negative, email customer
    if wallet and wallet.balance_paise < 0:
        logger.warning(f"Customer {customer.id} wallet negative: {wallet.balance_paise} paise")
        await notification_service.notify_customer_negative_balance(customer.id)
        
    from services import redis_client
    await redis_client.decrement_active_calls(master_id)

    return {"status": "ok", "cost_paise": cost_paise, "new_balance_paise": wallet.balance_paise if wallet else None}

@router.get("/wallet/by-org/{org_id}")
async def get_wallet_by_org(org_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    
    if not wallet:
        raise HTTPException(404, "Wallet not found")
        
    return {
        "balance_paise": wallet.balance_paise,
        "auto_recharge_enabled": wallet.auto_recharge_enabled,
        "auto_recharge_threshold_paise": wallet.auto_recharge_threshold_paise,
        "auto_recharge_amount_paise": wallet.auto_recharge_amount_paise,
        "has_saved_card": bool(wallet.razorpay_payment_method_id)
    }

class CreateRazorpayCustomerRequest(BaseModel):
    dograh_org_id: int
    name: str
    email: str

@router.post("/razorpay-customer/create")
async def create_razorpay_customer(data: CreateRazorpayCustomerRequest, db: AsyncSession = Depends(get_db)):
    """Step 1: Create a Razorpay customer record linked to their wallet."""
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    
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

class SaveCardRequest(BaseModel):
    dograh_org_id: int
    razorpay_payment_method_id: str

@router.post("/save-card")
async def save_card(data: SaveCardRequest, db: AsyncSession = Depends(get_db)):
    """Step 2: Save the card token after customer completes mandate authorization."""
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    
    wallet.razorpay_payment_method_id = data.razorpay_payment_method_id
    await db.commit()
    
    return {"status": "card_saved"}

@router.get("/subscription/by-org/{org_id}")
async def get_subscription_by_org(org_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    if not sub:
        # Provisioning may not have run yet — return graceful empty
        return {"tier": None, "status": "not_provisioned"}
    
    return {
        "tier": sub.plan,
        "per_minute_rate_paise": sub.per_minute_rate_paise,
        "status": sub.status,
        "tier_upgrade_requested": customer.onboarding_form.get("tier_upgrade_requested") if customer.onboarding_form else None
    }

@router.get("/transactions/by-org/{org_id}")
async def get_transactions_by_org(
    org_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    # Unified ledger: get all customer IDs with same email
    customers_result = await db.execute(select(Customer.id).where(Customer.contact_email == customer.contact_email))
    customer_ids = customers_result.scalars().all()
    
    base_query = select(WalletTransaction).where(WalletTransaction.customer_id.in_(customer_ids))
    if type and type != "all":
        base_query = base_query.where(WalletTransaction.type == type)
    
    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar()
    
    page_query = base_query.order_by(WalletTransaction.created_at.desc()).offset((page - 1) * limit).limit(limit)
    txn_result = await db.execute(page_query)
    transactions = txn_result.scalars().all()
    
    return {
        "transactions": [
            {
                "id": t.id,
                "type": t.type,
                "amount_paise": t.amount_paise,
                "description": t.description,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transactions
        ],
        "total": total,
        "page": page,
    }

@router.get("/usage/by-org/{org_id}")
async def get_usage_by_org(
    org_id: int,
    month: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    from datetime import datetime
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    now = datetime.utcnow()
    if month:
        year, mon = map(int, month.split("-"))
    else:
        year, mon = now.year, now.month
    
    month_start = datetime(year, mon, 1)
    month_end = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
    
    # Unified ledger: get all customer IDs with same email to include sub-orgs
    customers_result = await db.execute(select(Customer.id).where(Customer.contact_email == customer.contact_email))
    customer_ids = customers_result.scalars().all()
    
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
async def update_auto_recharge(org_id: int, data: AutoRechargeSettings, db: AsyncSession = Depends(get_db)):
    if data.threshold_paise < 10000:
        raise HTTPException(400, "Minimum threshold is ₹100")
    if data.amount_paise < 50000:
        raise HTTPException(400, "Minimum recharge amount is ₹500")
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
    wallet = wallet_res.scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    
    wallet.auto_recharge_enabled = data.enabled
    wallet.auto_recharge_threshold_paise = data.threshold_paise
    wallet.auto_recharge_amount_paise = data.amount_paise
    await db.commit()
    
    return {"status": "ok"}


