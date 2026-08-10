import logging
from sqlalchemy import select, update
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Wallet, WalletTransaction
from services import razorpay_client
from services import notification_service

logger = logging.getLogger(__name__)

async def credit_wallet(db: AsyncSession, customer_id: int, amount_paise: int, razorpay_order_id: str = None) -> Wallet:
    """Safely adds balance to a customer's wallet and records the ledger entry."""
    # 1. Atomic update of wallet balance
    result = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == customer_id)
        .values(balance_paise=Wallet.balance_paise + amount_paise)
        .returning(Wallet)
    )
    wallet = result.scalar_one_or_none()
    
    if not wallet:
        raise ValueError(f"Wallet not found for customer {customer_id}")

    # 2. Record transaction
    transaction = WalletTransaction(
        customer_id=customer_id,
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
    result = await db.execute(select(Wallet).where(Wallet.customer_id == customer_id))
    wallet = result.scalar_one_or_none()
    
    if not wallet or not wallet.auto_recharge_enabled:
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
                await credit_wallet(db, customer_id, wallet.auto_recharge_amount_paise)
                logger.info(f"Auto-recharge successful for customer {customer_id}")
        except Exception as e:
            logger.error(f"Auto-recharge failed for customer {customer_id}: {e}")
            await notification_service.notify_customer_auto_recharge_failed(customer_id)
            await notification_service.notify_admin_auto_recharge_failed(customer_id)

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
        duration = run.get("duration_seconds", 0)
        
        # Get customer and subscription
        result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
        customer = result.scalar_one_or_none()
        if not customer:
            logger.error(f"Customer not found for org {org_id}")
            return
            
        sub_result = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        subscription = sub_result.scalar_one_or_none()
        if not subscription:
            logger.error(f"Subscription not found for customer {customer.id}")
            return

        # Idempotency: if already logged with processed_at set, skip
        existing = await db.execute(
            select(CallLog).where(CallLog.dograh_run_id == run_id)
        )
        if existing.scalar_one_or_none():
            logger.info(f"Run {run_id} already processed, skipping reconciliation")
            return

        # Calculate cost
        minutes = math.ceil(duration / 60.0)
        cost_paise = minutes * subscription.per_minute_rate_paise
        
        # Insert call log
        call_log = CallLog(
            customer_id=customer.id,
            dograh_run_id=run_id,
            duration_seconds=duration,
            cost_to_customer_paise=cost_paise,
            called_at=func.now(),
            processed_at=func.now()
        )
        db.add(call_log)
        
        # Deduct wallet
        result = await db.execute(
            update(Wallet)
            .where(Wallet.customer_id == customer.id)
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
            
        await db.commit()
        
        # Trigger auto-recharge hook
        await check_and_trigger_auto_recharge(db, customer.id)
