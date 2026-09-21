import os
from arq import cron
from arq.connections import RedisSettings
from jobs.cron import (
    nightly_reconciliation,
    check_low_balances,
    check_suspensions,
    cleanup_abandoned_signups,
    dispatch_scheduled_reports,
    dispatch_scheduled_announcements
)
from db.session import engine
from config import settings

redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

async def startup(ctx):
    print("Worker starting up...")
    # Add anything that needs to persist across job executions in the context
    pass

async def shutdown(ctx):
    print("Worker shutting down...")
    await engine.dispose()

async def send_email_and_push_task(ctx, customer_id: int, to_email: str, subject: str, body: str, notification_type: str = "info", push_body: str | None = None, cc: str | None = None):
    from services.notification_service import send_email_and_push
    await send_email_and_push(customer_id, to_email, subject, body, notification_type, push_body, cc)

async def send_email_task(ctx, to_email: str, subject: str, body: str, cc: str | None = None):
    from services.notification_service import send_email
    await send_email(to_email, subject, body, cc)

async def push_notification_task(ctx, customer_id: int, title: str, body: str, notification_type: str = "info"):
    from services.notification_service import push_notification
    await push_notification(customer_id, title, body, notification_type)

class WorkerSettings:
    functions = [send_email_and_push_task, send_email_task, push_notification_task]
    max_jobs = 10
    cron_jobs = [
        # 8A: Nightly reconciliation (2:00 AM IST)
        # Note: ARQ cron uses UTC by default, but you can set timezone or do math.
        # 2:00 AM IST = 20:30 UTC. Arq cron: minute=30, hour=20
        cron(nightly_reconciliation, minute=30, hour=20),
        
        # 8B: Low balance alert (every hour)
        cron(check_low_balances, minute=0),
        
        # 8C: Suspension check (8:00 AM IST = 02:30 UTC)
        cron(check_suspensions, minute=30, hour=2),
        
        # 8D: Abandoned signup cleanup (Daily at 3:00 AM IST = 21:30 UTC)
        cron(cleanup_abandoned_signups, minute=30, hour=21),

        # 8E: Scheduled Account Performance Reports (Hourly check for due reports)
        cron(dispatch_scheduled_reports, minute=0),

        # 8F: Scheduled Announcements Broadcast (Checked every minute)
        cron(dispatch_scheduled_announcements, minute=None, second=0)
    ]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
