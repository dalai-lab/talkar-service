-- Talkar V6 Database Migration
-- Enforce UNIQUE index on wallet_transactions(razorpay_order_id) to prevent concurrent double crediting

DROP INDEX IF EXISTS idx_wallet_transactions_razorpay;
CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transactions_razorpay ON wallet_transactions(razorpay_order_id) WHERE razorpay_order_id IS NOT NULL;
