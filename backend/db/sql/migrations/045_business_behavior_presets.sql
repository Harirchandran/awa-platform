-- =================================================================
-- Migration: 045_business_behavior_presets
-- Description: Versioned super-admin business behavior preset storage.
-- =================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS business_behavior_presets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_type_key TEXT NOT NULL REFERENCES business_types(code) ON UPDATE CASCADE ON DELETE RESTRICT,
    version INTEGER NOT NULL,
    profile_json JSONB NOT NULL,
    tenant_editable_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ NULL,
    deactivated_at TIMESTAMPTZ NULL,
    change_note TEXT NULL,
    CONSTRAINT business_behavior_presets_version_positive CHECK (version > 0),
    CONSTRAINT business_behavior_presets_key_format CHECK (
        business_type_key = lower(business_type_key)
        AND length(business_type_key) BETWEEN 1 AND 64
        AND business_type_key ~ '^[a-z0-9][a-z0-9_-]{0,63}$'
    ),
    CONSTRAINT business_behavior_presets_status_check CHECK (status IN ('draft', 'active', 'inactive', 'deprecated')),
    CONSTRAINT business_behavior_presets_active_status_check CHECK (
        (is_active = TRUE AND status = 'active')
        OR (is_active = FALSE AND status IN ('draft', 'inactive', 'deprecated'))
    ),
    CONSTRAINT business_behavior_presets_profile_object_check CHECK (jsonb_typeof(profile_json) = 'object'),
    CONSTRAINT business_behavior_presets_editable_array_check CHECK (jsonb_typeof(tenant_editable_fields) = 'array'),
    CONSTRAINT business_behavior_presets_unique_version UNIQUE (business_type_key, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_business_behavior_presets_active
    ON business_behavior_presets(business_type_key)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_business_behavior_presets_type_version
    ON business_behavior_presets(business_type_key, version DESC);

DROP TRIGGER IF EXISTS update_business_behavior_presets_updated_at ON business_behavior_presets;
CREATE TRIGGER update_business_behavior_presets_updated_at
    BEFORE UPDATE ON business_behavior_presets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE business_behavior_presets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS super_admin_read_business_behavior_presets ON business_behavior_presets;
CREATE POLICY super_admin_read_business_behavior_presets
    ON business_behavior_presets
    FOR SELECT USING (TRUE);

COMMENT ON TABLE business_behavior_presets IS 'Versioned super-admin-owned presentation behavior presets. Not runtime execution authority.';
COMMENT ON COLUMN business_behavior_presets.profile_json IS 'Strict BehaviorProfileDefinition JSON. Must not contain prompts, provider/payment/security/OTP/confirmation/tool fields.';
COMMENT ON COLUMN business_behavior_presets.tenant_editable_fields IS 'Super-admin-owned allowlist copied from profile_json. Tenant overrides cannot edit this list.';

COMMIT;
