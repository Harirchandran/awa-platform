-- =================================================================
-- Migration: 033_tenant_invitations
-- Description: Adds tenant invitations table, indexes, and RLS policies.
-- =================================================================
BEGIN;
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
-- Case-insensitive partial unique index for active pending invites only (Correction 6)
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
-- RLS (Correction 7)
ALTER TABLE tenant_invitations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenant_invitations;
CREATE POLICY tenant_isolation ON tenant_invitations
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
COMMIT;