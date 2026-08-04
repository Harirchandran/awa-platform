-- =================================================================
-- AWA Complete Database Schema — Consolidated Single Source of Truth
-- =================================================================
-- This file is IDEMPOTENT — safe to run multiple times on any DB.
-- Running this single file on a fresh Postgres instance will create
-- the ENTIRE schema: enums, functions, tables, indexes, triggers,
-- RLS policies, extensions, and seed data.
--
-- Consolidates: base schema + migrations 003–009, plus schema-equivalent of 010–039
-- Last consolidated: 2026-06-26
-- =================================================================

BEGIN;

-- Create mock auth schema and functions if they do not exist (e.g. running in standard PostgreSQL environments like CI)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
        CREATE SCHEMA auth;
        CREATE FUNCTION auth.uid() RETURNS UUID AS 'SELECT NULL::UUID;' LANGUAGE SQL;
        CREATE FUNCTION auth.jwt() RETURNS JSONB AS 'SELECT ''{}''::JSONB;' LANGUAGE SQL;
    END IF;
END $$;

-- Create stubs for RLS tenant isolation functions so that schema compiles before migrations overwrite them
CREATE OR REPLACE FUNCTION auth_has_tenant_access(target_tenant_id UUID) RETURNS BOOLEAN AS 'SELECT FALSE;' LANGUAGE SQL;
CREATE OR REPLACE FUNCTION auth_has_tenant_write_access(target_tenant_id UUID) RETURNS BOOLEAN AS 'SELECT FALSE;' LANGUAGE SQL;


-- =================================================================
-- 1. EXTENSIONS
-- =================================================================

-- Create supabase_admin role if it does not exist (needed by pgvector extension in Supabase image)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_admin') THEN
        CREATE ROLE supabase_admin;
    END IF;
END $$;

-- Enable required extensions for pgvector and trigram indices in standard PostgreSQL
CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector for RAG embeddings (Migration 007)
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- trigram index support for lexical RAG fallback

-- =================================================================
-- 2. ENUM TYPES
-- =================================================================

DO $$ BEGIN
    CREATE TYPE tenant_status AS ENUM ('trial', 'active', 'suspended', 'expired');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE conversation_status AS ENUM ('active', 'closed', 'escalated');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE message_direction AS ENUM ('inbound', 'outbound');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE booking_status AS ENUM ('confirmed', 'cancelled', 'completed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE processing_status AS ENUM (
        'received', 'queued', 'processing', 'completed', 'failed', 'dlq'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- =================================================================
-- 3. UTILITY FUNCTIONS
-- =================================================================

-- Auto-update updated_at timestamp trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- =================================================================
-- 4. CORE TABLES
-- =================================================================

-- 4.1 SUBSCRIPTION PLANS (Billing — Migration 009)
CREATE TABLE IF NOT EXISTS subscription_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_code TEXT UNIQUE,
    name TEXT NOT NULL,
    monthly_price_cents INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'INR',
    razorpay_plan_id TEXT UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    features_json JSONB NOT NULL DEFAULT '[]',
    limits_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT subscription_plans_monthly_price_cents_non_negative CHECK (monthly_price_cents >= 0)
);

DROP TRIGGER IF EXISTS update_subscription_plans_updated_at ON subscription_plans;
CREATE TRIGGER update_subscription_plans_updated_at
    BEFORE UPDATE ON subscription_plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4.1b BUSINESS TYPES (Dynamic Presets - Migration 028)
CREATE TABLE IF NOT EXISTS business_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    enabled_modules JSONB NOT NULL DEFAULT '[]',
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS update_business_types_updated_at ON business_types;
CREATE TRIGGER update_business_types_updated_at
    BEFORE UPDATE ON business_types
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4.1c TOOL CATALOG (DB-backed workflow tool metadata - Migration 031)
CREATE TABLE IF NOT EXISTS tool_catalog (
    key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'workflow',
    description TEXT NOT NULL DEFAULT '',
    icon_key TEXT NOT NULL DEFAULT 'wrench',
    route_path TEXT,
    actions JSONB NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 100,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tool_catalog_key_format CHECK (key ~ '^[a-z0-9_]+$'),
    CONSTRAINT tool_catalog_actions_array CHECK (jsonb_typeof(actions) = 'array')
);

DROP TRIGGER IF EXISTS update_tool_catalog_updated_at ON tool_catalog;
CREATE TRIGGER update_tool_catalog_updated_at
    BEFORE UPDATE ON tool_catalog
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4.2 TENANTS
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE,
    plan_code TEXT NOT NULL DEFAULT 'trial',
    status tenant_status NOT NULL DEFAULT 'trial',
    ai_mode TEXT NOT NULL DEFAULT 'standard',                -- Migration 006: 'standard' or 'concierge'
    razorpay_customer_id TEXT UNIQUE,                        -- Migration 009
    razorpay_subscription_id TEXT UNIQUE,                    -- Migration 009
    trial_ends_at TIMESTAMPTZ,                               -- Migration 009
    current_period_end TIMESTAMPTZ,                          -- Migration 009
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN tenants.ai_mode IS 'Plan-gated AI capability profile. Runtime uses the unified agent for all profiles; profile controls available autonomy/capability semantics.';

CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);

DROP TRIGGER IF EXISTS update_tenants_updated_at ON tenants;
CREATE TRIGGER update_tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4.3 USERS
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number TEXT UNIQUE NOT NULL,
    email TEXT,
    name TEXT,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4.4 USER <-> TENANT MAPPING
CREATE TABLE IF NOT EXISTS user_tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, tenant_id)
);

-- 4.5 WHATSAPP ACCOUNTS
CREATE TABLE IF NOT EXISTS whatsapp_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number TEXT NOT NULL,
    phone_number_id TEXT,
    provider_account_id TEXT,
    waba_id TEXT,
    business_id TEXT,
    access_token TEXT,
    token_expires_at TIMESTAMPTZ,
    onboarding_mode TEXT NOT NULL DEFAULT 'manual',
    app_id TEXT,
    display_name TEXT,                                        -- Used by Admin wizard
    verification_status TEXT NOT NULL DEFAULT 'pending',
    connected_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(phone_number)
);

CREATE INDEX IF NOT EXISTS idx_wa_tenant ON whatsapp_accounts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_wa_phone ON whatsapp_accounts(phone_number);
CREATE INDEX IF NOT EXISTS idx_wa_phone_number_id ON whatsapp_accounts(phone_number_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_wa_phone_number_id
    ON whatsapp_accounts(phone_number_id)
    WHERE phone_number_id IS NOT NULL;

-- 4.6 BRANCHES
CREATE TABLE IF NOT EXISTS branches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    config_overrides JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_branches_tenant ON branches(tenant_id);

DROP TRIGGER IF EXISTS update_branches_updated_at ON branches;
CREATE TRIGGER update_branches_updated_at
    BEFORE UPDATE ON branches
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4.7 TENANT CONFIG (WITH UNIQUE ACTIVE SAFETY)
CREATE TABLE IF NOT EXISTS tenant_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    version INT NOT NULL DEFAULT 1,
    config_json JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Safety: Only one active config per tenant at any time
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_config_per_tenant
    ON tenant_configs(tenant_id) WHERE is_active = true;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_tenant_config_version
    ON tenant_configs(tenant_id, version);

-- 4.8 TENANT FEATURE FLAGS
CREATE TABLE IF NOT EXISTS tenant_feature_flags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    feature_key TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, feature_key)
);

DROP TRIGGER IF EXISTS update_feature_flags_updated_at ON tenant_feature_flags;
CREATE TRIGGER update_feature_flags_updated_at
    BEFORE UPDATE ON tenant_feature_flags
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4.9 TENANT COST DAILY
CREATE TABLE IF NOT EXISTS tenant_cost_daily (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    cost_date DATE NOT NULL DEFAULT CURRENT_DATE,
    ai_token_cost NUMERIC(12,4) DEFAULT 0,
    compute_cost NUMERIC(12,4) DEFAULT 0,
    ai_tokens_used INT DEFAULT 0,
    tool_action_count INT DEFAULT 0,
    total_credits_used NUMERIC(12,4) DEFAULT 0,
    message_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, cost_date)
);

-- 4.10 PROMPT REGISTRY (Migration 005)
CREATE TABLE IF NOT EXISTS prompt_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE, -- NULL means global/system prompt
    prompt_name TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique constraint for tenant/prompt_name/version
CREATE UNIQUE INDEX IF NOT EXISTS uniq_prompt_version
    ON prompt_registry(tenant_id, prompt_name, version);

DROP TRIGGER IF EXISTS update_prompt_registry_updated_at ON prompt_registry;
CREATE TRIGGER update_prompt_registry_updated_at
    BEFORE UPDATE ON prompt_registry
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 4.11 RETENTION POLICIES (Migration 004 — DPDP §8(7) / GDPR Art. 5(1)(e))
CREATE TABLE IF NOT EXISTS retention_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL,       -- 'messages', 'conversations', 'sessions', 'audit_logs'
    retention_days INT NOT NULL,       -- number of days to keep data
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, resource_type)
);

COMMENT ON TABLE retention_policies IS 'DPDP §8(7): Per-tenant data retention periods';

-- 4.12 TENANT KNOWLEDGE BASE (Migration 007 — Enterprise RAG / pgvector)
CREATE TABLE IF NOT EXISTS tenant_knowledge_base (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_title TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536),           -- 1536 dimensions for OpenAI text-embedding-3-small
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT tenant_knowledge_title_nonempty CHECK (length(btrim(document_title)) > 0),
    CONSTRAINT tenant_knowledge_chunk_min_length CHECK (length(btrim(chunk_text)) >= 10)
);

CREATE INDEX IF NOT EXISTS idx_tenant_knowledge_lexical
    ON tenant_knowledge_base USING gin (
        to_tsvector('simple', COALESCE(document_title, '') || ' ' || COALESCE(chunk_text, ''))
    );

CREATE INDEX IF NOT EXISTS idx_tenant_knowledge_trgm
    ON tenant_knowledge_base USING gin (
        (lower(COALESCE(document_title, '') || ' ' || COALESCE(chunk_text, ''))) gin_trgm_ops
    );

CREATE INDEX IF NOT EXISTS idx_tenant_knowledge_embedding_hnsw
    ON tenant_knowledge_base USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_knowledge_base_metadata_source
    ON tenant_knowledge_base USING gin (metadata);

-- =================================================================
-- 5. CONVERSATION TABLES
-- =================================================================

-- 5.1 CUSTOMERS (with DPDP/GDPR Consent — Migration 003; tombstone — Migration 039)
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number TEXT,
    name TEXT,
    email TEXT,
    metadata JSONB DEFAULT '{}',
    -- DPDP §6 / GDPR Art. 6-7: Consent tracking
    consent_status TEXT NOT NULL DEFAULT 'pending',  -- pending | granted | withdrawn
    consent_at TIMESTAMPTZ,
    consent_version TEXT,      -- e.g. "v1.0"
    consent_method TEXT,       -- e.g. "whatsapp_opt_in"
    channel_subject TEXT,
    source_channel TEXT NOT NULL DEFAULT 'whatsapp',
    -- DPDP §12 / GDPR Art.17: 72-hour tombstone erasure (Migration 039)
    status TEXT NOT NULL DEFAULT 'active',           -- active | inactive | suspended | pending_erasure
    erasure_scheduled_at TIMESTAMPTZ,               -- set to NOW()+72h when erasure is scheduled
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, phone_number),
    CONSTRAINT chk_customers_status
        CHECK (status IN ('active', 'inactive', 'suspended', 'pending_erasure'))
);

ALTER TABLE customers
    ALTER COLUMN phone_number DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS channel_subject TEXT,
    ADD COLUMN IF NOT EXISTS source_channel TEXT NOT NULL DEFAULT 'whatsapp';

COMMENT ON COLUMN customers.consent_status IS 'DPDP §6: pending | granted | withdrawn';
COMMENT ON COLUMN customers.consent_at IS 'Timestamp when consent was last updated';
COMMENT ON COLUMN customers.consent_version IS 'Version of privacy policy accepted (e.g. v1.0)';
COMMENT ON COLUMN customers.consent_method IS 'How consent was obtained (e.g. whatsapp_opt_in)';

CREATE INDEX IF NOT EXISTS idx_customers_tenant ON customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_customers_consent ON customers(tenant_id, consent_status);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_customers_tenant_channel_subject
    ON customers(tenant_id, channel_subject)
    WHERE channel_subject IS NOT NULL;
-- Partial index for privacy_reaper: only indexes rows awaiting hard-delete (Migration 039)
CREATE INDEX IF NOT EXISTS idx_customers_pending_erasure
    ON customers (erasure_scheduled_at)
    WHERE status = 'pending_erasure';

DROP TRIGGER IF EXISTS update_customers_updated_at ON customers;
CREATE TRIGGER update_customers_updated_at
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 5.1a CRM Lite tags and notes
CREATE TABLE IF NOT EXISTS customer_tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#64748b',
    description TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT customer_tags_name_nonempty CHECK (length(btrim(name)) > 0),
    CONSTRAINT customer_tags_color_format CHECK (color ~ '^#[0-9A-Fa-f]{6}$')
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_customer_tags_tenant_name_active
    ON customer_tags(tenant_id, lower(name))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_customer_tags_tenant_active
    ON customer_tags(tenant_id, created_at DESC)
    WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS update_customer_tags_updated_at ON customer_tags;
CREATE TRIGGER update_customer_tags_updated_at
    BEFORE UPDATE ON customer_tags
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS customer_tag_links (
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL REFERENCES customer_tags(id) ON DELETE CASCADE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, customer_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_customer_tag_links_tag
    ON customer_tag_links(tenant_id, tag_id);

CREATE TABLE IF NOT EXISTS customer_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    author_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    note TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'internal',
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT customer_notes_note_nonempty CHECK (length(btrim(note)) > 0),
    CONSTRAINT customer_notes_visibility_check CHECK (visibility IN ('internal'))
);

CREATE INDEX IF NOT EXISTS idx_customer_notes_customer_created
    ON customer_notes(tenant_id, customer_id, created_at DESC)
    WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS update_customer_notes_updated_at ON customer_notes;
CREATE TRIGGER update_customer_notes_updated_at
    BEFORE UPDATE ON customer_notes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 5.2 CONVERSATIONS (TENANT SAFETY HARDENED)
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status conversation_status NOT NULL DEFAULT 'active',
    channel_type TEXT NOT NULL DEFAULT 'whatsapp',
    channel_account_id UUID,
    channel_session_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    customer_phone_snapshot TEXT,
    last_message_at TIMESTAMPTZ,
    escalation_reason TEXT,
    escalated_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    resume_mode TEXT NOT NULL DEFAULT 'automatic',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversations_resume_mode_check
        CHECK (resume_mode IN ('automatic', 'customer', 'tenant', 'manual'))
);

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS channel_type TEXT NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS channel_account_id UUID,
    ADD COLUMN IF NOT EXISTS channel_session_id TEXT,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS escalation_reason TEXT,
    ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS resume_mode TEXT NOT NULL DEFAULT 'automatic';

ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_resume_mode_check;

ALTER TABLE conversations
    ADD CONSTRAINT conversations_resume_mode_check
    CHECK (resume_mode IN ('automatic', 'customer', 'tenant', 'manual'));

CREATE INDEX IF NOT EXISTS idx_conv_tenant_customer
    ON conversations(tenant_id, customer_id);

CREATE INDEX IF NOT EXISTS idx_conv_last_msg
    ON conversations(last_message_at);

CREATE INDEX IF NOT EXISTS idx_conv_tenant_status
    ON conversations(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_conversations_escalated
    ON conversations(tenant_id, escalated_at DESC)
    WHERE status = 'escalated';

CREATE INDEX IF NOT EXISTS idx_conversations_escalation_queue
    ON conversations(tenant_id, status, escalated_at DESC)
    WHERE status = 'escalated';

CREATE INDEX IF NOT EXISTS idx_conversations_assigned_to
    ON conversations(tenant_id, assigned_to)
    WHERE assigned_to IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conversations_channel
    ON conversations(tenant_id, channel_type, channel_session_id);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_whatsapp_conversation_per_customer
    ON conversations(tenant_id, customer_id)
    WHERE status = 'active'
      AND COALESCE(channel_type, 'whatsapp') = 'whatsapp'
      AND channel_session_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_channel_session_conversation
    ON conversations(tenant_id, channel_type, channel_session_id)
    WHERE status = 'active' AND channel_session_id IS NOT NULL;

COMMENT ON COLUMN conversations.escalation_reason IS 'Reason captured when the conversation is escalated to a human.';
COMMENT ON COLUMN conversations.escalated_at IS 'Timestamp when the conversation entered escalated status.';
COMMENT ON COLUMN conversations.resolved_at IS 'Timestamp when a human escalation was cleared/resolved.';
COMMENT ON COLUMN conversations.assigned_to IS 'User UUID currently assigned to the escalated conversation, when available.';
COMMENT ON COLUMN conversations.resume_mode IS 'Mode used by automation when resuming after human escalation.';

DROP TRIGGER IF EXISTS update_conversations_updated_at ON conversations;
CREATE TRIGGER update_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 5.3 MESSAGES (HIGHEST LOAD TABLE — HARDENED)
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    whatsapp_message_id TEXT NOT NULL,
    direction message_direction NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'text',
    processing_status processing_status NOT NULL DEFAULT 'received',
    delivery_status TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    message_text TEXT,
    raw_payload JSONB,
    channel_type TEXT NOT NULL DEFAULT 'whatsapp',
    provider_message_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, whatsapp_message_id)
);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS channel_type TEXT NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS provider_message_id TEXT;

CREATE INDEX IF NOT EXISTS idx_msg_conv_time
    ON messages(conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_msg_tenant_time
    ON messages(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_msg_processing_status
    ON messages(processing_status);

CREATE INDEX IF NOT EXISTS idx_messages_channel
    ON messages(tenant_id, channel_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_awa_processing_recovery
    ON messages(tenant_id, processing_status, created_at)
    WHERE channel_type = 'awa_chat' AND direction = 'inbound';

-- 5.4 CONVERSATION SESSION STATE
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
    current_module TEXT,
    current_intent TEXT,
    config_version INT NOT NULL DEFAULT 1,
    state_json JSONB NOT NULL DEFAULT '{"config_version": 1, "state": {}}'::jsonb,
    sequence_number INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_conversation_sessions_sequence_number CHECK (sequence_number >= 0),
    CONSTRAINT chk_conversation_sessions_config_version_authority CHECK (
        config_version >= 1
        AND jsonb_typeof(state_json -> 'config_version') = 'number'
        AND (state_json ->> 'config_version')::INT = config_version
    )
);

DROP TRIGGER IF EXISTS update_sessions_updated_at ON conversation_sessions;
CREATE TRIGGER update_sessions_updated_at
    BEFORE UPDATE ON conversation_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- 5.5 PUBLIC CHAT LINKS
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

DROP TRIGGER IF EXISTS update_public_chat_links_updated_at ON public_chat_links;
CREATE TRIGGER update_public_chat_links_updated_at
    BEFORE UPDATE ON public_chat_links
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- 5.6 PUBLIC CHAT SESSIONS
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
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_public_chat_sessions_tenant
    ON public_chat_sessions(tenant_id, link_id, status);

DROP TRIGGER IF EXISTS update_public_chat_sessions_updated_at ON public_chat_sessions;
CREATE TRIGGER update_public_chat_sessions_updated_at
    BEFORE UPDATE ON public_chat_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- =================================================================
-- 6. BUSINESS TABLES
-- =================================================================

-- 6.1 BOOKINGS
CREATE TABLE IF NOT EXISTS bookings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES conversations(id),
    branch_id UUID REFERENCES branches(id),
    service_name TEXT NOT NULL,
    booking_date DATE NOT NULL,
    booking_time TIME,
    status booking_status NOT NULL DEFAULT 'confirmed',
    booking_data JSONB DEFAULT '{}',
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bookings_tenant ON bookings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_bookings_customer ON bookings(customer_id);
CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(tenant_id, booking_date);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_bookings_tenant_idempotency
    ON bookings(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

DROP TRIGGER IF EXISTS update_bookings_updated_at ON bookings;
CREATE TRIGGER update_bookings_updated_at
    BEFORE UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 6.2 ORDERS
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    channel_type TEXT NOT NULL DEFAULT 'awa_chat',
    status TEXT NOT NULL DEFAULT 'new',
    currency TEXT NOT NULL DEFAULT 'INR',
    subtotal_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    fulfillment_type TEXT NOT NULL DEFAULT 'pickup',
    payment_method TEXT NOT NULL DEFAULT 'not_collected',
    payment_status TEXT NOT NULL DEFAULT 'not_collected',
    customer_snapshot JSONB NOT NULL DEFAULT '{}',
    delivery_address TEXT,
    order_data JSONB NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT orders_status_check CHECK (status IN ('new', 'accepted', 'preparing', 'ready', 'completed', 'cancelled')),
    CONSTRAINT orders_fulfillment_check CHECK (fulfillment_type IN ('pickup', 'delivery', 'in_store')),
    CONSTRAINT orders_payment_status_check CHECK (payment_status IN ('not_collected', 'pending', 'paid', 'failed', 'refunded'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_orders_tenant_idempotency
    ON orders(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_orders_tenant_created ON orders(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_status ON orders(tenant_id, status, created_at DESC);

DROP TRIGGER IF EXISTS update_orders_updated_at ON orders;
CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price NUMERIC(12,2) NOT NULL DEFAULT 0,
    line_total NUMERIC(12,2) NOT NULL DEFAULT 0,
    variant_data JSONB NOT NULL DEFAULT '{}',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_items_tenant_order ON order_items(tenant_id, order_id);

CREATE TABLE IF NOT EXISTS order_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    event_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_events_tenant_order ON order_events(tenant_id, order_id, created_at);

-- 6.3 CATALOG AND INVENTORY
CREATE TABLE IF NOT EXISTS catalog_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    sort_order INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_categories_name_nonempty CHECK (length(btrim(name)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_catalog_categories_tenant_name_active
    ON catalog_categories(tenant_id, lower(name))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_categories_tenant_sort
    ON catalog_categories(tenant_id, sort_order, created_at DESC)
    WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS update_catalog_categories_updated_at ON catalog_categories;
CREATE TRIGGER update_catalog_categories_updated_at
    BEFORE UPDATE ON catalog_categories
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS catalog_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    category_id UUID REFERENCES catalog_categories(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    description TEXT,
    price NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (price >= 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    sku TEXT,
    image_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    stock_tracking_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    stock_quantity NUMERIC(12,2) NOT NULL DEFAULT 0,
    low_stock_threshold NUMERIC(12,2),
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_items_name_nonempty CHECK (length(btrim(name)) > 0),
    CONSTRAINT catalog_items_currency_nonempty CHECK (length(btrim(currency)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_catalog_items_tenant_category
    ON catalog_items(tenant_id, category_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_items_tenant_lookup
    ON catalog_items(tenant_id, lower(name))
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_catalog_items_tenant_sku_active
    ON catalog_items(tenant_id, lower(sku))
    WHERE sku IS NOT NULL AND deleted_at IS NULL;

DROP TRIGGER IF EXISTS update_catalog_items_updated_at ON catalog_items;
CREATE TRIGGER update_catalog_items_updated_at
    BEFORE UPDATE ON catalog_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS catalog_item_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    price_delta NUMERIC(12,2) NOT NULL DEFAULT 0,
    sku TEXT,
    stock_quantity NUMERIC(12,2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT catalog_item_variants_name_nonempty CHECK (length(btrim(name)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_catalog_item_variants_item
    ON catalog_item_variants(tenant_id, item_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_catalog_item_variants_tenant_sku_active
    ON catalog_item_variants(tenant_id, lower(sku))
    WHERE sku IS NOT NULL AND deleted_at IS NULL;

DROP TRIGGER IF EXISTS update_catalog_item_variants_updated_at ON catalog_item_variants;
CREATE TRIGGER update_catalog_item_variants_updated_at
    BEFORE UPDATE ON catalog_item_variants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS inventory_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
    variant_id UUID REFERENCES catalog_item_variants(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    quantity_delta NUMERIC(12,2) NOT NULL DEFAULT 0,
    reason TEXT,
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT inventory_events_type_check CHECK (
        event_type IN ('manual_adjustment', 'order_reserved', 'order_confirmed', 'order_cancelled', 'restock', 'correction')
    )
);

CREATE INDEX IF NOT EXISTS idx_inventory_events_item_created
    ON inventory_events(tenant_id, item_id, created_at DESC);

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
        trigger_type IN ('manual', 'before_booking', 'after_booking', 'before_payment_expiry', 'after_payment_failed', 'after_order_completed', 'inactive_conversation', 'custom')
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
        trigger_type IN ('manual', 'before_booking', 'after_booking', 'before_payment_expiry', 'after_payment_failed', 'after_order_completed', 'after_payment_completed', 'inactive_conversation', 'custom', 'review_requested')
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
        status IN ('draft', 'scheduled', 'sent', 'responded', 'public_review_requested', 'negative_feedback_escalated', 'skipped', 'expired')
    ),
    CONSTRAINT review_requests_source_check CHECK (
        source IN ('manual', 'booking', 'order', 'reminder', 'automation', 'booking_completed_automation')
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


-- =================================================================
-- 7. AUDIT & OPERATIONS
-- =================================================================

-- 7.1 AUDIT LOGS
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    actor_id UUID,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id UUID,
    details JSONB DEFAULT '{}',
    ip_address TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);


-- =================================================================
-- 8. DATA RETENTION & PURGE FUNCTIONS (Migrations 004 + 008)
-- =================================================================

-- Purge messages older than retention period
CREATE OR REPLACE FUNCTION purge_old_messages(retention_days INT DEFAULT 180)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM messages
    WHERE created_at < NOW() - (retention_days || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    -- Log the purge action
    INSERT INTO audit_logs (id, tenant_id, action, details, created_at)
    SELECT gen_random_uuid(),
           '00000000-0000-0000-0000-000000000000'::UUID,
           'data_retention_purge',
           jsonb_build_object(
               'resource', 'messages',
               'deleted_count', deleted_count,
               'retention_days', retention_days,
               'purged_at', NOW()
           ),
           NOW()
    WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'audit_logs');

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Auto-close stale conversations
CREATE OR REPLACE FUNCTION close_stale_conversations(inactive_days INT DEFAULT 30)
RETURNS INT AS $$
DECLARE
    closed_count INT;
BEGIN
    UPDATE conversations
    SET status = 'closed'
    WHERE status = 'active'
      AND last_message_at < NOW() - (inactive_days || ' days')::INTERVAL;

    GET DIAGNOSTICS closed_count = ROW_COUNT;
    RETURN closed_count;
END;
$$ LANGUAGE plpgsql;

-- Remove expired session data
CREATE OR REPLACE FUNCTION purge_expired_sessions(grace_days INT DEFAULT 7)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM conversation_sessions
    WHERE expires_at IS NOT NULL
      AND expires_at < NOW() - (grace_days || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Purge old audit logs (Migration 008)
CREATE OR REPLACE FUNCTION purge_old_audit_logs()
RETURNS void AS $$
BEGIN
    DELETE FROM audit_logs
    WHERE created_at < NOW() - INTERVAL '90 days';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION purge_old_messages IS 'DPDP §8(7): Purge messages older than retention period';
COMMENT ON FUNCTION close_stale_conversations IS 'Auto-close conversations with no activity';
COMMENT ON FUNCTION purge_expired_sessions IS 'Remove expired session data';


-- =================================================================
-- 9. ROW LEVEL SECURITY (RLS)
-- =================================================================

-- Enable RLS on all tenant-scoped tables
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_feature_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_cost_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_item_variants ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE reminder_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_message_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE prompt_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE retention_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_knowledge_base ENABLE ROW LEVEL SECURITY;
ALTER TABLE public_chat_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public_chat_sessions ENABLE ROW LEVEL SECURITY;

-- RLS Policies: Tenant users can only see their own tenant's data
-- These use the JWT claim: auth.jwt() -> 'app_metadata' -> 'tenant_id'

-- Helper function to get current tenant_id from JWT
CREATE OR REPLACE FUNCTION auth_tenant_id()
RETURNS UUID AS $$
BEGIN
    RETURN (auth.jwt() -> 'app_metadata' ->> 'tenant_id')::UUID;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Tenants: users see only their own tenant
DROP POLICY IF EXISTS tenant_isolation ON tenants;
CREATE POLICY tenant_isolation ON tenants
    FOR ALL USING (id = auth_tenant_id());

-- WhatsApp Accounts
DROP POLICY IF EXISTS tenant_isolation ON whatsapp_accounts;
CREATE POLICY tenant_isolation ON whatsapp_accounts
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Branches
DROP POLICY IF EXISTS tenant_isolation ON branches;
CREATE POLICY tenant_isolation ON branches
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Tenant Configs
DROP POLICY IF EXISTS tenant_isolation ON tenant_configs;
CREATE POLICY tenant_isolation ON tenant_configs
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Feature Flags
DROP POLICY IF EXISTS tenant_isolation ON tenant_feature_flags;
CREATE POLICY tenant_isolation ON tenant_feature_flags
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Cost Daily
DROP POLICY IF EXISTS tenant_isolation ON tenant_cost_daily;
CREATE POLICY tenant_isolation ON tenant_cost_daily
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Customers
DROP POLICY IF EXISTS tenant_isolation ON customers;
CREATE POLICY tenant_isolation ON customers
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON customer_tags;
CREATE POLICY tenant_isolation ON customer_tags
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON customer_tag_links;
CREATE POLICY tenant_isolation ON customer_tag_links
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON customer_notes;
CREATE POLICY tenant_isolation ON customer_notes
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Conversations
DROP POLICY IF EXISTS tenant_isolation ON conversations;
CREATE POLICY tenant_isolation ON conversations
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Messages
DROP POLICY IF EXISTS tenant_isolation ON messages;
CREATE POLICY tenant_isolation ON messages
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Sessions
DROP POLICY IF EXISTS tenant_isolation ON conversation_sessions;
CREATE POLICY tenant_isolation ON conversation_sessions
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Bookings
DROP POLICY IF EXISTS tenant_isolation ON bookings;
CREATE POLICY tenant_isolation ON bookings
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Orders
DROP POLICY IF EXISTS tenant_isolation ON orders;
CREATE POLICY tenant_isolation ON orders
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON order_items;
CREATE POLICY tenant_isolation ON order_items
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON order_events;
CREATE POLICY tenant_isolation ON order_events
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON catalog_categories;
CREATE POLICY tenant_isolation ON catalog_categories
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON catalog_items;
CREATE POLICY tenant_isolation ON catalog_items
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON catalog_item_variants;
CREATE POLICY tenant_isolation ON catalog_item_variants
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON inventory_events;
CREATE POLICY tenant_isolation ON inventory_events
    FOR SELECT USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON payment_requests;
CREATE POLICY tenant_isolation ON payment_requests
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON payment_events;
CREATE POLICY tenant_isolation ON payment_events
    FOR SELECT USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON reminder_rules;
CREATE POLICY tenant_isolation ON reminder_rules
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON scheduled_messages;
CREATE POLICY tenant_isolation ON scheduled_messages
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON scheduled_message_events;
CREATE POLICY tenant_isolation ON scheduled_message_events
    FOR SELECT USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON review_settings;
CREATE POLICY tenant_isolation ON review_settings
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON review_requests;
CREATE POLICY tenant_isolation ON review_requests
    FOR ALL USING (tenant_id = auth_tenant_id());

DROP POLICY IF EXISTS tenant_isolation ON review_events;
CREATE POLICY tenant_isolation ON review_events
    FOR SELECT USING (tenant_id = auth_tenant_id());

-- Audit Logs
DROP POLICY IF EXISTS tenant_isolation ON audit_logs;
CREATE POLICY tenant_isolation ON audit_logs
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Prompt Registry
DROP POLICY IF EXISTS tenant_isolation ON prompt_registry;
CREATE POLICY tenant_isolation ON prompt_registry
    FOR ALL USING (tenant_id = auth_tenant_id() OR tenant_id IS NULL);

-- Retention Policies
DROP POLICY IF EXISTS tenant_isolation ON retention_policies;
CREATE POLICY tenant_isolation ON retention_policies
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Knowledge Base
DROP POLICY IF EXISTS tenant_isolation ON tenant_knowledge_base;
CREATE POLICY tenant_isolation ON tenant_knowledge_base
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Public Chat Links
DROP POLICY IF EXISTS tenant_isolation ON public_chat_links;
CREATE POLICY tenant_isolation ON public_chat_links
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Public Chat Sessions
DROP POLICY IF EXISTS tenant_isolation ON public_chat_sessions;
CREATE POLICY tenant_isolation ON public_chat_sessions
    FOR ALL USING (tenant_id = auth_tenant_id());

-- Business Types
DROP POLICY IF EXISTS read_all ON business_types;
CREATE POLICY read_all ON business_types
    FOR SELECT USING (TRUE);

-- Tool Catalog
DROP POLICY IF EXISTS read_all ON tool_catalog;
CREATE POLICY read_all ON tool_catalog
    FOR SELECT USING (TRUE);

-- Service role bypass (for backend server operations)
-- The service_role key bypasses RLS by default in Supabase


-- =================================================================
-- 10. SEED DATA (Development / Testing)
-- =================================================================

\if :{?AWA_SKIP_DEMO_SEED}
\echo 'Skipping AWA demo tenant seed data'
\else

-- Test Tenant
INSERT INTO tenants (id, name, slug, plan_code, status)
VALUES ('00000000-0000-0000-0000-000000000001', 'SmileDent Dental Clinic', 'smiledent_clinic', 'ENTERPRISE', 'active')
ON CONFLICT (id) DO NOTHING;

INSERT INTO tenants (id, name, slug, plan_code, status)
VALUES ('00000000-0000-0000-0000-000000000002', 'GlowUp Beauty Studio', 'glowup_salon', 'PROFESSIONAL', 'active')
ON CONFLICT (id) DO NOTHING;

-- WhatsApp Account Mapping
INSERT INTO whatsapp_accounts (id, tenant_id, phone_number, phone_number_id, verification_status)
VALUES (
    'a0000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000001',
    '+15556377680',
    '1013394988526222',
    'verified'
) ON CONFLICT (phone_number) DO NOTHING;

INSERT INTO whatsapp_accounts (id, tenant_id, phone_number, phone_number_id, verification_status)
VALUES (
    'a0000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000002',
    '+919876543210',
    'wa_phone_id_salon_001',
    'verified'
) ON CONFLICT (phone_number) DO NOTHING;

-- Local Docker mock auth user used only when ENV=docker and token=mock_token
INSERT INTO users (id, phone_number, email, name)
VALUES (
    '00000000-0000-0000-0000-000000000099',
    '+15550000099',
    'admin@docker.local',
    'Docker Admin'
) ON CONFLICT (id) DO NOTHING;

INSERT INTO user_tenants (id, user_id, tenant_id, role)
VALUES (
    '00000000-0000-0000-0000-000000000098',
    '00000000-0000-0000-0000-000000000099',
    '00000000-0000-0000-0000-000000000001',
    'super_admin'
) ON CONFLICT (user_id, tenant_id) DO NOTHING;

-- Test Tenant Config (SmileDent)
INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active)
VALUES (
    'c0000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000001',
    1,
    '{
        "business_name": "SmileDent Dental Clinic",
        "business_type": "clinic",
        "timezone": "Asia/Kolkata",
        "default_language": "en",
        "ai_mode": "standard",
        "working_hours": {
            "monday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "tuesday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "wednesday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "thursday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "friday": {"is_open": true, "open_time": "09:00", "close_time": "18:00"},
            "saturday": {"is_open": true, "open_time": "09:00", "close_time": "13:00"},
            "sunday": {"is_open": false, "open_time": "09:00", "close_time": "17:00"}
        },
        "services": [
            {"id": "checkup", "name": "General Checkup", "duration_mins": 30, "price": 500, "required_fields": []},
            {"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 45, "price": 800, "required_fields": []},
            {"id": "filling", "name": "Cavity Filling", "duration_mins": 60, "price": 1500, "required_fields": []}
        ],
        "modules": {
            "booking": {
                "enabled": true,
                "fields": [
                    {"key": "patient_name", "label": "Patient Name", "type": "string", "required": true},
                    {"key": "service_id", "label": "Service", "type": "select", "options_from": "services", "required": true},
                    {"key": "preferred_date", "label": "Preferred Date", "type": "date", "required": true},
                    {"key": "preferred_time", "label": "Preferred Time", "type": "time", "required": true},
                    {"key": "phone", "label": "Contact Number", "type": "phone", "required": true}
                ]
            },
            "lead_capture": {"enabled": false, "fields": []},
            "support": {"enabled": false, "fields": []},
            "faq": {"enabled": true, "fields": []},
            "human_handoff": {"enabled": true, "fields": []}
        },
        "ai_tone": "professional, warm, and reassuring",
        "faqs": [
            {"question": "Where are you located?", "answer": "We are at 123 MG Road, Bangalore."},
            {"question": "Do you accept insurance?", "answer": "Yes, we accept all major insurance providers."},
            {"question": "What are your timings?", "answer": "Mon-Fri 9AM-6PM, Sat 9AM-1PM."}
        ],
        "escalation_phone": "+91-9876543210",
        "greeting_message": "Hello! Welcome to SmileDent Dental Clinic. How can I help you today?",
        "fallback_message": "I am not sure yet. Could you share a little more detail?",
        "escalation_message": "I am connecting you with a human agent. They will be with you shortly.",
        "ignored_numbers": [],
        "limits": {},
        "custom_instructions": "Always ask patients about allergies before confirming a booking."
    }'::JSONB,
    TRUE
) ON CONFLICT (id) DO NOTHING;

-- Test Tenant Config (GlowUp)
INSERT INTO tenant_configs (id, tenant_id, version, config_json, is_active)
VALUES (
    'c0000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000002',
    1,
    '{
        "business_name": "GlowUp Beauty Studio",
        "business_type": "salon",
        "timezone": "Asia/Kolkata",
        "default_language": "en",
        "ai_mode": "standard",
        "working_hours": {
            "monday": {"is_open": true, "open_time": "10:00", "close_time": "20:00"},
            "tuesday": {"is_open": true, "open_time": "10:00", "close_time": "20:00"},
            "wednesday": {"is_open": true, "open_time": "10:00", "close_time": "20:00"},
            "thursday": {"is_open": true, "open_time": "10:00", "close_time": "20:00"},
            "friday": {"is_open": true, "open_time": "10:00", "close_time": "21:00"},
            "saturday": {"is_open": true, "open_time": "10:00", "close_time": "21:00"},
            "sunday": {"is_open": true, "open_time": "11:00", "close_time": "18:00"}
        },
        "services": [
            {"id": "haircut", "name": "Haircut & Styling", "duration_mins": 45, "price": 600, "required_fields": []},
            {"id": "facial", "name": "Classic Facial", "duration_mins": 60, "price": 1200, "required_fields": []},
            {"id": "manicure", "name": "Manicure", "duration_mins": 30, "price": 400, "required_fields": []}
        ],
        "modules": {
            "booking": {
                "enabled": true,
                "fields": [
                    {"key": "customer_name", "label": "Your Name", "type": "string", "required": true},
                    {"key": "service_id", "label": "Service", "type": "select", "options_from": "services", "required": true},
                    {"key": "preferred_date", "label": "Date", "type": "date", "required": true},
                    {"key": "stylist_preference", "label": "Preferred Stylist", "type": "string", "required": false}
                ]
            },
            "lead_capture": {
                "enabled": true,
                "fields": [
                    {"key": "name", "label": "Name", "type": "string", "required": true},
                    {"key": "interest", "label": "Interested In", "type": "string", "required": true}
                ]
            },
            "support": {"enabled": false, "fields": []},
            "faq": {"enabled": true, "fields": []},
            "human_handoff": {"enabled": true, "fields": []}
        },
        "ai_tone": "friendly, casual, and enthusiastic",
        "greeting_message": "Hey! Welcome to GlowUp Studio. What can we help you with today?",
        "fallback_message": "I am not sure yet. Could you share a little more detail?",
        "escalation_message": "I am connecting you with a human agent. They will be with you shortly.",
        "ignored_numbers": [],
        "limits": {},
        "custom_instructions": "Always recommend our Instagram page @glowup_studio. Offer a 10% discount for first-time customers."
    }'::JSONB,
    TRUE
) ON CONFLICT (id) DO NOTHING;

\endif

-- Seed Global Prompts
INSERT INTO prompt_registry (tenant_id, prompt_name, prompt_text, version, is_active)
VALUES
    (NULL, 'core_routing_agent', 'You are the primary Routing Agent. Classify user intent.', 1, TRUE),
    (NULL, 'sales_agent', 'You are a professional Sales Agent. Persuade the user gracefully.', 1, TRUE),
    (NULL, 'support_agent', 'You are a warm Support Agent. Fix technical issues patiently.', 1, TRUE)
ON CONFLICT DO NOTHING;

-- Seed Billing Plans with enforcement metadata
INSERT INTO subscription_plans (plan_code, name, monthly_price_cents, currency, razorpay_plan_id, is_active, features_json, limits_json)
VALUES
    (
        'trial',
        'Free Trial',
        0,
        'INR',
        'awa_trial',
        TRUE,
        '["300 messages", "Standard AI", "Booking, order, and FAQ tools"]',
        '{"monthly_messages":300,"daily_messages":50,"monthly_ai_tokens":100000,"monthly_credits":1000,"allowed_ai_modes":["standard"],"allowed_modules":["booking","order","faq","human_handoff"]}'
    ),
    (
        'starter',
        'Starter',
        99900,
        'INR',
        'awa_starter',
        TRUE,
        '["1,000 messages", "Standard AI", "Booking, order, payments, reminders, reviews, and FAQ tools"]',
        '{"monthly_messages":1000,"daily_messages":150,"monthly_ai_tokens":300000,"monthly_credits":3000,"allowed_ai_modes":["standard"],"allowed_modules":["booking","order","payments","reminders","reviews","faq","human_handoff"]}'
    ),
    (
        'pro',
        'Pro',
        299900,
        'INR',
        'awa_pro',
        TRUE,
        '["5,000 messages", "Concierge AI", "Booking, order, payments, reminders, reviews, lead, support, and FAQ tools"]',
        '{"monthly_messages":5000,"daily_messages":750,"monthly_ai_tokens":2000000,"monthly_credits":10000,"allowed_ai_modes":["standard","concierge"],"allowed_modules":["booking","order","payments","reminders","reviews","lead_capture","support","faq","human_handoff"]}'
    ),
    (
        'enterprise',
        'Enterprise',
        999900,
        'INR',
        'awa_enterprise',
        TRUE,
        '["Custom usage", "Concierge AI", "All tools", "Priority support"]',
        '{"monthly_messages":null,"daily_messages":null,"monthly_ai_tokens":null,"monthly_credits":null,"allowed_ai_modes":["standard","concierge"],"allowed_modules":["*"]}'
    )
ON CONFLICT (razorpay_plan_id) DO UPDATE SET
    plan_code = EXCLUDED.plan_code,
    name = EXCLUDED.name,
    monthly_price_cents = EXCLUDED.monthly_price_cents,
    currency = EXCLUDED.currency,
    is_active = EXCLUDED.is_active,
    features_json = EXCLUDED.features_json,
    limits_json = EXCLUDED.limits_json;

-- Seed Tool Catalog
INSERT INTO tool_catalog (key, label, category, description, icon_key, route_path, actions, sort_order, is_active)
VALUES
    ('booking', 'Bookings', 'Scheduling', 'Collect service, date, time, and customer details for appointments.', 'calendar_check', '/dashboard/bookings', '[]', 10, TRUE),
    ('order', 'Orders', 'Commerce', 'Take orders, manage fulfillment, and keep the customer catalog ready.', 'shopping_bag', '/dashboard/orders', '[{"label":"Orders","href":"/dashboard/orders"},{"label":"Catalog","href":"/dashboard/catalog"}]', 20, TRUE),
    ('payments', 'Payments', 'Commerce', 'Create payment requests, deposits, and manual collection records.', 'wallet', '/dashboard/payments', '[]', 30, TRUE),
    ('reminders', 'Reminders', 'Automation', 'Schedule booking, payment, order, and follow-up messages.', 'bell', '/dashboard/reminders', '[]', 40, TRUE),
    ('reviews', 'Reviews', 'Growth', 'Collect ratings and route happy customers toward public review channels.', 'star', '/dashboard/reviews', '[]', 50, TRUE),
    ('lead_capture', 'Lead Capture', 'Sales', 'Collect prospect details before customers are ready to book or buy.', 'megaphone', NULL, '[]', 60, TRUE),
    ('support', 'Support', 'Service', 'Collect issue details and route support requests with context.', 'headphones', NULL, '[]', 70, TRUE),
    ('faq', 'FAQ Answers', 'Knowledge', 'Answer common questions using the tenant knowledge and FAQ setup.', 'circle_help', NULL, '[]', 80, TRUE),
    ('human_handoff', 'Human Handoff', 'Handoff', 'Let customers ask for a person when automation should step aside.', 'user_round_check', NULL, '[]', 90, TRUE)
ON CONFLICT (key) DO UPDATE
SET label = EXCLUDED.label,
    category = EXCLUDED.category,
    description = EXCLUDED.description,
    icon_key = EXCLUDED.icon_key,
    route_path = EXCLUDED.route_path,
    actions = EXCLUDED.actions,
    sort_order = EXCLUDED.sort_order,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- Seed Dynamic Business Types
INSERT INTO business_types (code, label, enabled_modules, note)
VALUES
    ('salon', 'Salon / Beauty Studio', '["booking", "reminders", "reviews", "lead_capture", "faq", "human_handoff"]', 'Appointments first. Orders stay off unless the salon sells products.'),
    ('clinic', 'Clinic / Healthcare', '["booking", "reminders", "reviews", "support", "faq", "human_handoff"]', 'Appointment and patient support focused.'),
    ('restaurant', 'Restaurant / Cafe', '["order", "payments", "reminders", "reviews", "faq", "human_handoff"]', 'Ordering first. Booking can be enabled separately for table reservations.'),
    ('retail', 'Retail / Store', '["order", "payments", "reminders", "reviews", "lead_capture", "faq", "human_handoff"]', 'Product ordering and lead capture focused.'),
    ('services', 'Local Services', '["booking", "reminders", "reviews", "lead_capture", "support", "faq", "human_handoff"]', 'Good for repair, consulting, agencies, and other service businesses.'),
    ('education', 'Education / Coaching', '["booking", "reminders", "reviews", "lead_capture", "faq", "human_handoff"]', 'Inquiry and appointment focused.'),
    ('minimal', 'Minimal Assistant', '["faq", "human_handoff"]', 'Safest starting point when the business workflow is unclear.')
ON CONFLICT (code) DO UPDATE SET
    label = EXCLUDED.label,
    enabled_modules = EXCLUDED.enabled_modules,
    note = EXCLUDED.note,
    updated_at = NOW();

-- 4.23 TENANT INVITATIONS
CREATE TABLE IF NOT EXISTS tenant_invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    invited_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    accepted_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tenant_invitations_status_check CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    CONSTRAINT tenant_invitations_role_check CHECK (role IN ('member', 'operator', 'tenant_admin', 'admin', 'owner'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_tenant_invitations_active_pending
    ON tenant_invitations(tenant_id, lower(email))
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_tenant_invitations_token
    ON tenant_invitations(token_hash);

CREATE INDEX IF NOT EXISTS idx_tenant_invitations_tenant_status
    ON tenant_invitations(tenant_id, status);

DROP TRIGGER IF EXISTS update_tenant_invitations_updated_at ON tenant_invitations;
CREATE TRIGGER update_tenant_invitations_updated_at
    BEFORE UPDATE ON tenant_invitations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE tenant_invitations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON tenant_invitations;
CREATE POLICY tenant_isolation ON tenant_invitations
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));



-- Backend Auth Foundation (Migration 034)
CREATE TABLE IF NOT EXISTS user_credentials (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    password_algorithm TEXT NOT NULL DEFAULT 'bcrypt',
    email_confirmed_at TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT,
    ip_hash TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash
    ON user_sessions (refresh_token_hash)
    WHERE revoked_at IS NULL;


-- Leads Schema (Migration 035)
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

CREATE INDEX IF NOT EXISTS idx_leads_tenant ON leads (tenant_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads (created_at);
CREATE INDEX IF NOT EXISTS idx_leads_customer ON leads (customer_id);
CREATE INDEX IF NOT EXISTS idx_leads_conversation ON leads (conversation_id);

DROP TRIGGER IF EXISTS update_leads_updated_at ON leads;
CREATE TRIGGER update_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON leads;
CREATE POLICY tenant_isolation ON leads
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));

-- Business Events / Outbox (Migration 036)
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_conversation_turn_message
ON business_events (tenant_id, event_type, aggregate_id)
WHERE event_type = 'conversation.turn.received'
  AND aggregate_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_booking_completed
ON business_events (tenant_id, event_type, aggregate_id)
WHERE event_type = 'booking.completed' AND aggregate_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_review_requested_causation
ON business_events (tenant_id, event_type, causation_id)
WHERE event_type = 'review.requested' AND causation_id IS NOT NULL;

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
