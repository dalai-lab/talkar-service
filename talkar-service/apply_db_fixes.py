import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from config import settings

# Override the DB URL if needed for docker, but settings should load it from env_file
engine = create_async_engine(settings.TALKAR_DB_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def apply_fixes():
    async with SessionLocal() as session:
        try:
            # 1. Fix F-02: Starter plan rate backfill (600 paise / ₹6.00/min)
            print("Applying F-02: Fixing starter plan per_minute_rate_paise...")
            await session.execute(text("UPDATE subscriptions SET per_minute_rate_paise = 600 WHERE plan = 'starter';"))
            
            # 2. Fix F-03: Drop unique index on agents
            print("Applying F-03: Dropping unique index on agents(dograh_org_id)...")
            await session.execute(text("DROP INDEX IF EXISTS uq_agents_dograh_org_id;"))
            
            # 3. Fix F-04: Add created_at to wallets
            print("Applying F-04: Adding created_at to wallets...")
            await session.execute(text("ALTER TABLE wallets ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();"))
            
            # 4. Fix F-06: Add unique constraint to subscriptions(customer_id)
            print("Applying F-06: Adding UNIQUE constraint to subscriptions.customer_id...")
            try:
                await session.execute(text("ALTER TABLE subscriptions ADD CONSTRAINT uq_subscription_customer_id UNIQUE (customer_id);"))
            except Exception as e:
                if "already exists" in str(e):
                    print("Constraint uq_subscription_customer_id already exists.")
                else:
                    raise e

            # 5. Fix F-15: Add unique constraint on wallet_transactions.dograh_run_id
            print("Applying F-15: Adding unique index on wallet_transactions(dograh_run_id)...")
            await session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transactions_dograh_run_id ON wallet_transactions(dograh_run_id) WHERE dograh_run_id IS NOT NULL;"))
            
            # 6. Fix M-10: Make idx_wallet_transactions_razorpay unique
            print("Applying M-10: Making idx_wallet_transactions_razorpay unique...")
            await session.execute(text("DROP INDEX IF EXISTS idx_wallet_transactions_razorpay;"))
            await session.execute(text("CREATE UNIQUE INDEX idx_wallet_transactions_razorpay ON wallet_transactions(razorpay_order_id) WHERE razorpay_order_id IS NOT NULL;"))

            # 7. Fix E-02: Add zero_balance_since to wallets
            print("Applying E-02: Adding zero_balance_since to wallets...")
            await session.execute(text("ALTER TABLE wallets ADD COLUMN IF NOT EXISTS zero_balance_since TIMESTAMPTZ;"))

            # 8. Migration v8: Add suspended_at to customers
            print("Applying v8: Adding suspended_at to customers...")
            await session.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;"))

            # 9. Migration v8: Vendor Balance Monitoring Tables
            print("Applying v8: Creating vendor balance monitoring tables...")
            await session.execute(text("""
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
            """))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_vendor_balance_vendor_checked 
                ON vendor_balance_snapshots (vendor, checked_at DESC);
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS vendor_alert_states (
                    vendor VARCHAR(32) PRIMARY KEY,
                    current_tier VARCHAR(32) NOT NULL DEFAULT 'healthy',
                    last_alert_sent_at TIMESTAMP WITH TIME ZONE,
                    last_alert_tier VARCHAR(32),
                    alerts_sent_in_window INT DEFAULT 0,
                    window_start_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    warning_threshold NUMERIC(14, 4) NOT NULL,
                    critical_threshold NUMERIC(14, 4) NOT NULL,
                    warning_cooldown_hours INT NOT NULL DEFAULT 4,
                    critical_cooldown_hours INT NOT NULL DEFAULT 1,
                    last_resolved_at TIMESTAMP WITH TIME ZONE,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
            """))
                    
            await session.commit()
            print("Successfully applied DB fixes.")
            
        except Exception as e:
            await session.rollback()
            print(f"Error applying fixes: {e}")

if __name__ == "__main__":
    asyncio.run(apply_fixes())
