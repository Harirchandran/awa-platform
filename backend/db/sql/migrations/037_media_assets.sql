-- =================================================================
-- Migration: 037_media_assets
-- Description: Tenant-owned media metadata for catalog/service images.
-- =================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS media_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id UUID NOT NULL,
    storage_key TEXT NOT NULL,
    served_url TEXT,
    thumbnail_url TEXT,
    original_filename_sanitized TEXT,
    mime_type TEXT NOT NULL,
    width INT,
    height INT,
    size_bytes BIGINT NOT NULL,
    alt_text TEXT,
    caption TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    is_primary BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT media_assets_entity_type_check CHECK (entity_type IN ('catalog_item', 'service', 'category')),
    CONSTRAINT media_assets_status_check CHECK (status IN ('active', 'hidden', 'deleted', 'rejected')),
    CONSTRAINT media_assets_mime_type_check CHECK (mime_type IN ('image/jpeg', 'image/png', 'image/webp')),
    CONSTRAINT media_assets_size_positive CHECK (size_bytes > 0),
    CONSTRAINT media_assets_dimensions_positive CHECK (
        (width IS NULL OR width > 0) AND (height IS NULL OR height > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_media_assets_tenant_entity
    ON media_assets(tenant_id, entity_type, entity_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_media_assets_primary_active_entity
    ON media_assets(tenant_id, entity_type, entity_id)
    WHERE is_primary = TRUE AND status = 'active';

DROP TRIGGER IF EXISTS update_media_assets_updated_at ON media_assets;
CREATE TRIGGER update_media_assets_updated_at
    BEFORE UPDATE ON media_assets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE media_assets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON media_assets;
CREATE POLICY tenant_isolation ON media_assets
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));

COMMIT;
