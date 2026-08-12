-- Talkar V2 Database Migration

-- 1. Create phone_number_requests table
CREATE TABLE phone_number_requests (
  id                SERIAL PRIMARY KEY,
  customer_id       INTEGER NOT NULL REFERENCES customers(id),
  quantity          INTEGER NOT NULL DEFAULT 1,
  region            TEXT,
  use_case          TEXT,
  status            TEXT DEFAULT 'pending',
  admin_note        TEXT,
  requested_at      TIMESTAMPTZ DEFAULT now(),
  resolved_at       TIMESTAMPTZ
);

-- 2. Backfill subscriptions before altering columns
UPDATE subscriptions SET per_minute_rate_paise = 1200 WHERE plan = 'starter';
UPDATE subscriptions SET per_minute_rate_paise = 1800 WHERE plan = 'pro';

-- 3. Drop dead columns from subscriptions
ALTER TABLE subscriptions DROP COLUMN monthly_fee_paise;
ALTER TABLE subscriptions DROP COLUMN concurrent_call_limit;
ALTER TABLE subscriptions DROP COLUMN next_billing_date;
ALTER TABLE subscriptions DROP COLUMN razorpay_subscription_id;
