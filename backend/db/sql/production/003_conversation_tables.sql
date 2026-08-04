DO $$
BEGIN
    RAISE EXCEPTION 'Deprecated split production schema. Use backend/db/sql/production/000_apply_canonical_schema.sql instead.';
END $$;
\quit 3
-- 003_conversation_tables.sql
-- Deprecated legacy split schema retained only as a historical reference.
BEGIN;
-- 5. CUSTOMERS
CREATE TABLE customers (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    phone_number TEXT NOT NULL,
    name TEXT,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, phone_number)
);
-- 6. CONVERSATIONS (TENANT SAFETY HARDENED)
CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    customer_id UUID NOT NULL REFERENCES customers(id),
    status conversation_status NOT NULL,
    customer_phone_snapshot TEXT,
    last_message_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_conv_tenant_customer
ON conversations(tenant_id, customer_id);
CREATE INDEX idx_conv_last_msg
ON conversations(last_message_at);
-- 7. MESSAGES (HIGHEST LOAD TABLE - HARDENED)
CREATE TABLE messages (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    whatsapp_message_id TEXT NOT NULL,
    direction message_direction NOT NULL,
    message_type TEXT NOT NULL,
    processing_status processing_status NOT NULL DEFAULT 'received',
    retry_count INT NOT NULL DEFAULT 0,
    message_text TEXT,
    raw_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, whatsapp_message_id)
);
CREATE INDEX idx_msg_conv_time
ON messages(conversation_id, created_at DESC);
CREATE INDEX idx_msg_tenant_time
ON messages(tenant_id, created_at DESC);
CREATE INDEX idx_msg_processing_status
ON messages(processing_status);
-- 8. CONVERSATION SESSION STATE
CREATE TABLE conversation_sessions (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    conversation_id UUID NOT NULL UNIQUE REFERENCES conversations(id),
    current_module TEXT,
    current_intent TEXT,
    state_json JSONB NOT NULL,
    sequence_number INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_conversation_sessions_sequence_number CHECK (sequence_number >= 0)
);
COMMIT;