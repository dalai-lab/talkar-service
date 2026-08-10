-- Run this to initialize Talkar Database schema
-- Tables: customers, subscriptions, wallets, wallet_transactions, phone_numbers, agents, call_logs

CREATE TABLE customers (
  id                    SERIAL PRIMARY KEY,
  company_name          TEXT NOT NULL,
  industry              TEXT,
  contact_name          TEXT NOT NULL,
  contact_email         TEXT NOT NULL UNIQUE,
  contact_phone         TEXT,
  status                TEXT NOT NULL DEFAULT 'pending_approval',
  onboarding_form       JSONB,
  documents             JSONB,
  dograh_org_id         INTEGER,
  dograh_user_id        INTEGER,
  setup_fee_order_id    TEXT,
  created_at            TIMESTAMPTZ DEFAULT now(),
  updated_at            TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_customers_status ON customers(status);
CREATE INDEX idx_customers_dograh_org_id ON customers(dograh_org_id);

CREATE TABLE subscriptions (
  id                        SERIAL PRIMARY KEY,
  customer_id               INTEGER NOT NULL REFERENCES customers(id),
  plan                      TEXT NOT NULL,
  status                    TEXT NOT NULL DEFAULT 'active',
  monthly_fee_paise         BIGINT NOT NULL,
  per_minute_rate_paise     BIGINT NOT NULL,
  concurrent_call_limit     INTEGER NOT NULL,
  setup_fee_paid            BOOLEAN DEFAULT false,
  start_date                DATE NOT NULL,
  next_billing_date         DATE NOT NULL,
  razorpay_subscription_id  TEXT,
  created_at                TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE wallets (
  id                            SERIAL PRIMARY KEY,
  customer_id                   INTEGER NOT NULL REFERENCES customers(id) UNIQUE,
  balance_paise                 BIGINT NOT NULL DEFAULT 0,
  auto_recharge_enabled         BOOLEAN DEFAULT false,
  auto_recharge_threshold_paise BIGINT DEFAULT 100000,
  auto_recharge_amount_paise    BIGINT DEFAULT 500000,
  razorpay_customer_id          TEXT,
  razorpay_payment_method_id    TEXT,
  low_balance_alerted_at        TIMESTAMPTZ,
  updated_at                    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE wallet_transactions (
  id                SERIAL PRIMARY KEY,
  customer_id       INTEGER NOT NULL REFERENCES customers(id),
  type              TEXT NOT NULL,
  amount_paise      BIGINT NOT NULL,
  description       TEXT,
  dograh_run_id     INTEGER,
  razorpay_order_id TEXT,
  created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_wallet_transactions_customer ON wallet_transactions(customer_id, created_at DESC);
CREATE INDEX idx_wallet_transactions_razorpay ON wallet_transactions(razorpay_order_id) WHERE razorpay_order_id IS NOT NULL;

CREATE TABLE phone_numbers (
  id                    SERIAL PRIMARY KEY,
  customer_id           INTEGER NOT NULL REFERENCES customers(id),
  number                TEXT NOT NULL,
  country               TEXT DEFAULT 'IN',
  plivo_number_id       TEXT,
  dograh_phone_number_id TEXT,
  agent_id              INTEGER, -- Will add foreign key later to avoid circular dependency
  monthly_cost_paise    BIGINT,
  status                TEXT DEFAULT 'active',
  purchased_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE agents (
  id                SERIAL PRIMARY KEY,
  customer_id       INTEGER NOT NULL REFERENCES customers(id),
  name              TEXT NOT NULL,
  use_case          TEXT,
  language          TEXT,
  dograh_workflow_id INTEGER,
  dograh_org_id     INTEGER,
  status            TEXT DEFAULT 'building',
  built_at          TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE phone_numbers ADD CONSTRAINT fk_phone_numbers_agent FOREIGN KEY (agent_id) REFERENCES agents(id);

CREATE TABLE call_logs (
  id                    SERIAL PRIMARY KEY,
  customer_id           INTEGER NOT NULL REFERENCES customers(id),
  agent_id              INTEGER REFERENCES agents(id),
  phone_number_id       INTEGER REFERENCES phone_numbers(id),
  call_type             TEXT,
  caller_number         TEXT,
  duration_seconds      INTEGER DEFAULT 0,
  cost_to_customer_paise BIGINT DEFAULT 0,
  dograh_run_id         INTEGER NOT NULL UNIQUE,
  called_at             TIMESTAMPTZ NOT NULL,
  processed_at          TIMESTAMPTZ
);

CREATE INDEX idx_call_logs_processed_at ON call_logs(processed_at) WHERE processed_at IS NULL;

CREATE TABLE talkar_admins (
  id                    SERIAL PRIMARY KEY,
  email                 TEXT NOT NULL UNIQUE,
  password_hash         TEXT NOT NULL,
  name                  TEXT NOT NULL,
  role                  TEXT NOT NULL DEFAULT 'admin',
  created_at            TIMESTAMPTZ DEFAULT now()
);
