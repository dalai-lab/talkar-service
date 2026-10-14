import logging
import time
from typing import Optional
from sqlalchemy import select, update, text, or_, case
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Wallet, WalletTransaction, Customer, TransactionType
from services import razorpay_client
from services import notification_service
from config import CALL_BLOCK_THRESHOLD_PAISE

logger = logging.getLogger(__name__)

async def get_billing_wallet(db: AsyncSession, customer_id: int, allow_suspended: bool = False):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: return None, None
    master_customer_id = customer.billing_org_id if customer.billing_org_id else customer.id
    
    if customer.billing_org_id:
        master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
        master = master_res.scalar_one_or_none()
        if master and master.status == "suspended" and not allow_suspended:
            raise ValueError(f"Master organization {master.id} is suspended. Cannot perform billing operations.")
            
    result = await db.execute(select(Wallet).where(Wallet.customer_id == master_customer_id))
    wallet = result.scalar_one_or_none()
    
    if not wallet:
        # Auto-create wallet if it doesn't exist (e.g. pending_deposit customers)
        wallet = Wallet(customer_id=master_customer_id, balance_paise=0)
        db.add(wallet)
        await db.commit()
        await db.refresh(wallet)
        
    return wallet, master_customer_id

async def credit_wallet(
    db: AsyncSession,
    customer_id: int,
    amount_paise: int,
    razorpay_order_id: str = None,
    description: str = "Wallet top-up via Razorpay",
    tx_type: str = TransactionType.TOP_UP.value,
    razorpay_payment_id: str = None,
    sub_org_id: Optional[int] = None,
    dograh_org_id: Optional[int] = None,
) -> Wallet:
    """Safely adds balance to a customer's wallet and records the ledger entry."""
    wallet, master_id = await get_billing_wallet(db, customer_id, allow_suspended=True)
    if not wallet:
        raise ValueError(f"Wallet not found for customer {customer_id}")
    if amount_paise <= 0:
        raise ValueError("Amount to credit must be greater than zero")

    # 0. Fast-path idempotency check: if razorpay_order_id or razorpay_payment_id is already recorded, avoid double-crediting
    dup_checks = []
    if razorpay_order_id:
        dup_checks.append(WalletTransaction.razorpay_order_id == razorpay_order_id)
    if razorpay_payment_id:
        dup_checks.append(WalletTransaction.razorpay_order_id == razorpay_payment_id)

    if dup_checks:
        existing_txn = await db.execute(
            select(WalletTransaction).where(or_(*dup_checks))
        )
        if existing_txn.scalar_one_or_none():
            logger.info(f"Wallet transaction for order/payment {razorpay_order_id or razorpay_payment_id} already exists; skipping duplicate credit.")
            return wallet

    # 1. Atomic update of wallet balance
    result = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == master_id)
        .values(
            balance_paise=Wallet.balance_paise + amount_paise,
            zero_balance_since=case(
                (Wallet.balance_paise + amount_paise <= 0, func.coalesce(Wallet.zero_balance_since, func.now())),
                else_=None
            )
        )
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
    # 2. Record transaction on the master wallet owner with sub-org attribution
    effective_sub_org_id = sub_org_id if sub_org_id is not None else (customer_id if customer_id != master_id else None)
    effective_dograh_org_id = dograh_org_id
    if effective_dograh_org_id is None and effective_sub_org_id is not None:
        try:
            c_res = await db.execute(select(Customer.dograh_org_id).where(Customer.id == effective_sub_org_id))
            effective_dograh_org_id = c_res.scalar_one_or_none()
        except Exception:
            pass

    primary_ref = razorpay_order_id or razorpay_payment_id
    transaction = WalletTransaction(
        customer_id=master_id,
        sub_org_id=effective_sub_org_id,
        dograh_org_id=effective_dograh_org_id,
        type=tx_type,
        amount_paise=amount_paise,
        description=description,
        razorpay_order_id=primary_ref
    )
    db.add(transaction)
    
    if tx_type == "top_up":
        from db.models import Invoice
        import datetime
        import uuid
        await db.flush() # flush to get transaction.id
        invoice_number = f"INV-{datetime.datetime.now().year}-{uuid.uuid4().hex[:6].upper()}"
        invoice_customer_id = effective_sub_org_id if effective_sub_org_id is not None else master_id
        invoice = Invoice(
            customer_id=invoice_customer_id,
            sub_org_id=effective_sub_org_id,
            dograh_org_id=effective_dograh_org_id,
            wallet_transaction_id=transaction.id,
            invoice_number=invoice_number,
            amount_paise=amount_paise,
            status="paid"
        )
        db.add(invoice)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        # Handle unique constraint violation on razorpay_order_id (race condition between confirm-topup & webhook)
        if (razorpay_order_id or razorpay_payment_id) and (
            "idx_wallet_transactions_razorpay" in str(e).lower()
            or "unique constraint" in str(e).lower()
            or "duplicate key" in str(e).lower()
        ):
            logger.warning(
                f"Concurrent duplicate credit attempted for razorpay reference {razorpay_order_id or razorpay_payment_id}; "
                "rolled back to prevent double-crediting."
            )
            wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
            refreshed_wallet = wallet_res.scalar_one_or_none()
            if refreshed_wallet:
                return refreshed_wallet
            return wallet
        raise
    
    return wallet

_in_flight_recharges = set()

async def _handle_auto_recharge_failure(db: AsyncSession, wallet: Wallet, master_id: int, error_detail: str):
    """
    Handles auto-recharge failure by setting a cooldown, tracking consecutive failures,
    notifying customer & admin, and disabling auto-recharge after repeated failures.
    """
    from services import redis_client
    fail_count, cooldown_sec = await redis_client.record_auto_recharge_failure(master_id)
    logger.warning(
        f"Auto-recharge failure #{fail_count} for customer {master_id} ({error_detail}). "
        f"Cooldown applied for {cooldown_sec}s."
    )
    is_disabled = fail_count >= 3
    if is_disabled:
        logger.error(
            f"Disabling auto-recharge for customer {master_id} after {fail_count} consecutive payment failures."
        )
        wallet.auto_recharge_enabled = False
        try:
            await db.commit()
        except Exception as commit_err:
            logger.error(f"Failed to persist auto_recharge_enabled=False to DB: {commit_err}")

    try:
        await notification_service.notify_customer_auto_recharge_failed(master_id, error_detail=error_detail, disabled=is_disabled)
        await notification_service.notify_admin_auto_recharge_failed(master_id, error_detail=error_detail, disabled=is_disabled)
    except Exception as notify_err:
        logger.error(f"Failed to send auto-recharge failure notifications: {notify_err}")

async def check_and_trigger_auto_recharge(db: AsyncSession, customer_id: int):
    """Evaluates threshold and triggers Razorpay token charge if enabled with duplicate charge guard."""
    wallet, master_id = await get_billing_wallet(db, customer_id, allow_suspended=True)
    
    if not wallet or not wallet.auto_recharge_enabled:
        return

    # Ensure saved payment method exists
    if not wallet.razorpay_payment_method_id or not wallet.razorpay_customer_id:
        logger.warning(f"Wallet {wallet.id} for master {master_id} has auto-recharge enabled but no saved payment method.")
        return

    # Proactively check for unchargeable legacy payment IDs (pay_xxx) under RBI mandate rules
    if str(wallet.razorpay_payment_method_id).startswith("pay_"):
        logger.warning(
            f"Wallet {wallet.id} for master {master_id} has unchargeable payment ID '{wallet.razorpay_payment_method_id}' instead of an RBI recurring token. Disabling auto-recharge."
        )
        wallet.auto_recharge_enabled = False
        wallet.razorpay_payment_method_id = None
        try:
            await db.commit()
            await notification_service.notify_customer_auto_recharge_failed(
                master_id,
                error_detail="Invalid payment token: An RBI recurring mandate token (token_xxx) is required.",
                disabled=True
            )
        except Exception as e:
            logger.error(f"Failed to clear invalid payment method on wallet {wallet.id}: {e}")
        return

    # Check if the master customer is active before charging
    result = await db.execute(select(Customer).where(Customer.id == master_id))
    master = result.scalar_one_or_none()
    if not master or master.status != "active":
        return
    user_threshold = wallet.auto_recharge_threshold_paise if wallet.auto_recharge_threshold_paise is not None else 150000
    
    # Pre-emptive scaling: ensure threshold accounts for active tier concurrency reserve
    # (e.g. 2 calls @ ₹6/min for 15 min = ₹180; Pro 5 calls @ ₹18/min = ₹1,350)
    from config import TIER_CONFIG
    from db.models import Subscription
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    plan = getattr(sub, "plan", "starter") if sub else "starter"
    plan_cfg = TIER_CONFIG.get(plan, TIER_CONFIG["starter"])
    concurrency_limit = plan_cfg.get("concurrent_call_limit", 2)
    rate = getattr(sub, "per_minute_rate_paise", None) or plan_cfg.get("per_minute_rate_paise", 600)
    concurrency_reserve = concurrency_limit * 15 * rate

    effective_threshold = max(user_threshold, concurrency_reserve)
    if wallet.balance_paise < effective_threshold:
        from services import redis_client

        # Guard 1: Cooldown check to prevent stampede on repeated payment failures
        if await redis_client.is_auto_recharge_in_cooldown(master_id):
            logger.info(f"Auto-recharge for customer {master_id} is in cooldown after previous payment failure, skipping.")
            return

        # Guard 2: In-memory in-flight guard for current process
        if master_id in _in_flight_recharges:
            logger.info(f"Auto-recharge already in flight in this process for customer {master_id}, skipping.")
            return

        # Guard 3: Distributed lock guard (fails closed) to prevent race condition across multiple concurrent calls/workers
        locked = await redis_client.acquire_auto_recharge_lock(master_id, ttl_seconds=120)
        if not locked:
            logger.info(f"Auto-recharge lock currently held (or Redis unavailable) for customer {master_id}, skipping concurrent charge.")
            return

        _in_flight_recharges.add(master_id)
        logger.info(f"Wallet {wallet.id} below threshold ({wallet.balance_paise} < {wallet.auto_recharge_threshold_paise}). Triggering auto-recharge.")
        try:
            order_id = None
            try:
                receipt = f"rcpt_auto_{master_id}_{int(time.time())}"
                order = await razorpay_client.create_topup_order(
                    amount_paise=wallet.auto_recharge_amount_paise,
                    receipt=receipt,
                    customer_id=master_id,
                    extra_notes={"type": "auto_recharge", "customer_id": str(master_id)},
                )
                if order and order.get("id"):
                    order_id = order.get("id")
            except Exception as e:
                logger.warning(f"Failed to pre-create Razorpay order for auto-recharge: {e}")

            charge = await razorpay_client.charge_saved_card(
                customer_id=wallet.razorpay_customer_id,
                payment_method_id=wallet.razorpay_payment_method_id,
                amount_paise=wallet.auto_recharge_amount_paise,
                email=master.contact_email if master else None,
                contact=master.contact_phone if master else None,
                order_id=order_id,
                talkar_customer_id=master_id,
                notes={"type": "auto_recharge", "customer_id": str(master_id)},
            )
            if charge.get("status") in ("captured", "authorized"):
                order_ref = order_id or charge.get("order_id")
                payment_ref = charge.get("id")
                txn_ref = order_ref or payment_ref
                await credit_wallet(
                    db,
                    master_id,
                    wallet.auto_recharge_amount_paise,
                    razorpay_order_id=txn_ref,
                    description="Auto-recharge: Low balance top-up via saved card",
                    razorpay_payment_id=payment_ref,
                )
                logger.info(f"Auto-recharge successful for customer {master_id}")
                await sync_wallet_block_policy(db, master_id)
                # Successful charge clears any prior failure counter or cooldown
                await redis_client.clear_auto_recharge_failures(master_id)
            else:
                charge_status = charge.get("status", "unknown")
                err_msg = f"Auto-recharge charge uncaptured (status: {charge_status})"
                logger.error(f"Auto-recharge charge uncaptured for customer {master_id}: {charge}")
                await _handle_auto_recharge_failure(db, wallet, master_id, err_msg)
        except Exception as e:
            logger.error(f"Auto-recharge failed for customer {master_id}: {e}")
            await _handle_auto_recharge_failure(db, wallet, master_id, str(e))
        finally:
            _in_flight_recharges.discard(master_id)
            await redis_client.release_auto_recharge_lock(master_id, token=locked)


async def trigger_auto_recharge_background(customer_id: int):
    """
    Background worker task to trigger auto-recharge asynchronously without blocking
    real-time telephony signaling requests. Uses an independent AsyncSessionLocal session.
    """
    from db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            await check_and_trigger_auto_recharge(session, customer_id)
    except Exception as e:
        logger.error(f"Background auto-recharge failed for customer {customer_id}: {e}")


async def deduct_for_run(run_id: int, db: Optional[AsyncSession] = None):
    """
    Cron job entry point to deduct funds for a specific Dograh run.
    """
    if db is not None:
        return await _deduct_for_run_impl(db, run_id)
    from db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        return await _deduct_for_run_impl(session, run_id)

async def _deduct_for_run_impl(db: AsyncSession, run_id: int):
    from db.models import Customer, Subscription, CallLog
    from services import dograh_client
    import math

    # Fetch run details from Dograh
    run = await dograh_client.get_run(run_id)
    if not run:
        logger.error(f"Run {run_id} not found in Dograh")
        return

    # Text chat sessions are free web interactions, not billable telephony calls
    if run.get("mode") == "textchat":
        logger.info(f"Run {run_id} is textchat — skipping billing reconciliation")
        return
        
    org_id = run["organization_id"]
    duration = run.get("duration_seconds") or 0.0
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        logger.warning(f"No customer found for org {org_id} — run {run_id} not billed")
        return
        
    # For sub-orgs, subscription and custom plan configuration live on the master account
    billing_customer_id = customer.billing_org_id or customer.id
    sub_result = await db.execute(select(Subscription).where(Subscription.customer_id == billing_customer_id))
    subscription = sub_result.scalar_one_or_none()
    if not subscription and customer.billing_org_id:
        sub_own_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        subscription = sub_own_res.scalar_one_or_none()
    if not subscription:
        logger.warning(f"No subscription found for customer/master {billing_customer_id}, defaulting to starter rate")

    # Idempotency: if already logged with processed_at set, skip
    existing = await db.execute(
        select(CallLog).where(CallLog.dograh_run_id == run_id)
    )
    if existing.scalar_one_or_none():
        logger.info(f"Run {run_id} already processed, skipping reconciliation")
        return

    # Lookup agent from Talkar DB via dograh_workflow_id
    workflow_id = run.get("workflow_id")
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

    # Calculate cost: zero duration is logged as 0 cost, agent_building playground is free,
    # all connected runs (>0s) are billed in 1-minute increments to cover carrier/AI provider costs.
    mode = run.get("mode")
    from config import TIER_CONFIG, resolve_tier_config, get_minimum_rate_floor_paise
    tier_cfg = resolve_tier_config(subscription) if subscription else TIER_CONFIG["starter"]
    rate = (subscription.per_minute_rate_paise if subscription and subscription.per_minute_rate_paise is not None else None) or tier_cfg.get("per_minute_rate_paise", 600)
    active_plan = subscription.plan if subscription else "starter"
    active_tts = tier_cfg.get("tts_provider", "deepgram")

    if duration <= 0:
        logger.info(f"Run {run_id} zero duration — logging as ₹0 cost")
        cost_paise = 0
    elif customer.status == "agent_building" and mode in ("textchat", "webrtc", "smallwebrtc"):
        logger.info(f"Run {run_id} is in agent_building mode ({mode}) — logging as ₹0 cost")
        cost_paise = 0
    else:
        minutes = math.ceil(duration / 60.0)

        # If sub-org, check if sub-org customer record itself indicates a higher/different tier or voice (Issue 19)
        if customer.billing_org_id:
            form = customer.onboarding_form or {}
            sub_tier = form.get("approved_tier")
            if sub_tier and sub_tier in TIER_CONFIG:
                active_tts = TIER_CONFIG[sub_tier].get("tts_provider", active_tts)
                active_plan = sub_tier
                sub_tier_cfg = resolve_tier_config(sub_tier)
                rate = sub_tier_cfg.get("per_minute_rate_paise", rate)
        if agent and agent.per_minute_rate_paise is not None and agent.per_minute_rate_paise > 0:
            rate = agent.per_minute_rate_paise
            logger.info(f"Using effective agent rate {rate} paise/min for agent {agent.id} on cron run {run_id}")

        # Margin floor enforcement (Issue 9 Protection):
        tts_floor = get_minimum_rate_floor_paise(active_tts)
        if not (agent and agent.per_minute_rate_paise is not None and agent.per_minute_rate_paise > 0):
            if "elevenlabs" in active_tts or "cartesia" in active_tts:
                pro_rate = TIER_CONFIG.get("pro", {}).get("per_minute_rate_paise", 1800)
                if rate < pro_rate:
                    logger.warning(
                        f"Sub-org {customer.id} cron run {run_id} used premium voice '{active_tts}' while on lower tier rate {rate} paise/min. "
                        f"Up-rating to {pro_rate} paise/min to protect gross margins."
                    )
                    rate = pro_rate

        if rate < tts_floor:
            logger.warning(
                f"Rate {rate} paise/min is below floor {tts_floor} paise/min for {active_tts} on org {org_id}. Clamping to floor on cron run {run_id}."
            )
            rate = tts_floor
        cost_paise = minutes * rate
    
    # Insert call log — stamp plan+tts_provider at this moment so profitability
    # always reflects what was active during the call, even after later plan switches.
    call_log = CallLog(
        customer_id=customer.id,
        agent_id=agent.id if agent else None,
        dograh_run_id=run_id,
        duration_seconds=duration,
        cost_to_customer_paise=cost_paise,
        called_at=func.now(),
        processed_at=func.now(),
        plan=active_plan,
        tts_provider=active_tts,
    )
    db.add(call_log)
    try:
        wallet, master_id = await get_billing_wallet(db, customer.id, allow_suspended=True)
        
        if cost_paise > 0:
            # Deduct wallet
            result = await db.execute(
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
            wallet = result.scalar_one_or_none()
            
            sub_org_id = customer.id if customer.billing_org_id else None
            dograh_org_id = customer.dograh_org_id
            # Record transaction
            transaction = WalletTransaction(
                customer_id=master_id,
                sub_org_id=sub_org_id,
                dograh_org_id=dograh_org_id,
                type=TransactionType.CALL_DEDUCTION.value,
                amount_paise=-cost_paise,
                description=f"Call deduction for run {run_id}",
                dograh_run_id=run_id
            )
            db.add(transaction)
            
        await db.commit()
        
        # Trigger auto-recharge hook
        if cost_paise > 0:
            await check_and_trigger_auto_recharge(db, customer.id)

        # Re-fetch authoritative wallet balance after auto-recharge to prevent stale-balance false warnings
        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
        refreshed_wallet = wallet_res.scalar_one_or_none()
        if refreshed_wallet:
            await db.refresh(refreshed_wallet)
            wallet = refreshed_wallet

        if cost_paise > 0 and wallet and wallet.balance_paise <= CALL_BLOCK_THRESHOLD_PAISE:
            logger.warning(f"Customer {customer.id} wallet went negative/low after auto-recharge check: {wallet.balance_paise}")
            await notification_service.notify_customer_service_paused(customer.id)
            await sync_wallet_block_policy(db, customer.id)
    except ValueError as ve:
        logger.warning(f"Cron deduction for run {run_id} encountered ValueError: {ve}")
        try:
            await db.commit()
        except Exception:
            await db.rollback()
    except Exception as e:
        logger.error(f"Error in cron deduction for run {run_id}: {e}")
        raise
        # NOTE: No decrement_active_calls here — the cron reconciliation path processes
        # runs that were never granted quota via check_quota (no INCR was ever called).
        # Decrementing here would corrupt the live counter with ghost decrements.

async def get_customer_month_spend(db: AsyncSession, customer_id: int) -> int:
    """Calculate the total call spend in paise for a customer in the current calendar month."""
    from db.models import CallLog
    import datetime
    
    now = datetime.datetime.now(datetime.timezone.utc)
    month_start = datetime.datetime(now.year, now.month, 1, tzinfo=datetime.timezone.utc)
    month_start_naive = datetime.datetime(now.year, now.month, 1)
    
    date_col = func.coalesce(CallLog.processed_at, CallLog.called_at)
    res = await db.execute(
        select(func.coalesce(func.sum(CallLog.cost_to_customer_paise), 0)).where(
            CallLog.customer_id == customer_id,
            or_(
                date_col >= month_start,
                date_col >= month_start_naive,
                date_col.is_(None),
            )
        )
    )
    val = res.scalar()
    return int(val) if val is not None else 0

async def sync_wallet_block_policy(db: AsyncSession, customer_id: int):
    """
    Unified function to enforce call block rules across master and all sub-orgs.
    Checks the master's balance and status, and updates Dograh CONCURRENT_CALL_LIMIT accordingly.
    Prevents fleet starvation: individual sub-org spending caps and concurrency limits are enforced
    independently so that an aggressive sub-org cannot drain master concurrency or starve sister sub-orgs.
    """
    from services import dograh_client
    from config import CALL_BLOCK_THRESHOLD_PAISE, resolve_tier_config
    from db.models import Subscription, Customer
    
    wallet, master_id = await get_billing_wallet(db, customer_id, allow_suspended=True)
    if not wallet: return

    master_res = await db.execute(select(Customer).where(Customer.id == master_id))
    master = master_res.scalar_one_or_none()
    if not master: return

    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    
    fleet_should_block = False
    if master.status not in ("active", "agent_building"):
        fleet_should_block = True
    elif wallet.balance_paise <= CALL_BLOCK_THRESHOLD_PAISE:
        fleet_should_block = True

    tier = sub.plan if sub else "starter"
    concurrent_limit = resolve_tier_config(sub).get("concurrent_call_limit")

    # Master Dograh org ID acts as the concurrency group key for the entire fleet
    master_dograh_id = master.dograh_org_id

    # 1. Update Master Org
    if master.dograh_org_id:
        try:
            if fleet_should_block:
                await dograh_client.block_org_calls(master.dograh_org_id)
            else:
                await dograh_client.restore_org_calls(
                    master.dograh_org_id,
                    tier,
                    concurrent_limit,
                    group_id=master.dograh_org_id
                )
        except Exception as e:
            logger.error(f"Failed to sync Dograh call limit for master org {master.dograh_org_id}: {e}")

    # 2. Update Sub-Orgs independently to prevent fleet starvation
    sub_orgs = await db.execute(select(Customer).where(Customer.billing_org_id == master_id))
    for sub_org in sub_orgs.scalars().all():
        if not sub_org.dograh_org_id or sub_org.dograh_org_id == master.dograh_org_id:
            continue
        try:
            if fleet_should_block:
                await dograh_client.block_org_calls(sub_org.dograh_org_id)
            elif sub_org.status not in ("active", "agent_building"):
                await dograh_client.block_org_calls(sub_org.dograh_org_id)
            else:
                form = sub_org.onboarding_form or {}
                sub_cap = form.get("monthly_spending_cap_paise")
                sub_blocked = False
                if sub_cap is not None and int(sub_cap) > 0:
                    sub_spend = await get_customer_month_spend(db, sub_org.id)
                    if sub_spend >= int(sub_cap):
                        logger.warning(
                            f"Sub-org {sub_org.id} (org {sub_org.dograh_org_id}) exceeded monthly spending cap: "
                            f"{sub_spend} >= {sub_cap} paise. Blocking calls for this sub-org."
                        )
                        sub_blocked = True

                if sub_blocked:
                    await dograh_client.block_org_calls(sub_org.dograh_org_id)
                else:
                    sub_limit = concurrent_limit
                    is_custom_cap = False
                    if form.get("concurrent_call_limit"):
                        try:
                            custom_limit = int(form["concurrent_call_limit"])
                            if custom_limit > 0:
                                sub_limit = min(concurrent_limit, custom_limit)
                                is_custom_cap = True
                        except (ValueError, TypeError):
                            pass
                    await dograh_client.restore_org_calls(
                        sub_org.dograh_org_id,
                        tier,
                        sub_limit,
                        group_id=master_dograh_id or sub_org.dograh_org_id,
                        is_custom_cap=is_custom_cap
                    )
        except Exception as e:
            logger.error(f"Failed to sync Dograh call limit for sub-org {sub_org.dograh_org_id}: {e}")


async def charge_initial_phone_number_lease(
    db: AsyncSession,
    customer_id: int,
    phone_number
) -> Optional[WalletTransaction]:
    """
    Charges the first month's telephony DID lease when a phone number is assigned/approved,
    if the customer fleet has exceeded their plan's free_phone_numbers allowance (Issue 31).
    """
    from config import DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE, resolve_tier_config, CALL_BLOCK_THRESHOLD_PAISE
    from db.models import Customer, Subscription, Wallet, WalletTransaction, TransactionType, Invoice, PhoneNumber
    from sqlalchemy.sql import case, func
    from sqlalchemy import select, update, or_
    import datetime, uuid

    try:
        wallet, master_id = await get_billing_wallet(db, customer_id, allow_suspended=True)
    except Exception as e:
        logger.warning(f"Could not resolve billing wallet for customer {customer_id}: {e}")
        return None

    # Get master subscription to check free_phone_numbers
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    tier_cfg = resolve_tier_config(sub)
    free_allowance = tier_cfg.get("free_phone_numbers", 1)

    # Count existing active numbers across the entire fleet excluding this one
    fleet_cust_ids_res = await db.execute(
        select(Customer.id).where(or_(Customer.id == master_id, Customer.billing_org_id == master_id))
    )
    fleet_cust_ids = fleet_cust_ids_res.scalars().all() or [customer_id]

    active_pn_res = await db.execute(
        select(func.count(PhoneNumber.id)).where(
            PhoneNumber.customer_id.in_(fleet_cust_ids),
            PhoneNumber.status == "active",
            PhoneNumber.id != phone_number.id
        )
    )
    existing_active_count = active_pn_res.scalar_one() or 0

    now = datetime.datetime.now(datetime.timezone.utc)
    phone_number.last_billed_at = now

    if existing_active_count < free_allowance:
        # Covered by plan subscription allowance
        logger.info(
            f"Phone number {phone_number.number} for customer {customer_id} covered by tier allowance "
            f"({existing_active_count + 1}/{free_allowance} free numbers)"
        )
        await db.commit()
        return None

    # Extra billable number
    cost_paise = phone_number.monthly_cost_paise if (
        phone_number.monthly_cost_paise is not None and phone_number.monthly_cost_paise > 0
    ) else DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE

    phone_number.monthly_cost_paise = cost_paise

    # Deduct from master wallet
    result = await db.execute(
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
    wallet = result.scalar_one_or_none()

    c_res = await db.execute(select(Customer.dograh_org_id, Customer.company_name).where(Customer.id == customer_id))
    c_row = c_res.fetchone()
    dograh_org_id = c_row.dograh_org_id if c_row else None
    company_name = c_row.company_name if c_row else ""

    sub_org_id = customer_id if customer_id != master_id else None
    desc = f"Initial lease for phone number {phone_number.number}"
    if sub_org_id and company_name:
        desc += f" ({company_name})"

    txn = WalletTransaction(
        customer_id=master_id,
        sub_org_id=sub_org_id,
        dograh_org_id=dograh_org_id,
        type=TransactionType.PHONE_NUMBER_LEASE.value,
        amount_paise=-cost_paise,
        description=desc,
    )
    db.add(txn)
    await db.flush()

    invoice_number = f"INV-{now.year}-{uuid.uuid4().hex[:6].upper()}"
    invoice_customer_id = sub_org_id if sub_org_id is not None else master_id
    invoice = Invoice(
        customer_id=invoice_customer_id,
        sub_org_id=sub_org_id,
        dograh_org_id=dograh_org_id,
        wallet_transaction_id=txn.id,
        invoice_number=invoice_number,
        amount_paise=cost_paise,
        status="paid"
    )
    db.add(invoice)
    await db.commit()

    logger.info(
        f"Deducted initial lease {cost_paise} paise for phone {phone_number.number} "
        f"from master wallet {master_id}. New balance: {wallet.balance_paise if wallet else 'unknown'}"
    )

    await check_and_trigger_auto_recharge(db, master_id)
    if wallet and wallet.balance_paise <= CALL_BLOCK_THRESHOLD_PAISE:
        await sync_wallet_block_policy(db, master_id)

    from services.notification_service import notify_customer_phone_number_lease_billed
    try:
        await notify_customer_phone_number_lease_billed(
            customer_id=customer_id,
            number=phone_number.number,
            amount_paise=cost_paise,
            new_balance_paise=wallet.balance_paise if wallet else 0,
            is_renewal=False
        )
    except Exception as e:
        logger.warning(f"Failed to send lease notification for phone {phone_number.number}: {e}")

    return txn


async def bill_monthly_phone_number_leases(db: AsyncSession) -> dict:
    """
    Cron task: Bills monthly telephony DID lease fees for all active phone numbers
    that exceed the customer fleet's plan allowance and have reached their 30-day billing cycle (Issue 31).
    """
    from config import DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE, resolve_tier_config, CALL_BLOCK_THRESHOLD_PAISE
    from db.models import Customer, Subscription, Wallet, WalletTransaction, TransactionType, Invoice, PhoneNumber
    from sqlalchemy.sql import case, func
    from sqlalchemy import select, update, or_
    import datetime, uuid

    now = datetime.datetime.now(datetime.timezone.utc)
    logger.info("Evaluating monthly telephony DID lease billing for active phone numbers...")

    # Fetch all active phone numbers with customer metadata
    pns_res = await db.execute(
        select(PhoneNumber, Customer)
        .join(Customer, Customer.id == PhoneNumber.customer_id)
        .where(PhoneNumber.status == "active")
        .order_by(PhoneNumber.purchased_at.asc(), PhoneNumber.id.asc())
    )
    rows = pns_res.all()
    if not rows:
        logger.info("No active phone numbers found for lease billing.")
        return {"billed_count": 0, "total_paise": 0, "free_numbers_count": 0}

    # Group by master_id
    fleet_map: dict[int, list[tuple[PhoneNumber, Customer]]] = {}
    for pn, cust in rows:
        m_id = cust.billing_org_id if cust.billing_org_id else cust.id
        fleet_map.setdefault(m_id, []).append((pn, cust))

    billed_count = 0
    total_paise = 0
    free_count = 0

    for master_id, pn_tuples in fleet_map.items():
        # Get master subscription to check free_phone_numbers
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
        sub = sub_res.scalar_one_or_none()
        tier_cfg = resolve_tier_config(sub)
        free_allowance = tier_cfg.get("free_phone_numbers", 1)

        # Iterate in purchased_at order
        for idx, (pn, cust) in enumerate(pn_tuples):
            if idx < free_allowance:
                # Included in plan
                free_count += 1
                if pn.last_billed_at is None:
                    pn.last_billed_at = now
                    await db.commit()
                continue

            # Check if 30-day renewal cycle has elapsed
            last_billed = pn.last_billed_at or pn.purchased_at
            if last_billed is not None and last_billed.tzinfo is None:
                last_billed = last_billed.replace(tzinfo=datetime.timezone.utc)

            is_due = (last_billed is None) or ((now - last_billed).total_seconds() >= 30 * 86400)
            if not is_due:
                continue

            cost_paise = pn.monthly_cost_paise if (
                pn.monthly_cost_paise is not None and pn.monthly_cost_paise > 0
            ) else DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE

            pn.monthly_cost_paise = cost_paise

            # Deduct from master wallet
            result = await db.execute(
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
            wallet = result.scalar_one_or_none()

            sub_org_id = cust.id if cust.id != master_id else None
            desc = f"Monthly lease for phone number {pn.number}"
            if sub_org_id and cust.company_name:
                desc += f" ({cust.company_name})"

            txn = WalletTransaction(
                customer_id=master_id,
                sub_org_id=sub_org_id,
                dograh_org_id=cust.dograh_org_id,
                type=TransactionType.PHONE_NUMBER_LEASE.value,
                amount_paise=-cost_paise,
                description=desc,
            )
            db.add(txn)
            await db.flush()

            invoice_number = f"INV-{now.year}-{uuid.uuid4().hex[:6].upper()}"
            invoice_customer_id = sub_org_id if sub_org_id is not None else master_id
            invoice = Invoice(
                customer_id=invoice_customer_id,
                sub_org_id=sub_org_id,
                dograh_org_id=cust.dograh_org_id,
                wallet_transaction_id=txn.id,
                invoice_number=invoice_number,
                amount_paise=cost_paise,
                status="paid"
            )
            db.add(invoice)

            pn.last_billed_at = now
            await db.commit()

            billed_count += 1
            total_paise += cost_paise

            await check_and_trigger_auto_recharge(db, master_id)
            if wallet and wallet.balance_paise <= CALL_BLOCK_THRESHOLD_PAISE:
                await sync_wallet_block_policy(db, master_id)

            from services.notification_service import notify_customer_phone_number_lease_billed
            try:
                await notify_customer_phone_number_lease_billed(
                    customer_id=cust.id,
                    number=pn.number,
                    amount_paise=cost_paise,
                    new_balance_paise=wallet.balance_paise if wallet else 0,
                    is_renewal=True
                )
            except Exception as e:
                logger.warning(f"Failed to send lease notification for phone {pn.number}: {e}")

    logger.info(
        f"Telephony lease billing finished: {billed_count} numbers billed ({total_paise} paise total), "
        f"{free_count} covered by tier allowance."
    )
    return {
        "billed_count": billed_count,
        "total_paise": total_paise,
        "free_numbers_count": free_count
    }
