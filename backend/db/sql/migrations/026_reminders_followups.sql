-- =================================================================
-- Migration: 026_reminders_followups
-- Description: Adds reminder rules and scheduled follow-up message queue.
-- =================================================================
BEGIN;
CREATE TABLE IF NOT EXISTS reminder_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    offset_minutes INT NOT NULL DEFAULT 0,
    channel TEXT NOT NULL DEFAULT 'whatsapp',
    template_text TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    quiet_hours_start TIME,
    quiet_hours_end TIME,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT reminder_rules_name_nonempty CHECK (length(btrim(name)) > 0),
    CONSTRAINT reminder_rules_template_nonempty CHECK (length(btrim(template_text)) > 0),
    CONSTRAINT reminder_rules_trigger_check CHECK (
        trigger_type IN (
            'manual',
            'before_booking',
            'after_booking',
            'before_payment_expiry',
            'after_payment_failed',
            'after_order_completed',
            'inactive_conversation',
            'custom'
        )
    ),
    CONSTRAINT reminder_rules_channel_check CHECK (
        channel IN ('whatsapp', 'awa_chat', 'sms', 'email', 'manual')
    )
);
CREATE INDEX IF NOT EXISTS idx_reminder_rules_tenant_active
    ON reminder_rules(tenant_id, is_active, trigger_type, created_at DESC)
    WHERE deleted_at IS NULL;
DROP TRIGGER IF EXISTS update_reminder_rules_updated_at ON reminder_rules;
CREATE TRIGGER update_reminder_rules_updated_at
    BEFORE UPDATE ON reminder_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS scheduled_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    rule_id UUID REFERENCES reminder_rules(id) ON DELETE SET NULL,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    booking_id UUID REFERENCES bookings(id) ON DELETE SET NULL,
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    payment_request_id UUID REFERENCES payment_requests(id) ON DELETE SET NULL,
    review_request_id UUID,
    trigger_type TEXT NOT NULL DEFAULT 'manual',
    channel TEXT NOT NULL DEFAULT 'whatsapp',
    message_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    scheduled_for TIMESTAMPTZ NOT NULL,
    locked_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    attempt_count INT NOT NULL DEFAULT 0,
    last_error TEXT,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT scheduled_messages_text_nonempty CHECK (length(btrim(message_text)) > 0),
    CONSTRAINT scheduled_messages_status_check CHECK (
        status IN ('scheduled', 'due', 'snoozed', 'sent', 'failed', 'cancelled', 'skipped')
    ),
    CONSTRAINT scheduled_messages_channel_check CHECK (
        channel IN ('whatsapp', 'awa_chat', 'sms', 'email', 'manual')
    ),
    CONSTRAINT scheduled_messages_trigger_check CHECK (
        trigger_type IN (
            'manual',
            'before_booking',
            'after_booking',
            'before_payment_expiry',
            'after_payment_failed',
            'after_order_completed',
            'inactive_conversation',
            'custom'
        )
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_scheduled_messages_tenant_idempotency
    ON scheduled_messages(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_due
    ON scheduled_messages(tenant_id, status, scheduled_for)
    WHERE deleted_at IS NULL AND status IN ('scheduled', 'due', 'snoozed', 'failed');
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_customer
    ON scheduled_messages(tenant_id, customer_id, scheduled_for DESC)
    WHERE customer_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_booking
    ON scheduled_messages(tenant_id, booking_id, scheduled_for DESC)
    WHERE booking_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_payment
    ON scheduled_messages(tenant_id, payment_request_id, scheduled_for DESC)
    WHERE payment_request_id IS NOT NULL AND deleted_at IS NULL;
DROP TRIGGER IF EXISTS update_scheduled_messages_updated_at ON scheduled_messages;
CREATE TRIGGER update_scheduled_messages_updated_at
    BEFORE UPDATE ON scheduled_messages
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TABLE IF NOT EXISTS scheduled_message_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scheduled_message_id UUID NOT NULL REFERENCES scheduled_messages(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT scheduled_message_events_actor_type_check CHECK (
        actor_type IN ('system', 'tenant_user', 'automation', 'provider')
    )
);
CREATE INDEX IF NOT EXISTS idx_scheduled_message_events_message
    ON scheduled_message_events(tenant_id, scheduled_message_id, created_at DESC);
ALTER TABLE reminder_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_message_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON reminder_rules;
CREATE POLICY tenant_isolation ON reminder_rules
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON scheduled_messages;
CREATE POLICY tenant_isolation ON scheduled_messages
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON scheduled_message_events;
CREATE POLICY tenant_isolation ON scheduled_message_events
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
UPDATE subscription_plans
SET
    features_json = CASE
        WHEN features_json::jsonb ? 'Reminders' THEN features_json
        ELSE (features_json::jsonb || '["Reminders"]'::jsonb)
    END,
    limits_json = jsonb_set(
        limits_json::jsonb,
        '{allowed_modules}',
        CASE
            WHEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) ? 'reminders'
                THEN COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb)
            ELSE COALESCE(limits_json::jsonb -> 'allowed_modules', '[]'::jsonb) || '["reminders"]'::jsonb
        END,
        true
    )
WHERE plan_code IN ('starter', 'pro', 'enterprise');
COMMIT;