-- =================================================================
-- Migration 030: Prompt Registry Active De-duplication
-- Ensures only one active prompt exists per scope/name, including
-- platform-global prompts where tenant_id is NULL.
-- =================================================================
BEGIN;
WITH ranked_active_prompts AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY tenant_id, prompt_name
            ORDER BY version DESC NULLS LAST, updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
        ) AS active_rank
    FROM prompt_registry
    WHERE is_active = TRUE
)
UPDATE prompt_registry pr
SET is_active = FALSE,
    updated_at = NOW()
FROM ranked_active_prompts ranked
WHERE pr.id = ranked.id
  AND ranked.active_rank > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_prompt_registry_active_global
    ON prompt_registry(prompt_name)
    WHERE tenant_id IS NULL AND is_active = TRUE;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_prompt_registry_active_tenant
    ON prompt_registry(tenant_id, prompt_name)
    WHERE tenant_id IS NOT NULL AND is_active = TRUE;
COMMIT;