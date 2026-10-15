from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import health, customers, wallet, billing, provisioning, admin, notifications, announcements, automation, invoices, vendor_balances
from contextlib import asynccontextmanager
from services import redis_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.init_redis()
    from arq import create_pool
    from arq.connections import RedisSettings
    from config import settings
    import logging
    _startup_log = logging.getLogger(__name__)
    is_prod = getattr(settings, "ENVIRONMENT", "").lower() in ("production", "prod", "staging")
    if is_prod:
        insecure_defaults = [
            "change-me-in-production-billing-token",
            "change-me-in-production-admin-secret",
            "",
        ]
        token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "").strip()
        secret = getattr(settings, "TALKAR_ADMIN_JWT_SECRET", "").strip()

        if token in insecure_defaults or len(token) < 16:
            _startup_log.critical("FATAL: Insecure or default TALKAR_BILLING_API_TOKEN in production! Blocking startup.")
            raise RuntimeError("CRITICAL SECURITY ERROR: TALKAR_BILLING_API_TOKEN must be securely set in production.")

        if secret in insecure_defaults or len(secret) < 16:
            _startup_log.critical("FATAL: Insecure or default TALKAR_ADMIN_JWT_SECRET in production! Blocking startup.")
            raise RuntimeError("CRITICAL SECURITY ERROR: TALKAR_ADMIN_JWT_SECRET must be securely set in production.")
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

            # V6 / V7 Audit Hardening & Ledger Consistency Migrations
            await conn.execute(text("ALTER TABLE wallets ADD COLUMN IF NOT EXISTS zero_balance_since TIMESTAMPTZ;"))
            await conn.execute(text("ALTER TABLE wallets ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();"))
            await conn.execute(text("DROP INDEX IF EXISTS uq_agents_dograh_org_id;"))
            await conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transactions_dograh_run_id
                ON wallet_transactions(dograh_run_id) WHERE dograh_run_id IS NOT NULL;
            """))
            await conn.execute(text("DROP INDEX IF EXISTS idx_wallet_transactions_razorpay;"))
            await conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transactions_razorpay
                ON wallet_transactions(razorpay_order_id) WHERE razorpay_order_id IS NOT NULL;
            """))
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'uq_subscription_customer_id'
                    ) THEN
                        ALTER TABLE subscriptions ADD CONSTRAINT uq_subscription_customer_id UNIQUE (customer_id);
                    END IF;
                END $$;
            """))
            await conn.execute(text("UPDATE subscriptions SET per_minute_rate_paise = 600 WHERE plan = 'starter' AND per_minute_rate_paise != 600;"))
            await conn.execute(text("UPDATE subscriptions SET per_minute_rate_paise = 1800 WHERE plan = 'pro' AND per_minute_rate_paise != 1800;"))
            await conn.execute(text("ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS sub_org_id INTEGER REFERENCES customers(id) ON DELETE SET NULL;"))
            await conn.execute(text("ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS dograh_org_id INTEGER;"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_wallet_transactions_sub_org ON wallet_transactions(sub_org_id, created_at DESC);"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_wallet_transactions_dograh_org ON wallet_transactions(dograh_org_id, created_at DESC);"))
            await conn.execute(text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS sub_org_id INTEGER REFERENCES customers(id) ON DELETE SET NULL;"))
            await conn.execute(text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS dograh_org_id INTEGER;"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_invoices_sub_org ON invoices(sub_org_id, created_at DESC);"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_invoices_dograh_org ON invoices(dograh_org_id, created_at DESC);"))
            await conn.execute(text("""
                UPDATE invoices i
                SET sub_org_id = wt.sub_org_id,
                    dograh_org_id = wt.dograh_org_id,
                    customer_id = wt.sub_org_id
                FROM wallet_transactions wt
                WHERE i.wallet_transaction_id = wt.id
                  AND wt.sub_org_id IS NOT NULL
                  AND i.sub_org_id IS NULL;
            """))
            # Phone number lease recurring billing tracking (Issue 31)
            await conn.execute(text("ALTER TABLE phone_numbers ADD COLUMN IF NOT EXISTS last_billed_at TIMESTAMPTZ;"))
            # Foreign key ON DELETE SET NULL for billing_org_id (Issue 20/32)
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.table_constraints 
                        WHERE constraint_name = 'customers_billing_org_id_fkey'
                    ) THEN
                        ALTER TABLE customers DROP CONSTRAINT customers_billing_org_id_fkey;
                    END IF;
                    ALTER TABLE customers ADD CONSTRAINT customers_billing_org_id_fkey 
                    FOREIGN KEY (billing_org_id) REFERENCES customers(id) ON DELETE SET NULL;
                END $$;
            """))

            # Migration v8: Vendor Infrastructure Balance Monitoring
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS vendor_balance_snapshots (
                    id SERIAL PRIMARY KEY,
                    vendor VARCHAR(32) NOT NULL,
                    balance_numeric NUMERIC(14, 4),
                    units VARCHAR(16) NOT NULL,
                    quota_limit NUMERIC(14, 4),
                    status VARCHAR(32) NOT NULL,
                    auto_recharge_enabled BOOLEAN DEFAULT FALSE,
                    overages_enabled BOOLEAN DEFAULT FALSE,
                    next_reset_at TIMESTAMP WITH TIME ZONE,
                    error_message TEXT,
                    raw_payload JSONB,
                    checked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_vendor_balance_vendor_checked 
                ON vendor_balance_snapshots (vendor, checked_at DESC);

                CREATE TABLE IF NOT EXISTS vendor_alert_states (
                    vendor VARCHAR(32) PRIMARY KEY,
                    current_tier VARCHAR(32) NOT NULL DEFAULT 'healthy',
                    last_alert_sent_at TIMESTAMP WITH TIME ZONE,
                    last_alert_tier VARCHAR(32),
                    alerts_sent_in_window INT DEFAULT 0,
                    window_start_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    warning_threshold NUMERIC(14, 4) NOT NULL,
                    critical_threshold NUMERIC(14, 4) NOT NULL,
                    last_delivery_status VARCHAR(16) DEFAULT 'success',
                    last_delivery_error TEXT,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );

                INSERT INTO vendor_alert_states (vendor, current_tier, warning_threshold, critical_threshold)
                VALUES 
                    ('plivo', 'healthy', 20.00, 2.00),
                    ('deepgram', 'healthy', 20.00, 2.00),
                    ('elevenlabs', 'healthy', 50000, 5000),
                    ('smallest', 'healthy', 500, 50)
                ON CONFLICT (vendor) DO NOTHING;
            """))
    except Exception as e:
        _startup_log.critical(f"FATAL: Database migration / backfill failed on startup: {e}", exc_info=True)
        if is_prod:
            raise RuntimeError(f"Database migration failed on startup: {e}") from e
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
    allow_origin_regex=r"https://.*\.talkar\.in",
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
app.include_router(invoices.router, prefix="/invoices", tags=["Invoices"])
app.include_router(vendor_balances.router)
app.middleware("http")(automation.audit_log_middleware)
app.include_router(automation.router, prefix="/automation/v1", tags=["Automation"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
