-- Talkar V7 Database Migration: Audit Hardening & Ledger Consistency

-- 1. Ensure wallets table tracks zero_balance_since for grace period calculations
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS zero_balance_since TIMESTAMPTZ;

-- 2. Ensure created_at exists on wallets
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

-- 3. Idempotent deductions: Unique index on dograh_run_id preventing double deductions
CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transactions_dograh_run_id
ON wallet_transactions(dograh_run_id) WHERE dograh_run_id IS NOT NULL;

-- 4. Idempotent credits: Unique index on razorpay_order_id preventing duplicate webhook credits
DROP INDEX IF EXISTS idx_wallet_transactions_razorpay;
CREATE UNIQUE INDEX idx_wallet_transactions_razorpay
ON wallet_transactions(razorpay_order_id) WHERE razorpay_order_id IS NOT NULL;

-- 5. Drop obsolete single-agent constraint per org to allow multi-agent setups
DROP INDEX IF EXISTS uq_agents_dograh_org_id;

-- 6. Harmonize subscription unit rates
UPDATE subscriptions SET per_minute_rate_paise = 600 WHERE plan = 'starter';
UPDATE subscriptions SET per_minute_rate_paise = 1800 WHERE plan = 'pro';
