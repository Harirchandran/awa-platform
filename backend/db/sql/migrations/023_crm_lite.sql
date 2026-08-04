-- =================================================================
-- Migration: 023_crm_lite
-- Description: Adds CRM Lite tags and notes for tenant customer profiles.
-- =================================================================
BEGIN;
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
ALTER TABLE customer_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_tag_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_notes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON customer_tags;
CREATE POLICY tenant_isolation ON customer_tags
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON customer_tag_links;
CREATE POLICY tenant_isolation ON customer_tag_links
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON customer_notes;
CREATE POLICY tenant_isolation ON customer_notes
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
COMMIT;