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


