-- =================================================================
-- Migration: 027_review_collection
-- Description: Adds tenant review requests, review settings, and events.
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS review_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    public_review_url TEXT,
    ask_after_booking_completed BOOLEAN NOT NULL DEFAULT TRUE,
    ask_after_order_completed BOOLEAN NOT NULL DEFAULT TRUE,
    ask_delay_minutes INT NOT NULL DEFAULT 1440 CHECK (ask_delay_minutes >= 0 AND ask_delay_minutes <= 525600),
    positive_rating_threshold INT NOT NULL DEFAULT 4 CHECK (positive_rating_threshold >= 1 AND positive_rating_threshold <= 5),
    negative_feedback_escalation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    request_template TEXT NOT NULL DEFAULT 'How was your experience with us? Please reply with a rating from 1 to 5.',
    positive_template TEXT NOT NULL DEFAULT 'Thank you. Would you mind leaving us a public review?',
    negative_template TEXT NOT NULL DEFAULT 'I am sorry about that. I have shared this with the team so they can help.',
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT review_settings_templates_nonempty CHECK (
        length(btrim(request_template)) > 0
        AND length(btrim(positive_template)) > 0
        AND length(btrim(negative_template)) > 0
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_review_settings_tenant
    ON review_settings(tenant_id);
DROP TRIGGER IF EXISTS update_review_settings_updated_at ON review_settings;
CREATE TRIGGER update_review_settings_updated_at
    BEFORE UPDATE ON review_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS review_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    booking_id UUID REFERENCES bookings(id) ON DELETE SET NULL,
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'manual',
    rating INT CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    feedback_text TEXT,
    public_review_url TEXT,
    sent_at TIMESTAMPTZ,
    responded_at TIMESTAMPTZ,
    public_requested_at TIMESTAMPTZ,
    escalated_at TIMESTAMPTZ,
    skipped_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT review_requests_status_check CHECK (
        status IN (
            'draft',
            'scheduled',
            'sent',
            'responded',
            'public_review_requested',
            'negative_feedback_escalated',
            'skipped',
            'expired'
        )
    ),
    CONSTRAINT review_requests_source_check CHECK (
        source IN ('manual', 'booking', 'order', 'reminder', 'automation')
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_review_requests_tenant_idempotency
    ON review_requests(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_review_requests_tenant_status
    ON review_requests(tenant_id, status, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_review_requests_customer
    ON review_requests(tenant_id, customer_id, created_at DESC)
    WHERE customer_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_review_requests_booking
    ON review_requests(tenant_id, booking_id, created_at DESC)
    WHERE booking_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_review_requests_order
    ON review_requests(tenant_id, order_id, created_at DESC)
    WHERE order_id IS NOT NULL AND deleted_at IS NULL;
DROP TRIGGER IF EXISTS update_review_requests_updated_at ON review_requests;
CREATE TRIGGER update_review_requests_updated_at
    BEFORE UPDATE ON review_requests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS review_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    review_request_id UUID NOT NULL REFERENCES review_requests(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT review_events_actor_type_check CHECK (
        actor_type IN ('system', 'tenant_user', 'customer', 'automation', 'scheduler')
    )
);
CREATE INDEX IF NOT EXISTS idx_review_events_request_created
    ON review_events(tenant_id, review_request_id, created_at DESC);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'scheduled_messages_review_request_id_fkey'
    ) THEN
        ALTER TABLE scheduled_messages
            ADD CONSTRAINT scheduled_messages_review_request_id_fkey
            FOREIGN KEY (review_request_id) REFERENCES review_requests(id) ON DELETE SET NULL;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_review_request
    ON scheduled_messages(tenant_id, review_request_id, scheduled_for DESC)
    WHERE review_request_id IS NOT NULL AND deleted_at IS NULL;
ALTER TABLE review_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON review_settings;
CREATE POLICY tenant_isolation ON review_settings
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON review_requests;
CREATE POLICY tenant_isolation ON review_requests
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON review_events;
CREATE POLICY tenant_isolation ON review_events
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
UPDATE subscription_plans
SET
    features_json = CASE
        WHEN features_json::jsonb ? 'Review collection' THEN features_json
        ELSE (features_json::jsonb || '["Review collection"]'::jsonb)
    END,
    limits_json = jsonb_set(
        limits_json::jsonb,
        '{allowed_modules}',
        CASE
            WHEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) ? 'reviews'
                THEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb)
            ELSE COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) || '["reviews"]'::jsonb
        END,
        true
    )
WHERE plan_code IN ('starter', 'pro', 'enterprise');
COMMIT;