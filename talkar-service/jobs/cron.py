import logging
import json
from datetime import datetime, timedelta, timezone
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
    if not runs:
        logger.info("Nightly reconciliation completed: 0 completed runs found.")
        return

    # Filter out textchat runs upfront (defense-in-depth against dummy CallLog creation)
    billable_runs = [r for r in runs if r.get("mode") != "textchat"]
    if not billable_runs:
        logger.info("Nightly reconciliation completed: 0 billable completed runs found.")
        return

    logger.info(f"Reconciliation: fetched {len(billable_runs)} billable completed runs from Dograh to verify.")

    run_ids = [r["id"] for r in billable_runs if r.get("id")]
    processed_run_ids = set()

    async with AsyncSessionLocal() as db:
        # Bulk query existing processed call logs in chunks to eliminate N+1 queries
        CHUNK_SIZE = 1000
        for i in range(0, len(run_ids), CHUNK_SIZE):
            chunk = run_ids[i : i + CHUNK_SIZE]
            result = await db.execute(
                select(CallLog.dograh_run_id).where(
                    CallLog.dograh_run_id.in_(chunk),
                    CallLog.processed_at.is_not(None)
                )
            )
            processed_run_ids.update(result.scalars().all())

    deducted_count = 0
    skipped_count = len(processed_run_ids)
    error_count = 0

    for run in billable_runs:
        run_id = run["id"]
        if run_id in processed_run_ids:
            continue
        if run.get("mode") == "textchat":
            continue

        try:
            await billing_service.deduct_for_run(run_id)
            deducted_count += 1
            logger.info(f"Reconciliation: successfully deducted for run {run_id}")
        except Exception as e:
            error_count += 1
            logger.error(f"Reconciliation: failed to deduct for run {run_id}: {e}", exc_info=True)
                
    logger.info(
        f"Nightly reconciliation completed: {deducted_count} deducted, "
        f"{skipped_count} already processed, {error_count} failed."
    )

async def check_low_balances(ctx):
    """
    8B - Notify customers when wallet is getting low.
    Runs every hour.

    Respects per-customer settings stored in report_settings:
      - low_balance_alert_paise  : custom threshold (default ₹1,500)
      - email_notifications_enabled : if False, skip email (in-app still fires)
    """
    logger.info("Checking for low balances...")
    MAX_THRESHOLD_PAISE = 5000000  # ₹50,000 — max a customer can configure
    DEFAULT_THRESHOLD_PAISE = 150000  # ₹1,500

    now_utc = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        # Blocker 28: Direct Low Balance Alerts Exclusively to Master Wallet Owners
        query = text("""
            SELECT c.id as customer_id, c.billing_org_id, c.contact_email, c.report_settings,
                   w.customer_id as wallet_customer_id, w.balance_paise, w.low_balance_alerted_at
            FROM customers c
            JOIN wallets w ON w.customer_id = COALESCE(c.billing_org_id, c.id)
            WHERE c.status = 'active'
              AND c.billing_org_id IS NULL
              AND w.balance_paise < :max_threshold
              AND w.balance_paise >= 50000
        """)
        result = await db.execute(query, {"max_threshold": MAX_THRESHOLD_PAISE})
        customers = result.fetchall()
        
        for cust_row in customers:
            rep = cust_row.report_settings or {}
            threshold = rep.get("low_balance_alert_paise", DEFAULT_THRESHOLD_PAISE)

            # Only alert if this customer's wallet is actually below their configured threshold
            if cust_row.balance_paise >= threshold:
                continue

            # 24-hour rate limit check per customer
            last_alerted_str = rep.get("low_balance_alerted_at")
            last_alerted = None
            if last_alerted_str:
                try:
                    last_alerted = datetime.fromisoformat(last_alerted_str)
                    if last_alerted.tzinfo is None:
                        last_alerted = last_alerted.replace(tzinfo=timezone.utc)
                except Exception:
                    last_alerted = None

            # Fallback for master accounts to wallet.low_balance_alerted_at
            if not last_alerted and not cust_row.billing_org_id and cust_row.low_balance_alerted_at:
                last_alerted = cust_row.low_balance_alerted_at
                if last_alerted.tzinfo is None:
                    last_alerted = last_alerted.replace(tzinfo=timezone.utc)

            if last_alerted and (now_utc - last_alerted).total_seconds() < 86400:
                continue

            # Respect email mute preference
            emails_enabled = rep.get("email_notifications_enabled", True)
            cc_email = (rep.get("cc_email") or "").strip() or None

            if emails_enabled and cust_row.contact_email:
                await notification_service.send_email(
                    to_email=cust_row.contact_email,
                    subject="Low Balance Alert",
                    body=f"Your wallet balance is getting low: ₹{cust_row.balance_paise / 100:.2f}. Please top up to keep your agents active.",
                    cc=cc_email,
                )
            else:
                # Still send the in-app push even when emails are muted or contact_email is missing
                await notification_service.push_notification(
                    customer_id=cust_row.customer_id,
                    title="Low Balance Alert",
                    body=f"Low balance: ₹{cust_row.balance_paise / 100:.2f}. Please top up.",
                    notification_type="warning"
                )

            logger.info(f"Sent low balance alert to customer {cust_row.customer_id} (threshold: ₹{threshold//100}, email: {emails_enabled})")
            
            # Record alerted_at in customer's report_settings so each sub-org has its own 24h rate limit
            updated_rep = dict(rep)
            updated_rep["low_balance_alerted_at"] = now_utc.isoformat()
            await db.execute(
                update(Customer)
                .where(Customer.id == cust_row.customer_id)
                .values(report_settings=updated_rep)
            )

            # For master accounts, also update wallet table for backward compatibility
            if not cust_row.billing_org_id:
                await db.execute(
                    update(Wallet)
                    .where(Wallet.customer_id == cust_row.wallet_customer_id)
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
        # Suspend accounts dormant at <= 0 balance for 14+ continuous days
        suspend_query = text("""
            WITH MasterSuspensions AS (
                SELECT w.customer_id FROM wallets w
                WHERE w.balance_paise <= 0
                  AND COALESCE(
                        w.zero_balance_since,
                        (SELECT MAX(wt.created_at) FROM wallet_transactions wt WHERE wt.customer_id = w.customer_id),
                        w.created_at
                      ) <= now() - interval '14 days'
            )
            UPDATE customers SET status = 'suspended', suspended_at = now()
            WHERE status = 'active'
              AND (id IN (SELECT customer_id FROM MasterSuspensions)
                   OR billing_org_id IN (SELECT customer_id FROM MasterSuspensions))
            RETURNING id, dograh_org_id, contact_email, onboarding_form
        """)
        suspended_result = await db.execute(suspend_query)
        suspended_rows = suspended_result.fetchall()

        # Write suspension reason into onboarding_form for each suspended customer
        for row in suspended_rows:
            existing_form = row.onboarding_form or {}
            existing_form["suspension_reason"] = "zero_balance"
            existing_form["suspension_message"] = ""
            await db.execute(
                text("UPDATE customers SET onboarding_form = :form WHERE id = :id"),
                {"form": json.dumps(existing_form), "id": row.id}
            )

        await db.commit()

        # Block calls + notify after commit so DB state is consistent
        for row in suspended_rows:
            logger.info(f"Suspended customer {row.id}")
            if row.dograh_org_id:
                try:
                    await dograh_client.block_org_calls(row.dograh_org_id)
                except Exception as e:
                    logger.error(f"Failed to block calls for suspended org {row.dograh_org_id}: {e}")
            # Use the reason-aware notification (sends in-app push + email)
            try:
                await notification_service.notify_customer_suspended(row.id, reason="zero_balance")
            except Exception as e:
                logger.error(f"Failed to send suspension notification to customer {row.id}: {e}")

        # Churn accounts suspended for 45+ days (cascading to sub-orgs under churned masters)
        churn_query = text("""
            WITH MasterChurns AS (
                SELECT id FROM customers
                WHERE (status = 'churned' OR (status = 'suspended' AND COALESCE(suspended_at, updated_at) < now() - interval '45 days'))
                  AND billing_org_id IS NULL
            ),
            churned_ids AS (
                UPDATE customers SET status = 'churned'
                WHERE status = 'suspended'
                  AND (
                      id IN (SELECT id FROM MasterChurns)
                      OR billing_org_id IN (SELECT id FROM MasterChurns)
                      OR COALESCE(suspended_at, updated_at) < now() - interval '45 days'
                  )
                RETURNING id, dograh_org_id, contact_email
            ),
            release_phones AS (
                UPDATE phone_numbers SET status = 'released' WHERE customer_id IN (SELECT id FROM churned_ids)
            )
            SELECT id, dograh_org_id, contact_email FROM churned_ids
        """)
        churned_result = await db.execute(churn_query)
        churned_rows = list(churned_result)
        for row in churned_rows:
            logger.info(f"Churned customer {row.id}. Archiving Dograh org {row.dograh_org_id}")
            if row.dograh_org_id:
                try:
                    await dograh_client.archive_org(row.dograh_org_id)
                except Exception as e:
                    logger.error(f"Failed to archive Dograh org {row.dograh_org_id} for churned customer {row.id}: {e}")

            # Blocker 19: Release DIDs and cease billing on account churn
            from db.models import PhoneNumber
            try:
                pns_res = await db.execute(
                    select(PhoneNumber).where(PhoneNumber.customer_id == row.id, PhoneNumber.status != "released")
                )
                churned_pns = pns_res.scalars().all()
                for pn in churned_pns:
                    pn.status = "released"
                    if row.dograh_org_id and pn.number:
                        try:
                            await dograh_client.release_phone_number_from_dograh(row.dograh_org_id, pn.number)
                        except Exception as e:
                            logger.warning(f"Failed to release phone number {pn.number} from Dograh for churned customer {row.id}: {e}")
            except Exception as e:
                logger.error(f"Failed to release phone numbers for churned customer {row.id}: {e}")
                    
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

        # Day 30: Delete completely (only master orgs without active sub-orgs)
        abandoned_query = text("""
            SELECT id, dograh_org_id, dograh_user_id FROM customers
            WHERE status = 'pending_approval'
              AND billing_org_id IS NULL
              AND created_at < now() - interval '30 days'
              AND NOT EXISTS (
                  SELECT 1 FROM customers sub
                  WHERE sub.billing_org_id = customers.id
                    AND sub.status IN ('active', 'agent_building', 'approved', 'pending_deposit', 'suspended')
              )
        """)
        abandoned_result = await db.execute(abandoned_query)
        
        for row in abandoned_result:
            try:
                # Issue 13: Exclude master signups if active child sub-organizations exist
                active_subs_res = await db.execute(
                    text("""
                        SELECT 1 FROM customers
                        WHERE billing_org_id = :id
                          AND status IN ('active', 'agent_building', 'approved', 'pending_deposit', 'suspended')
                    """),
                    {"id": row.id}
                )
                if [r for r in active_subs_res]:
                    logger.warning(
                        f"Skipping automated deletion of master customer {row.id}: "
                        f"active child sub-organizations exist under this master."
                    )
                    continue

                logger.info(f"Deleting abandoned customer {row.id}")
                if row.dograh_org_id:
                    try:
                        await dograh_client.delete_org(row.dograh_org_id)
                    except Exception as de:
                        logger.warning(f"Failed to delete Dograh org {row.dograh_org_id} for abandoned customer {row.id}: {de}")

                # 1. Gracefully archive remaining unestablished child sub-orgs
                await db.execute(
                    text("""
                        UPDATE customers 
                        SET status = 'churned' 
                        WHERE billing_org_id = :id 
                          AND status IN ('pending_approval', 'under_review')
                    """),
                    {"id": row.id}
                )

                # 2. Detach dependent sub-orgs so deleting master does not crash on foreign key constraint
                await db.execute(text("UPDATE customers SET billing_org_id = NULL WHERE billing_org_id = :id"), {"id": row.id})

                # 2. Delete related records in dependency order
                await db.execute(text("DELETE FROM phone_number_requests WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM phone_numbers WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM support_requests WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM call_logs WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM notifications WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM invoices WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM wallet_transactions WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM wallets WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM subscriptions WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM agents WHERE customer_id = :id"), {"id": row.id})
                await db.execute(text("DELETE FROM customers WHERE id = :id"), {"id": row.id})
                await db.commit()
            except Exception as e:
                logger.error(f"Failed to delete abandoned customer {row.id}: {e}", exc_info=True)
                await db.rollback()

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


async def bill_monthly_phone_numbers(ctx):
    """
    8G - Monthly recurring telephony DID lease billing (Issue 31).
    Runs daily to process recurring phone number lease deductions.
    """
    logger.info("Starting monthly phone number lease billing check...")
    from services.billing_service import bill_monthly_phone_number_leases
    async with AsyncSessionLocal() as db:
        try:
            results = await bill_monthly_phone_number_leases(db)
            logger.info(f"Phone number lease billing completed: {results}")
        except Exception as e:
            logger.error(f"Error during phone number lease billing: {e}", exc_info=True)


async def poll_vendor_balances(ctx):
    """
    Periodic Infrastructure Check:
    Queries Plivo, Deepgram, ElevenLabs, and Smallest AI balances,
    stores snapshots, evaluates thresholds, and alerts it@4thorbit.com.
    """
    logger.info("Starting periodic upstream vendor balance check...")
    from services.vendor_balance_service import VendorBalanceService
    service = VendorBalanceService()
    try:
        results = await service.sync_all_vendors(force=False)
        logger.info(f"Vendor balance check completed: overall_healthy={results.get('overall_healthy')}")
    except Exception as e:
        logger.error(f"Error during periodic vendor balance check: {e}", exc_info=True)



