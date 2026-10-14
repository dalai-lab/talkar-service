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

-- 7. Fix billing_org_id foreign key constraint to ON DELETE SET NULL to avoid abandoned signup cleanup crashes
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

-- 8. Wallet transaction attribution: sub_org_id and dograh_org_id tracking (Issue 18)
ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS sub_org_id INTEGER REFERENCES customers(id) ON DELETE SET NULL;
ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS dograh_org_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_wallet_transactions_sub_org ON wallet_transactions(sub_org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wallet_transactions_dograh_org ON wallet_transactions(dograh_org_id, created_at DESC);

-- 9. Telephony DID monthly recurring lease billing tracking (Issue 31)
ALTER TABLE phone_numbers ADD COLUMN IF NOT EXISTS last_billed_at TIMESTAMPTZ;

-- 10. Invoice attribution: sub_org_id and dograh_org_id tracking & retro backfill (Issue 34)
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS sub_org_id INTEGER REFERENCES customers(id) ON DELETE SET NULL;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS dograh_org_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_invoices_sub_org ON invoices(sub_org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_invoices_dograh_org ON invoices(dograh_org_id, created_at DESC);
UPDATE invoices i
SET sub_org_id = wt.sub_org_id,
    dograh_org_id = wt.dograh_org_id,
    customer_id = wt.sub_org_id
FROM wallet_transactions wt
WHERE i.wallet_transaction_id = wt.id
  AND wt.sub_org_id IS NOT NULL
  AND i.sub_org_id IS NULL;



