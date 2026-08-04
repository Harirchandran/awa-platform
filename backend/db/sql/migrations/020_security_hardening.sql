-- =================================================================
-- Migration: 020_security_hardening
-- Description: Tightens RLS, roles, retention purge, and local mock auth.
-- =================================================================
BEGIN;
-- Local Docker mock auth user. Bare mock_token maps to this tenant admin,
-- not to the super admin seed.
INSERT INTO users (id, phone_number, email, name)
VALUES (
    '00000000-0000-0000-0000-000000000100',
    '+15550000100',
    'tenant-admin@docker.local',
    'Docker Tenant Admin'
) ON CONFLICT (id) DO NOTHING;
INSERT INTO user_tenants (id, user_id, tenant_id, role)
VALUES (
    '00000000-0000-0000-0000-000000000101',
    '00000000-0000-0000-0000-000000000100',
    '00000000-0000-0000-0000-000000000001',
    'tenant_admin'
) ON CONFLICT (user_id, tenant_id) DO UPDATE SET role = EXCLUDED.role;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM users
        WHERE email IS NOT NULL AND deleted_at IS NULL
        GROUP BY lower(email)
        HAVING COUNT(*) > 1
    ) THEN
        RAISE NOTICE 'Duplicate active user emails exist; clean duplicates before adding idx_users_email_lower_unique.';
        CREATE INDEX IF NOT EXISTS idx_users_email_lower_lookup
            ON users (lower(email))
            WHERE email IS NOT NULL AND deleted_at IS NULL;
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower_unique
            ON users (lower(email))
            WHERE email IS NOT NULL AND deleted_at IS NULL;
    END IF;
END;
$$;
ALTER TABLE user_tenants
    DROP CONSTRAINT IF EXISTS chk_user_tenants_role;
ALTER TABLE user_tenants
    ADD CONSTRAINT chk_user_tenants_role
    CHECK (role IN ('member', 'operator', 'tenant_admin', 'admin', 'owner', 'super_admin'));
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM whatsapp_accounts
        WHERE phone_number_id IS NOT NULL
        GROUP BY phone_number_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate WhatsApp phone_number_id values exist; clean duplicates before adding uniq_wa_phone_number_id.';
    END IF;
END;
$$;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_wa_phone_number_id
    ON whatsapp_accounts (phone_number_id)
    WHERE phone_number_id IS NOT NULL;
CREATE OR REPLACE FUNCTION auth_has_tenant_access(target_tenant_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
        FROM user_tenants ut
        JOIN users u ON u.id = ut.user_id
        WHERE ut.user_id = auth.uid()
          AND ut.tenant_id = target_tenant_id
          AND u.deleted_at IS NULL
          AND ut.role IN ('member', 'operator', 'tenant_admin', 'admin', 'owner', 'super_admin')
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;
CREATE OR REPLACE FUNCTION auth_has_tenant_write_access(target_tenant_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
        FROM user_tenants ut
        JOIN users u ON u.id = ut.user_id
        WHERE ut.user_id = auth.uid()
          AND ut.tenant_id = target_tenant_id
          AND u.deleted_at IS NULL
          AND ut.role IN ('tenant_admin', 'admin', 'owner', 'super_admin')
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_tenants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS users_self_select ON users;
CREATE POLICY users_self_select ON users
    FOR SELECT USING (id = auth.uid());
DROP POLICY IF EXISTS user_tenants_self_select ON user_tenants;
CREATE POLICY user_tenants_self_select ON user_tenants
    FOR SELECT USING (user_id = auth.uid());
-- Replace broad tenant policies with DB-membership backed policies.
DROP POLICY IF EXISTS tenant_isolation ON tenants;
CREATE POLICY tenant_isolation ON tenants
    FOR SELECT USING (auth_has_tenant_access(id));
DROP POLICY IF EXISTS tenant_isolation ON whatsapp_accounts;
CREATE POLICY tenant_isolation ON whatsapp_accounts
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON branches;
CREATE POLICY tenant_isolation ON branches
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON tenant_configs;
CREATE POLICY tenant_isolation ON tenant_configs
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON tenant_feature_flags;
CREATE POLICY tenant_isolation ON tenant_feature_flags
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON tenant_cost_daily;
CREATE POLICY tenant_isolation ON tenant_cost_daily
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON customers;
CREATE POLICY tenant_isolation ON customers
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON conversations;
CREATE POLICY tenant_isolation ON conversations
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON messages;
CREATE POLICY tenant_isolation ON messages
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON conversation_sessions;
CREATE POLICY tenant_isolation ON conversation_sessions
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON bookings;
CREATE POLICY tenant_isolation ON bookings
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON orders;
CREATE POLICY tenant_isolation ON orders
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON order_items;
CREATE POLICY tenant_isolation ON order_items
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON order_events;
CREATE POLICY tenant_isolation ON order_events
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON audit_logs;
CREATE POLICY tenant_isolation ON audit_logs
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON retention_policies;
CREATE POLICY tenant_isolation ON retention_policies
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON tenant_knowledge_base;
CREATE POLICY tenant_isolation ON tenant_knowledge_base
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON public_chat_links;
CREATE POLICY tenant_isolation ON public_chat_links
    FOR ALL USING (auth_has_tenant_access(tenant_id))
    WITH CHECK (auth_has_tenant_write_access(tenant_id));
DROP POLICY IF EXISTS tenant_isolation ON public_chat_sessions;
CREATE POLICY tenant_isolation ON public_chat_sessions
    FOR SELECT USING (auth_has_tenant_access(tenant_id));
-- Tenant users can manage only tenant-owned prompts through direct client RLS.
-- Platform-global prompts (tenant_id IS NULL) remain service-role/backend only.
DROP POLICY IF EXISTS tenant_isolation ON prompt_registry;
DROP POLICY IF EXISTS prompt_registry_tenant_select ON prompt_registry;
DROP POLICY IF EXISTS prompt_registry_tenant_insert ON prompt_registry;
DROP POLICY IF EXISTS prompt_registry_tenant_update ON prompt_registry;
DROP POLICY IF EXISTS prompt_registry_tenant_delete ON prompt_registry;
CREATE POLICY prompt_registry_tenant_select ON prompt_registry
    FOR SELECT USING (tenant_id IS NOT NULL AND auth_has_tenant_access(tenant_id));
CREATE POLICY prompt_registry_tenant_insert ON prompt_registry
    FOR INSERT WITH CHECK (tenant_id IS NOT NULL AND auth_has_tenant_write_access(tenant_id));
CREATE POLICY prompt_registry_tenant_update ON prompt_registry
    FOR UPDATE USING (tenant_id IS NOT NULL AND auth_has_tenant_write_access(tenant_id))
    WITH CHECK (tenant_id IS NOT NULL AND auth_has_tenant_write_access(tenant_id));
CREATE POLICY prompt_registry_tenant_delete ON prompt_registry
    FOR DELETE USING (tenant_id IS NOT NULL AND auth_has_tenant_write_access(tenant_id));
CREATE OR REPLACE FUNCTION purge_old_audit_logs(retention_days INT DEFAULT 365)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM audit_logs
    WHERE created_at < NOW() - make_interval(days => retention_days);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
COMMIT;