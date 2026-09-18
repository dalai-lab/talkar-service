-- Talkar V4 Database Migration
-- Custom pricing plan columns for subscriptions table

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS custom_config JSONB;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS custom_plan_label TEXT;

