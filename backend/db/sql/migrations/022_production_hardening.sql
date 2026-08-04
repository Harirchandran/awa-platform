-- =================================================================
-- Migration: 022_production_hardening
-- Description: Queue-safe idempotency, race-condition constraints, and RAG indexes.
-- =================================================================
BEGIN;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Idempotency for deterministic booking execution/retry safety.
ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_bookings_tenant_idempotency
    ON bookings(tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
-- Config versions are created from MAX(version)+1, so enforce the invariant
-- and pair it with an advisory lock in application code.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tenant_config_version
    ON tenant_configs(tenant_id, version);
-- Close older duplicate active conversations before adding uniqueness guards.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY tenant_id, customer_id
               ORDER BY COALESCE(last_message_at, created_at) DESC, created_at DESC, id DESC
           ) AS rn
    FROM conversations
    WHERE status = 'active'
      AND COALESCE(channel_type, 'whatsapp') = 'whatsapp'
      AND channel_session_id IS NULL
)
UPDATE conversations c
SET status = 'closed',
    metadata = COALESCE(c.metadata, '{}'::jsonb) || jsonb_build_object(
        'closed_by_migration', '022_production_hardening',
        'closed_reason', 'duplicate_active_whatsapp_conversation'
    ),
    updated_at = NOW()
FROM ranked
WHERE c.id = ranked.id AND ranked.rn > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_whatsapp_conversation_per_customer
    ON conversations(tenant_id, customer_id)
    WHERE status = 'active'
      AND COALESCE(channel_type, 'whatsapp') = 'whatsapp'
      AND channel_session_id IS NULL;
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY tenant_id, channel_type, channel_session_id
               ORDER BY COALESCE(last_message_at, created_at) DESC, created_at DESC, id DESC
           ) AS rn
    FROM conversations
    WHERE status = 'active'
      AND channel_session_id IS NOT NULL
)
UPDATE conversations c
SET status = 'closed',
    metadata = COALESCE(c.metadata, '{}'::jsonb) || jsonb_build_object(
        'closed_by_migration', '022_production_hardening',
        'closed_reason', 'duplicate_active_channel_session'
    ),
    updated_at = NOW()
FROM ranked
WHERE c.id = ranked.id AND ranked.rn > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_channel_session_conversation
    ON conversations(tenant_id, channel_type, channel_session_id)
    WHERE status = 'active' AND channel_session_id IS NOT NULL;
-- RAG hardening: do not accept empty knowledge chunks, and make both lexical
-- fallback and vector search index-backed.
ALTER TABLE tenant_knowledge_base
    DROP CONSTRAINT IF EXISTS tenant_knowledge_tenant_not_null,
    DROP CONSTRAINT IF EXISTS tenant_knowledge_title_nonempty,
    DROP CONSTRAINT IF EXISTS tenant_knowledge_chunk_min_length;
ALTER TABLE tenant_knowledge_base
    ADD CONSTRAINT tenant_knowledge_tenant_not_null
        CHECK (tenant_id IS NOT NULL) NOT VALID,
    ADD CONSTRAINT tenant_knowledge_title_nonempty
        CHECK (length(btrim(document_title)) > 0) NOT VALID,
    ADD CONSTRAINT tenant_knowledge_chunk_min_length
        CHECK (length(btrim(chunk_text)) >= 10) NOT VALID;
UPDATE tenant_knowledge_base
SET embedding = NULL,
    metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
        'embedding_status', 'lexical_only',
        'embedding_cleared_by', '022_production_hardening'
    )
WHERE embedding IS NOT NULL
  AND vector_norm(embedding) = 0;
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
COMMIT;