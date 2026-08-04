-- =================================================================
-- Migration: 035_leads_schema
-- Description: Creates the leads table for structured lead capture.
-- =================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    source_channel VARCHAR(50) NOT NULL DEFAULT 'public_chat',
    name VARCHAR(255),
    phone VARCHAR(50),
    email VARCHAR(255),
    interest TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'converted', 'lost')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for fast query and filtering
CREATE INDEX IF NOT EXISTS idx_leads_tenant ON leads (tenant_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads (created_at);
CREATE INDEX IF NOT EXISTS idx_leads_customer ON leads (customer_id);
CREATE INDEX IF NOT EXISTS idx_leads_conversation ON leads (conversation_id);

-- Auto-update updated_at trigger
DROP TRIGGER IF EXISTS update_leads_updated_at ON leads;
CREATE TRIGGER update_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Enable RLS and isolate by tenant
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON leads;
CREATE POLICY tenant_isolation ON leads
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));

COMMIT;
