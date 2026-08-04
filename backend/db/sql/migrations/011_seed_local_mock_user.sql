-- =================================================================
-- Migration: 011_seed_local_mock_user
-- Description: Keeps Docker mock_token auth aligned with local seed data.
-- =================================================================
BEGIN;
INSERT INTO users (id, phone_number, email, name)
VALUES (
    '00000000-0000-0000-0000-000000000099',
    '+15550000099',
    'admin@docker.local',
    'Docker Admin'
) ON CONFLICT (id) DO NOTHING;
INSERT INTO user_tenants (id, user_id, tenant_id, role)
VALUES (
    '00000000-0000-0000-0000-000000000098',
    '00000000-0000-0000-0000-000000000099',
    '00000000-0000-0000-0000-000000000001',
    'super_admin'
) ON CONFLICT (user_id, tenant_id) DO NOTHING;
COMMIT;