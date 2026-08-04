-- =================================================================
-- Migration: 019_escalation_reply_delivery
-- Description: Tenant human replies, public chat close tracking, and approved RAG capture metadata.
-- =================================================================
BEGIN;
ALTER TABLE public_chat_sessions
    ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;
ALTER TABLE tenant_knowledge_base
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_knowledge_base_metadata_source
    ON tenant_knowledge_base USING gin (metadata);
COMMIT;