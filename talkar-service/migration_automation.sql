CREATE TABLE IF NOT EXISTS automation_api_keys (
    id SERIAL PRIMARY KEY,
    key_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created_by_admin_id INTEGER REFERENCES talkar_admins(id),
    scopes JSONB DEFAULT '[]'::jsonb,
    rate_limit_per_minute INTEGER DEFAULT 60,
    last_used_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS automation_audit_log (
    id BIGSERIAL PRIMARY KEY,
    api_key_id INTEGER REFERENCES automation_api_keys(id),
    endpoint TEXT NOT NULL,
    customer_id INTEGER,
    dograh_org_id TEXT,
    payload JSONB,
    response_status INTEGER,
    ip_address TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_automation_audit_api_key ON automation_audit_log(api_key_id);
CREATE INDEX IF NOT EXISTS idx_automation_audit_customer ON automation_audit_log(customer_id);
CREATE INDEX IF NOT EXISTS idx_automation_audit_dograh_org ON automation_audit_log(dograh_org_id);
CREATE INDEX IF NOT EXISTS idx_automation_keys_hash ON automation_api_keys(key_hash);
