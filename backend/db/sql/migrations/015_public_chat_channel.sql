-- =================================================================
-- Migration: 015_public_chat_channel
-- Description: Adds first-party AWA Chat public link/session channel support.
-- =================================================================
BEGIN;
ALTER TABLE customers
    ALTER COLUMN phone_number DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS channel_subject TEXT,
    ADD COLUMN IF NOT EXISTS source_channel TEXT NOT NULL DEFAULT 'whatsapp';
CREATE UNIQUE INDEX IF NOT EXISTS uniq_customers_tenant_channel_subject
    ON customers(tenant_id, channel_subject)
    WHERE channel_subject IS NOT NULL;
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS channel_type TEXT NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS channel_account_id UUID,
    ADD COLUMN IF NOT EXISTS channel_session_id TEXT,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_conversations_channel
    ON conversations(tenant_id, channel_type, channel_session_id);
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS channel_type TEXT NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS provider_message_id TEXT;
UPDATE messages
SET provider_message_id = whatsapp_message_id
WHERE provider_message_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_messages_channel
    ON messages(tenant_id, channel_type, created_at DESC);
CREATE TABLE IF NOT EXISTS public_chat_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    scan_count INT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}',
    last_opened_at TIMESTAMPTZ,
    disabled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_public_chat_links_tenant
    ON public_chat_links(tenant_id, status);
CREATE TABLE IF NOT EXISTS public_chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    link_id UUID NOT NULL REFERENCES public_chat_links(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    session_token_hash TEXT NOT NULL UNIQUE,
    visitor_name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_public_chat_sessions_tenant
    ON public_chat_sessions(tenant_id, link_id, status);
ALTER TABLE public_chat_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public_chat_sessions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public_chat_links;
CREATE POLICY tenant_isolation ON public_chat_links
    FOR ALL USING (tenant_id = auth_tenant_id());
DROP POLICY IF EXISTS tenant_isolation ON public_chat_sessions;
CREATE POLICY tenant_isolation ON public_chat_sessions
    FOR ALL USING (tenant_id = auth_tenant_id());
COMMIT;