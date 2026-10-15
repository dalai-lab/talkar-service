-- Migration v8: Add suspended_at column to customers table for reliable churn tracking (Blocker 26)
ALTER TABLE customers ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;
