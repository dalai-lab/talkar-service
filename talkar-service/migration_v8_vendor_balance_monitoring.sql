-- Migration v8: Vendor Infrastructure Balance & Credit Monitoring System
-- Adds tables for tracking vendor credit snapshots, alert states, debouncing, and thresholds.

CREATE TABLE IF NOT EXISTS vendor_balance_snapshots (
    id SERIAL PRIMARY KEY,
    vendor VARCHAR(32) NOT NULL,               -- 'plivo', 'deepgram', 'elevenlabs', 'smallest'
    balance_numeric NUMERIC(14, 4),           -- Numeric balance or remaining count (NULL if error/payg)
    units VARCHAR(16) NOT NULL,                 -- 'USD', 'INR', 'characters', 'credits', 'payg'
    quota_limit NUMERIC(14, 4),                 -- Total tier limit (e.g. 100,000 chars)
    status VARCHAR(32) NOT NULL,                -- 'healthy', 'warning', 'critical', 'payg_active', 'not_configured', 'error'
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
    current_tier VARCHAR(32) NOT NULL DEFAULT 'healthy', -- 'healthy', 'warning', 'critical'
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

-- Seed initial vendor configurations
INSERT INTO vendor_alert_states (vendor, current_tier, warning_threshold, critical_threshold)
VALUES 
    ('plivo', 'healthy', 20.00, 2.00),
    ('deepgram', 'healthy', 20.00, 2.00),
    ('elevenlabs', 'healthy', 50000, 5000),
    ('smallest', 'healthy', 500, 50)
ON CONFLICT (vendor) DO NOTHING;
