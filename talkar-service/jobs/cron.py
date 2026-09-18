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
              AND w.balance_paise < 150000
              AND w.balance_paise >= 50000
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
            WITH MasterSuspensions AS (
                SELECT w.customer_id FROM wallets w
                WHERE w.balance_paise <= 0
                  AND NOT EXISTS (
                    SELECT 1 FROM wallet_transactions wt 
                    WHERE wt.customer_id = w.customer_id 
                    AND wt.amount_paise > 0
                    AND wt.created_at >= now() - interval '14 days'
                  )
            )
            UPDATE customers SET status = 'suspended'
            WHERE status = 'active'
              AND (id IN (SELECT customer_id FROM MasterSuspensions)
                   OR billing_org_id IN (SELECT customer_id FROM MasterSuspensions))
            RETURNING id, dograh_org_id, contact_email
        """)
        suspended_result = await db.execute(suspend_query)
        for row in suspended_result:
            logger.info(f"Suspended customer {row.id}")
            # Block calls in Dograh immediately — this is the actual enforcement
            if row.dograh_org_id:
                try:
                    await dograh_client.block_org_calls(row.dograh_org_id)
                except Exception as e:
                    logger.error(f"Failed to block calls for suspended org {row.dograh_org_id}: {e}")
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
                try:
                    await dograh_client.archive_org(row.dograh_org_id)
                except Exception as e:
                    logger.error(f"Failed to archive Dograh org {row.dograh_org_id} for churned customer {row.id}: {e}")
                    # In a real app we might revert the churn status so it retries, 
                    # but for now we'll just log it and proceed to email them.
                    
            if row.contact_email and "@" in row.contact_email:
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
            if not row.contact_email or "@" not in row.contact_email:
                logger.warning(f"Skipping Day 7 reminder to {row.id}: invalid email '{row.contact_email}'")
                continue
                
            logger.info(f"Sending Day 7 reminder to {row.id}")
            await notification_service.send_email(
                to_email=row.contact_email,
                subject="Complete your Talkar Setup",
                body="It's been 7 days! Please complete your onboarding form to get your agent built."
            )

        # Day 30: Delete completely (only master orgs, let sub-orgs wait for admin review)
        abandoned_query = text("""
            SELECT id, dograh_org_id, dograh_user_id FROM customers
            WHERE status = 'pending_approval'
              AND billing_org_id IS NULL
              AND created_at < now() - interval '30 days'
        """)
        abandoned_result = await db.execute(abandoned_query)
        
        for row in abandoned_result:
            logger.info(f"Deleting abandoned customer {row.id}")
            if row.dograh_org_id:
                await dograh_client.delete_org(row.dograh_org_id)
            await db.execute(text("DELETE FROM phone_number_requests WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM phone_numbers WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM support_requests WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM call_logs WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM wallet_transactions WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM wallets WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM subscriptions WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM agents WHERE customer_id = :id"), {"id": row.id})
            await db.execute(text("DELETE FROM customers WHERE id = :id"), {"id": row.id})
            
        await db.commit()
    logger.info("Abandoned signup cleanup completed.")

async def dispatch_scheduled_reports(ctx):
    """
    Check for organizations with reports enabled and due, then dispatch Talkar email digests.
    Runs every hour at minute 0.
    """
    logger.info("Checking for scheduled organization reports...")
    now_utc = datetime.now(timezone.utc)
    redis = ctx.get("redis")
    
    async with AsyncSessionLocal() as db:
        query = text("""
            SELECT id, company_name, contact_email, report_settings
            FROM customers
            WHERE status = 'active'
              AND report_settings IS NOT NULL
              AND (report_settings->>'enabled')::boolean = true
              AND (
                  report_settings->>'next_due_at' IS NULL
                  OR (report_settings->>'next_due_at')::timestamptz <= now()
              )
        """)
        res = await db.execute(query)
        customers_due = res.fetchall()

        for c_row in customers_due:
            customer_id = c_row.id
            settings = c_row.report_settings or {}
            freq = settings.get("frequency", "weekly")
            
            # Deduplication lock
            lock_key = f"lock:report:{customer_id}:{now_utc.strftime('%Y%m%d%H')}"
            acquired = True
            if redis:
                try:
                    acquired = await redis.set(lock_key, "1", nx=True, ex=3600)
                except Exception as e:
                    logger.warning(f"Redis lock check failed for customer {customer_id}: {e}")
            
            if not acquired:
                continue

            try:
                cust_res = await db.execute(select(Customer).where(Customer.id == customer_id))
                customer = cust_res.scalar_one_or_none()
                if not customer:
                    continue

                sent = await notification_service.send_organization_report(
                    db=db,
                    customer=customer,
                    frequency=freq,
                    is_test=False
                )
                
                if sent:
                    next_due = notification_service.calculate_next_report_due_date(freq).isoformat()
                    updated_settings = {
                        **settings,
                        "last_sent_at": now_utc.isoformat(),
                        "next_due_at": next_due
                    }
                    customer.report_settings = updated_settings
                    await db.commit()
                    logger.info(f"Dispatched scheduled report for customer {customer_id}, next due: {next_due}")
            except Exception as e:
                logger.error(f"Failed to dispatch scheduled report for customer {customer_id}: {e}")

    logger.info("Scheduled organization reports check completed.")


async def dispatch_scheduled_announcements(ctx):
    """
    Check for pending scheduled announcements whose scheduled_for time has arrived.
    Runs periodically.
    """
    from datetime import datetime, timezone
    from db.models import Announcement
    from routers.announcements import execute_announcement_broadcast
    
    now_utc = datetime.now(timezone.utc)
    logger.info("Checking for scheduled announcements to dispatch...")

    async with AsyncSessionLocal() as db:
        query = select(Announcement).where(
            Announcement.status == "scheduled",
            Announcement.scheduled_for <= now_utc
        )
        res = await db.execute(query)
        due_announcements = res.scalars().all()

        for ann in due_announcements:
            try:
                logger.info(f"Dispatching scheduled announcement #{ann.id}: '{ann.title}'")
                await execute_announcement_broadcast(db, ann)
            except Exception as e:
                logger.error(f"Failed to broadcast scheduled announcement #{ann.id}: {e}")

    logger.info("Scheduled announcements check completed.")


