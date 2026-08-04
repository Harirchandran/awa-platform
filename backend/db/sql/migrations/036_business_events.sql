-- =================================================================
-- Migration: 036_business_events
-- Description: Durable tenant-scoped business event outbox.
-- =================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS business_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id UUID,
    source_domain TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    conversation_id UUID,
    customer_id UUID,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'processed', 'failed', 'skipped')),
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_business_events_tenant ON business_events (tenant_id);
CREATE INDEX IF NOT EXISTS idx_business_events_event_type ON business_events (event_type);
CREATE INDEX IF NOT EXISTS idx_business_events_status_available ON business_events (status, available_at);
CREATE INDEX IF NOT EXISTS idx_business_events_aggregate ON business_events (aggregate_type, aggregate_id);
CREATE INDEX IF NOT EXISTS idx_business_events_correlation ON business_events (correlation_id);
CREATE INDEX IF NOT EXISTS idx_business_events_created_at ON business_events (created_at);

DROP TRIGGER IF EXISTS update_business_events_updated_at ON business_events;
CREATE TRIGGER update_business_events_updated_at
    BEFORE UPDATE ON business_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE business_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON business_events;
CREATE POLICY tenant_isolation ON business_events
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));

COMMIT;
