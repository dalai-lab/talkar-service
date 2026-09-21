from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import health, customers, wallet, billing, provisioning, admin, notifications, announcements, automation
from contextlib import asynccontextmanager
from services import redis_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.init_redis()
    from arq import create_pool
    from arq.connections import RedisSettings
    from config import settings
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        from db.session import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.execute(text("""
                UPDATE customers
                SET company_name = COALESCE(
                    NULLIF(TRIM(onboarding_form->>'businessName'), ''),
                    NULLIF(TRIM(onboarding_form->>'company_name'), ''),
                    NULLIF(TRIM(contact_name), ''),
                    SPLIT_PART(contact_email, '@', 1)
                )
                WHERE company_name IS NULL OR TRIM(company_name) = '';
            """))
            
            # Phase 1/2 Migrations - individual commands for asyncpg
            await conn.execute(text("ALTER TABLE agents ADD COLUMN IF NOT EXISTS crm_link TEXT;"))
            await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS crm_links JSONB DEFAULT '[]'::jsonb;"))
            await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS report_settings JSONB DEFAULT '{\"enabled\": false, \"frequency\": \"weekly\", \"recipients\": []}'::jsonb;"))
            
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    type TEXT NOT NULL,
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))

            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS announcements (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'general',
                    channels JSONB DEFAULT '[]'::jsonb,
                    status TEXT NOT NULL DEFAULT 'sent',
                    scheduled_for TIMESTAMP WITH TIME ZONE,
                    sent_at TIMESTAMP WITH TIME ZONE,
                    sent_by INTEGER REFERENCES talkar_admins(id),
                    recipients_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Database migration / backfill skipped/failed: {e}")
    yield
    if hasattr(app.state, 'arq_pool'):
        await app.state.arq_pool.close()
    await redis_client.close_redis()

app = FastAPI(title="Talkar Service API", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://talkar.in",
        "https://admin.talkar.in",
        "https://billing.talkar.in"
    ],
    allow_origin_regex="https://.*\.talkar\.in",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(customers.router, prefix="/customers", tags=["Customers"])
app.include_router(wallet.router, prefix="/wallet", tags=["Wallet"])
app.include_router(billing.router, prefix="/billing", tags=["Billing"])
app.include_router(provisioning.router, prefix="/provisioning", tags=["Provisioning"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
app.include_router(announcements.router)
app.middleware("http")(automation.audit_log_middleware)
app.include_router(automation.router, prefix="/automation/v1", tags=["Automation"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
