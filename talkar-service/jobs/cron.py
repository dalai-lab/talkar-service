import logging
from datetime import datetime, timedelta
from sqlalchemy import select, update, text
from sqlalchemy.sql import func
from db.session import get_db, AsyncSessionLocal
from db.models import Customer, Wallet, CallLog
from services import dograh_client, billing_service, notification_service

logger = logging.getLogger("arq.cron")
logger.setLevel(logging.INFO)

async def nightly_reconciliation(ctx):
    """
    8A - Find calls that completed but wallet was never deducted.
    Runs at 2:00 AM IST.
    """
    logger.info("Starting nightly reconciliation...")
    
    # Get all Dograh workflow_runs completed in last 25h (buffer for 24h)
    runs = await dograh_client.get_completed_runs(hours=25)
    
    async with AsyncSessionLocal() as db:
        for run in runs:
            # Check if this run was already processed
            result = await db.execute(
                select(CallLog).where(
                    CallLog.dograh_run_id == run["id"],
                    CallLog.processed_at.is_not(None)
                )
            )
            exists = result.scalar_one_or_none()
            
            if not exists:
                await billing_service.deduct_for_run(run["id"])
                logger.info(f"Reconciliation: deducted for run {run['id']}")
                
    logger.info("Nightly reconciliation completed.")

async def check_low_balances(ctx):
    """
    8B - Notify customers when wallet is getting low.
    Runs every hour.
    """
    logger.info("Checking for low balances...")
    async with AsyncSessionLocal() as db:
        query = text("""
            SELECT w.customer_id, w.balance_paise, w.low_balance_alerted_at, c.contact_email
            FROM wallets w JOIN customers c ON c.id = w.customer_id
            WHERE c.status = 'active'
              AND w.balance_paise < 50000
              AND w.balance_paise > 0
              AND (w.low_balance_alerted_at IS NULL
                   OR w.low_balance_alerted_at < now() - interval '24 hours')
        """)
        result = await db.execute(query)
        wallets = result.fetchall()
        
        for wallet in wallets:
            # Send alert
            await notification_service.send_email(
                to_email=wallet.contact_email,
                subject="Low Balance Alert",
                body=f"Your wallet balance is getting low: ₹{wallet.balance_paise / 100:.2f}. Please top up to keep your agents active."
            )
            logger.info(f"Sent low balance alert to customer {wallet.customer_id}")
            
            # Update alerted_at
            await db.execute(
                update(Wallet)
                .where(Wallet.customer_id == wallet.customer_id)
                .values(low_balance_alerted_at=func.now())
            )
        await db.commit()
    logger.info("Low balance check completed.")

async def check_suspensions(ctx):
    """
    8C - Suspend customers who have been at ₹0 for 14+ days.
    Runs daily at 8:00 AM IST.
    """
    logger.info("Checking for overdue zero-balance accounts...")
    async with AsyncSessionLocal() as db:
        # Suspend accounts dormant for 14+ days
        suspend_query = text("""
            UPDATE customers SET status = 'suspended'
            WHERE status = 'active'
              AND id IN (
                SELECT customer_id FROM wallets
                WHERE balance_paise = 0
                  AND updated_at < now() - interval '14 days'
              )
            RETURNING id, contact_email
        """)
        suspended_result = await db.execute(suspend_query)
        for row in suspended_result:
            logger.info(f"Suspended customer {row.id}")
            await notification_service.send_email(
                to_email=row.contact_email,
                subject="Account Suspended",
                body="Your account has been suspended due to 14 days of zero balance. Please top up to reactivate."
            )

        # Churn accounts suspended for 45+ days
        churn_query = text("""
            UPDATE customers SET status = 'churned'
            WHERE status = 'suspended'
              AND updated_at < now() - interval '45 days'
            RETURNING id, dograh_org_id, contact_email
        """)
        churned_result = await db.execute(churn_query)
        for row in churned_result:
            logger.info(f"Churned customer {row.id}. Archiving Dograh org {row.dograh_org_id}")
            if row.dograh_org_id:
                await dograh_client.archive_org(row.dograh_org_id)
            await notification_service.send_email(
                to_email=row.contact_email,
                subject="Account Closed",
                body="Your account has been permanently closed due to inactivity."
            )
            
        await db.commit()
    logger.info("Suspension check completed.")

async def cleanup_abandoned_signups(ctx):
    """
    8D - Delete customers who signed up but never submitted onboarding form.
    Runs daily.
    """
    logger.info("Cleaning up abandoned signups...")
    async with AsyncSessionLocal() as db:
        # Send Day 7 reminder
        reminder_query = text("""
            SELECT id, contact_email FROM customers
            WHERE status = 'pending_approval'
              AND created_at < now() - interval '7 days'
              AND created_at > now() - interval '8 days'
        """)
        reminders = await db.execute(reminder_query)
        for row in reminders:
            logger.info(f"Sending Day 7 reminder to {row.id}")
            await notification_service.send_email(
                to_email=row.contact_email,
                subject="Complete your Talkar Setup",
                body="It's been 7 days! Please complete your onboarding form to get your agent built."
            )

        # Day 30: Delete completely
        abandoned_query = text("""
            SELECT id, dograh_org_id, dograh_user_id FROM customers
            WHERE status = 'pending_approval'
              AND created_at < now() - interval '30 days'
        """)
        abandoned_result = await db.execute(abandoned_query)
        
        for row in abandoned_result:
            logger.info(f"Deleting abandoned customer {row.id}")
            if row.dograh_org_id:
                await dograh_client.delete_org(row.dograh_org_id)
            await db.execute(text("DELETE FROM customers WHERE id = :id"), {"id": row.id})
            
        await db.commit()
    logger.info("Abandoned signup cleanup completed.")

async def charge_monthly_subscriptions(ctx):
    """
    Runs daily at 8:00 AM IST.
    Deducts monthly_fee_paise from wallets where next_billing_date <= today.
    Idempotent: checks for existing monthly_fee transaction in this billing cycle.
    """
    from datetime import date
    from db.models import Subscription, WalletTransaction
    
    logger.info("Starting monthly subscription billing...")
    today = date.today()
    
    async with AsyncSessionLocal() as db:
        # Find all due subscriptions
        result = await db.execute(
            select(Subscription).where(
                Subscription.next_billing_date <= today,
                Subscription.status == "active"
            )
        )
        due_subs = result.scalars().all()
        
        for sub in due_subs:
            # Idempotency: check if we already charged this cycle
            # A "monthly_fee" transaction must NOT exist between (next_billing_date - 30d) and next_billing_date
            from datetime import timedelta
            cycle_start = sub.next_billing_date - timedelta(days=31)
            
            existing_charge = await db.execute(
                select(WalletTransaction).where(
                    WalletTransaction.customer_id == sub.customer_id,
                    WalletTransaction.type == "monthly_fee",
                    WalletTransaction.created_at >= datetime.combine(cycle_start, datetime.min.time()),
                )
            )
            if existing_charge.scalar_one_or_none():
                logger.info(f"Monthly fee already charged for customer {sub.customer_id} this cycle. Skipping.")
                # Just advance the billing date if needed
                sub.next_billing_date = sub.next_billing_date + timedelta(days=30)
                await db.commit()
                continue
            
            # Deduct the fee atomically
            result2 = await db.execute(
                update(Wallet)
                .where(Wallet.customer_id == sub.customer_id)
                .values(balance_paise=Wallet.balance_paise - sub.monthly_fee_paise)
                .returning(Wallet)
            )
            wallet = result2.scalar_one_or_none()
            
            if not wallet:
                logger.error(f"No wallet for customer {sub.customer_id}. Skipping billing.")
                continue
            
            # Record transaction
            txn = WalletTransaction(
                customer_id=sub.customer_id,
                type="monthly_fee",
                amount_paise=-sub.monthly_fee_paise,
                description=f"Monthly subscription fee ({sub.plan} plan)"
            )
            db.add(txn)
            
            # Advance next billing date
            sub.next_billing_date = sub.next_billing_date + timedelta(days=30)
            
            await db.commit()
            logger.info(f"Charged ₹{sub.monthly_fee_paise/100:.0f} monthly fee for customer {sub.customer_id}")
            
            # If wallet went negative, suspend + email
            if wallet.balance_paise < 0:
                cust_res = await db.execute(select(Customer).where(Customer.id == sub.customer_id))
                cust = cust_res.scalar_one_or_none()
                if cust:
                    cust.status = "suspended"
                    await db.commit()
                    await notification_service.send_email(
                        to_email=cust.contact_email,
                        subject="Talkar — Monthly Fee Payment Failed",
                        body=f"Hi {cust.contact_name},\n\nYour wallet had insufficient funds for this month's subscription fee (₹{sub.monthly_fee_paise/100:.0f}). Your account has been suspended.\n\nPlease top up your wallet to reactivate.\n\nThe Talkar Team"
                    )
    
    logger.info("Monthly subscription billing complete.")
