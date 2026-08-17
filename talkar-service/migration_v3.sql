-- Talkar V3 Database Migration
-- Feature 4: Per-agent billing rate + unique constraint for billing bridge
ALTER TABLE agents ADD COLUMN IF NOT EXISTS per_minute_rate_paise BIGINT;
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_dograh_org_id ON agents(dograh_org_id) WHERE dograh_org_id IS NOT NULL; -- Removed: Talkar needs to support multiple workflows (agents) per org.
CREATE INDEX IF NOT EXISTS idx_agents_dograh_org_id ON agents(dograh_org_id);

-- Feature 3: Support requests table
CREATE TABLE IF NOT EXISTS support_requests (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    type TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    agent_id INTEGER REFERENCES agents(id),
    status TEXT NOT NULL DEFAULT 'open',
    admin_note TEXT,
    resolved_by TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_support_requests_customer ON support_requests(customer_id);
CREATE INDEX IF NOT EXISTS idx_support_requests_status ON support_requests(status);
