-- =================================================================
-- Migration: 029_repair_reminders_schema
-- Description: Idempotent repair for databases that missed reminders
--              tables during local or production upgrades.
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
    customer_id UUID,
    conversation_id UUID,
    booking_id UUID,
    order_id UUID,
    payment_request_id UUID,
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
    created_by UUID,
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
ALTER TABLE scheduled_messages
    ADD COLUMN IF NOT EXISTS review_request_id UUID;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'customers'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scheduled_messages_customer_id_fkey'
    ) THEN
        ALTER TABLE scheduled_messages
            ADD CONSTRAINT scheduled_messages_customer_id_fkey
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'conversations'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scheduled_messages_conversation_id_fkey'
    ) THEN
        ALTER TABLE scheduled_messages
            ADD CONSTRAINT scheduled_messages_conversation_id_fkey
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'bookings'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scheduled_messages_booking_id_fkey'
    ) THEN
        ALTER TABLE scheduled_messages
            ADD CONSTRAINT scheduled_messages_booking_id_fkey
            FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE SET NULL;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'orders'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scheduled_messages_order_id_fkey'
    ) THEN
        ALTER TABLE scheduled_messages
            ADD CONSTRAINT scheduled_messages_order_id_fkey
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'payment_requests'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scheduled_messages_payment_request_id_fkey'
    ) THEN
        ALTER TABLE scheduled_messages
            ADD CONSTRAINT scheduled_messages_payment_request_id_fkey
            FOREIGN KEY (payment_request_id) REFERENCES payment_requests(id) ON DELETE SET NULL;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scheduled_messages_created_by_fkey'
    ) THEN
        ALTER TABLE scheduled_messages
            ADD CONSTRAINT scheduled_messages_created_by_fkey
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
    END IF;
END $$;
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
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'review_requests'
    ) AND NOT EXISTS (
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
    actor_user_id UUID,
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT scheduled_message_events_actor_type_check CHECK (
        actor_type IN ('system', 'tenant_user', 'automation', 'provider')
    )
);
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'scheduled_message_events_actor_user_id_fkey'
    ) THEN
        ALTER TABLE scheduled_message_events
            ADD CONSTRAINT scheduled_message_events_actor_user_id_fkey
            FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL;
    END IF;
END $$;
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