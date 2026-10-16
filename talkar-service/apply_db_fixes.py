import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from config import settings

engine = create_async_engine(settings.TALKAR_DB_URL, echo=False)

STATEMENTS = [
    (
        "F-02: Fixing starter plan per_minute_rate_paise",
        "UPDATE subscriptions SET per_minute_rate_paise = 600 WHERE plan = 'starter'",
    ),
    (
        "F-03: Dropping unique index on agents(dograh_org_id)",
        "DROP INDEX IF EXISTS uq_agents_dograh_org_id",
    ),
    (
        "F-04: Adding created_at to wallets",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()",
    ),
    (
        "F-06: Adding UNIQUE constraint to subscriptions.customer_id",
        """DO $$ 
        BEGIN 
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_subscription_customer_id') THEN 
                ALTER TABLE subscriptions ADD CONSTRAINT uq_subscription_customer_id UNIQUE (customer_id); 
            END IF; 
        END $$;""",
    ),
    (
        "F-15: Adding unique index on wallet_transactions(dograh_run_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transactions_dograh_run_id ON wallet_transactions(dograh_run_id) WHERE dograh_run_id IS NOT NULL",
    ),
    (
        "M-10: Making idx_wallet_transactions_razorpay unique",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transactions_razorpay ON wallet_transactions(razorpay_order_id) WHERE razorpay_order_id IS NOT NULL",
    ),
    (
        "E-02: Adding zero_balance_since to wallets",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS zero_balance_since TIMESTAMPTZ",
    ),
    (
        "v8: Adding suspended_at to customers",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ",
    ),
    (
        "v8: Creating vendor_balance_snapshots table",
        """CREATE TABLE IF NOT EXISTS vendor_balance_snapshots (
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
        )""",
    ),
    (
        "v8: Creating idx_vendor_balance_vendor_checked index",
        "CREATE INDEX IF NOT EXISTS idx_vendor_balance_vendor_checked ON vendor_balance_snapshots (vendor, checked_at DESC)",
    ),
    (
        "v8: Creating vendor_alert_states table",
        """CREATE TABLE IF NOT EXISTS vendor_alert_states (
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
        )""",
    ),
]


async def apply_fixes():
    async with engine.connect() as conn:
        for desc, stmt in STATEMENTS:
            print(f"Applying {desc}...")
            try:
                await conn.execute(text(stmt))
                await conn.commit()
                print("  -> Success")
            except Exception as e:
                await conn.rollback()
                print(f"  -> Error: {e}")
    print("\nAll database fixes processed successfully.")


if __name__ == "__main__":
    asyncio.run(apply_fixes())
