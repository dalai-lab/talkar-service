import os
from arq import cron
from arq.connections import RedisSettings
from jobs.cron import (
    nightly_reconciliation,
    check_low_balances,
    check_suspensions,
    cleanup_abandoned_signups
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

class WorkerSettings:
    functions = []
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
        cron(cleanup_abandoned_signups, minute=30, hour=21)
    ]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
