import logging
from sqlalchemy import select, update
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Wallet, WalletTransaction, Customer
from services import razorpay_client
from services import notification_service

logger = logging.getLogger(__name__)

async def get_billing_wallet(db: AsyncSession, customer_id: int):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: return None, None
    master_customer_id = customer.billing_org_id if customer.billing_org_id else customer.id
    
    if customer.billing_org_id:
        master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
        master = master_res.scalar_one_or_none()
        if master and master.status == "suspended":
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

async def credit_wallet(db: AsyncSession, customer_id: int, amount_paise: int, razorpay_order_id: str = None) -> Wallet:
    """Safely adds balance to a customer's wallet and records the ledger entry."""
    wallet, master_id = await get_billing_wallet(db, customer_id)
    if not wallet:
        raise ValueError(f"Wallet not found for customer {customer_id}")
    if amount_paise <= 0:
        raise ValueError("Amount to credit must be greater than zero")

    # 1. Atomic update of wallet balance
    result = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == master_id)
        .values(balance_paise=Wallet.balance_paise + amount_paise)
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
    # 2. Record transaction on the master wallet owner
    transaction = WalletTransaction(
        customer_id=master_id,
        type="top_up",
        amount_paise=amount_paise,
        description="Wallet top-up via Razorpay",
        razorpay_order_id=razorpay_order_id
    )
    db.add(transaction)
    await db.commit()
    
    return wallet

async def check_and_trigger_auto_recharge(db: AsyncSession, customer_id: int):
    """Evaluates threshold and triggers Razorpay token charge if enabled."""
    wallet, master_id = await get_billing_wallet(db, customer_id)
    
    if not wallet or not wallet.auto_recharge_enabled:
        return

    # Check if the master customer is active before charging
    result = await db.execute(select(Customer).where(Customer.id == master_id))
    master = result.scalar_one_or_none()
    if not master or master.status != "active":
        return

    if wallet.balance_paise < wallet.auto_recharge_threshold_paise:
        logger.info(f"Wallet {wallet.id} below threshold. Triggering auto-recharge.")
        try:
            charge = await razorpay_client.charge_saved_card(
                customer_id=wallet.razorpay_customer_id,
                payment_method_id=wallet.razorpay_payment_method_id,
                amount_paise=wallet.auto_recharge_amount_paise,
            )
            if charge.get("status") == "captured":
                await credit_wallet(db, master_id, wallet.auto_recharge_amount_paise)
                logger.info(f"Auto-recharge successful for customer {master_id}")
        except Exception as e:
            logger.error(f"Auto-recharge failed for customer {master_id}: {e}")
            await notification_service.notify_customer_auto_recharge_failed(master_id)
            await notification_service.notify_admin_auto_recharge_failed(master_id)

async def deduct_for_run(run_id: int):
    """
    Cron job entry point to deduct funds for a specific Dograh run.
    """
    from db.session import AsyncSessionLocal
    from db.models import Customer, Subscription, CallLog
    from services import dograh_client
    import math

    async with AsyncSessionLocal() as db:
        # Fetch run details from Dograh
        run = await dograh_client.get_run(run_id)
        if not run:
            logger.error(f"Run {run_id} not found in Dograh")
            return
            
        org_id = run["organization_id"]
        duration = run.get("duration_seconds") or 0.0
        
        result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
        customer = result.scalar_one_or_none()
        if not customer:
            logger.warning(f"No customer found for org {org_id} — run {run_id} not billed")
            return
            
        if customer.status != "active":
            logger.warning(f"Customer {customer.id} is not active (status: {customer.status}) — run {run_id} not billed")
            return
            
        sub_result = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        subscription = sub_result.scalar_one_or_none()
        if not subscription:
            logger.warning(f"No subscription found for customer {customer.id}, defaulting to starter rate")

        # Idempotency: if already logged with processed_at set, skip
        existing = await db.execute(
            select(CallLog).where(CallLog.dograh_run_id == run_id)
        )
        if existing.scalar_one_or_none():
            logger.info(f"Run {run_id} already processed, skipping reconciliation")
            return

        # Calculate cost (with minimum billable guard)
        MIN_BILLABLE_SECONDS = 10
        if duration < MIN_BILLABLE_SECONDS:
            logger.info(f"Run {run_id} too short ({duration}s) — logging as ₹0 cost")
            cost_paise = 0
        else:
            minutes = math.ceil(duration / 60.0)
            from config import TIER_CONFIG
            rate = subscription.per_minute_rate_paise if subscription else TIER_CONFIG["starter"]["per_minute_rate_paise"]
            cost_paise = minutes * rate
        
        # Insert call log — stamp plan+tts_provider at this moment so profitability
        # always reflects what was active during the call, even after later plan switches.
        from config import TIER_CONFIG
        active_plan = subscription.plan if subscription else "starter"
        active_tts = TIER_CONFIG.get(active_plan, {}).get("tts_provider", "deepgram")
        call_log = CallLog(
            customer_id=customer.id,
            agent_id=None,
            dograh_run_id=run_id,
            duration_seconds=duration,
            cost_to_customer_paise=cost_paise,
            called_at=func.now(),
            processed_at=func.now(),
            plan=active_plan,
            tts_provider=active_tts,
        )
        db.add(call_log)
        
        wallet, master_id = await get_billing_wallet(db, customer.id)
        
        if cost_paise > 0:
            # Deduct wallet
            result = await db.execute(
                update(Wallet)
                .where(Wallet.customer_id == master_id)
                .values(balance_paise=Wallet.balance_paise - cost_paise)
                .returning(Wallet)
            )
            wallet = result.scalar_one_or_none()
            
            # Record transaction
            transaction = WalletTransaction(
                customer_id=customer.id,
                type="call_deduction",  # Must match SOT schema: 'top_up' | 'call_deduction' | 'refund' | 'grant'
                amount_paise=-cost_paise,
                description=f"Call deduction for run {run_id}",
                dograh_run_id=run_id
            )
            db.add(transaction)
            
            if wallet and wallet.balance_paise < 0:
                logger.warning(f"Customer {customer.id} wallet went negative: {wallet.balance_paise}")
                await notification_service.notify_customer_negative_balance(customer.id)
            
        from services import redis_client
        await redis_client.decrement_active_calls(master_id)
            
        await db.commit()
        
        # Trigger auto-recharge hook
        if cost_paise > 0:
            await check_and_trigger_auto_recharge(db, customer.id)
