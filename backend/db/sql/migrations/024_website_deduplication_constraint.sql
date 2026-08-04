-- =================================================================
-- Migration 024: Website Ingestion Duplicate Prevention
-- Adds a partial unique index on (tenant_id, URL, chunk_index)
-- to ensure no duplicate chunks for a given URL can exist.
-- =================================================================

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS tenant_knowledge_base_website_chunk_idx
ON tenant_knowledge_base (
    tenant_id,
    (metadata->>'url'),
    ((metadata->>'chunk_index')::integer)
)
WHERE (metadata->>'source' = 'website');

COMMIT;
