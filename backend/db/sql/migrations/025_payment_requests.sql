-- =================================================================
-- Migration: 025_payment_requests
-- Description: Adds tenant customer payment requests, deposits, and payment events.
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS payment_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    booking_id UUID REFERENCES bookings(id) ON DELETE SET NULL,
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    type TEXT NOT NULL DEFAULT 'custom_payment',
    status TEXT NOT NULL DEFAULT 'draft',
    provider TEXT NOT NULL DEFAULT 'manual',
    provider_payment_link_id TEXT,
    provider_order_id TEXT,
    provider_payment_id TEXT,
    payment_url TEXT,
    expires_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT payment_requests_type_check CHECK (
        type IN ('booking_deposit', 'booking_full_payment', 'order_payment', 'custom_payment', 'balance_due')
    ),
    CONSTRAINT payment_requests_status_check CHECK (
        status IN ('draft', 'sent', 'viewed', 'paid', 'expired', 'cancelled', 'failed', 'refunded')
    ),
    CONSTRAINT payment_requests_provider_check CHECK (
        provider IN ('manual', 'external', 'razorpay', 'upi', 'cash', 'bank_transfer')
    ),
    CONSTRAINT payment_requests_currency_nonempty CHECK (length(btrim(currency)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_requests_tenant_idempotency
    ON payment_requests(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payment_requests_tenant_status
    ON payment_requests(tenant_id, status, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_payment_requests_customer
    ON payment_requests(tenant_id, customer_id, created_at DESC)
    WHERE customer_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_payment_requests_order
    ON payment_requests(tenant_id, order_id, created_at DESC)
    WHERE order_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_payment_requests_booking
    ON payment_requests(tenant_id, booking_id, created_at DESC)
    WHERE booking_id IS NOT NULL AND deleted_at IS NULL;
DROP TRIGGER IF EXISTS update_payment_requests_updated_at ON payment_requests;
CREATE TRIGGER update_payment_requests_updated_at
    BEFORE UPDATE ON payment_requests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS payment_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    payment_request_id UUID NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    provider_event_id TEXT,
    provider_payload JSONB NOT NULL DEFAULT '{}',
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT payment_events_actor_type_check CHECK (
        actor_type IN ('system', 'tenant_user', 'provider', 'automation')
    )
);
CREATE INDEX IF NOT EXISTS idx_payment_events_request_created
    ON payment_events(tenant_id, payment_request_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_events_provider_event
    ON payment_events(tenant_id, provider_event_id)
    WHERE provider_event_id IS NOT NULL;
ALTER TABLE payment_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON payment_requests;
CREATE POLICY tenant_isolation ON payment_requests
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON payment_events;
CREATE POLICY tenant_isolation ON payment_events
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
UPDATE subscription_plans
SET
    features_json = CASE
        WHEN features_json::jsonb ? 'Manual payments' THEN features_json
        ELSE (features_json::jsonb || '["Manual payments"]'::jsonb)
    END,
    limits_json = jsonb_set(
        limits_json::jsonb,
        '{allowed_modules}',
        CASE
            WHEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) ? 'payments'
                THEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb)
            ELSE COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) || '["payments"]'::jsonb
        END,
        true
    )
WHERE plan_code IN ('starter', 'pro', 'enterprise');
COMMIT;