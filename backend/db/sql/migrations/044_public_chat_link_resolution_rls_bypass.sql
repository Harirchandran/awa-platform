-- =================================================================
-- Migration: 044_public_chat_link_resolution_rls_bypass
-- Description: Creates a SECURITY DEFINER function to allow RLS bypass
--              specifically for resolving public chat links by slug,
--              where tenant ID is initially unknown.
-- =================================================================

BEGIN;

CREATE OR REPLACE FUNCTION get_public_chat_link_by_slug_v1(p_slug TEXT)
RETURNS TABLE (
    id UUID,
    tenant_id UUID,
    slug TEXT,
    display_name TEXT,
    token_hash TEXT,
    metadata JSONB,
    status TEXT,
    scan_count INT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    disabled_at TIMESTAMPTZ,
    last_opened_at TIMESTAMPTZ,
    tenant_name TEXT,
    tenant_status TEXT,
    ai_mode TEXT,
    plan_code TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT pcl.id, pcl.tenant_id, pcl.slug, pcl.display_name, pcl.token_hash,
           pcl.metadata, pcl.status,
           pcl.scan_count, pcl.created_at, pcl.updated_at, pcl.disabled_at,
           pcl.last_opened_at, t.name AS tenant_name, t.status::text AS tenant_status,
           t.ai_mode, t.plan_code
    FROM public_chat_links pcl
    JOIN tenants t ON t.id = pcl.tenant_id
    WHERE pcl.slug = p_slug AND t.deleted_at IS NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMIT;
