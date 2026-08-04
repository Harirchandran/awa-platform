-- =================================================================
-- Migration 004: Data Retention Policy
-- DPDP §8(7) / GDPR Art. 5(1)(e): Storage limitation principle
-- =================================================================
-- Data should not be kept longer than necessary for the purpose
-- for which it was collected.
-- =================================================================
BEGIN;
-- ============================================================
-- 1. Retention Policy Config Table
-- ============================================================
-- Allows per-tenant configurable retention periods.
CREATE TABLE IF NOT EXISTS retention_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL,      -- 'messages', 'conversations', 'sessions', 'audit_logs'
    retention_days INT NOT NULL,       -- number of days to keep data
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, resource_type)
);
COMMENT ON TABLE retention_policies IS 'DPDP §8(7): Per-tenant data retention periods';
-- ============================================================
-- 2. Default Retention Policies (Global)
-- ============================================================
-- Insert global defaults (NULL tenant_id = global fallback).
-- Tenants can override with their own entries.
-- Messages: 180 days (6 months)
-- Conversations: 365 days (1 year) after last activity
-- Sessions: 30 days after expiry
-- Audit logs: 730 days (2 years) — compliance requirement
-- ============================================================
-- 3. Purge Function: Messages
-- ============================================================
CREATE OR REPLACE FUNCTION purge_old_messages(retention_days INT DEFAULT 180)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM messages
    WHERE created_at < NOW() - (retention_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    -- Log the purge action
    INSERT INTO audit_logs (id, tenant_id, action, details, created_at)
    SELECT gen_random_uuid(),
           '00000000-0000-0000-0000-000000000000'::UUID,
           'data_retention_purge',
           jsonb_build_object(
               'resource', 'messages',
               'deleted_count', deleted_count,
               'retention_days', retention_days,
               'purged_at', NOW()
           ),
           NOW()
    WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'audit_logs');
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
-- ============================================================
-- 4. Purge Function: Inactive Conversations
-- ============================================================
CREATE OR REPLACE FUNCTION close_stale_conversations(inactive_days INT DEFAULT 30)
RETURNS INT AS $$
DECLARE
    closed_count INT;
BEGIN
    UPDATE conversations
    SET status = 'closed'
    WHERE status = 'active'
      AND last_message_at < NOW() - (inactive_days || ' days')::INTERVAL;
    GET DIAGNOSTICS closed_count = ROW_COUNT;
    RETURN closed_count;
END;
$$ LANGUAGE plpgsql;
-- ============================================================
-- 5. Purge Function: Expired Sessions
-- ============================================================
CREATE OR REPLACE FUNCTION purge_expired_sessions(grace_days INT DEFAULT 7)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM conversation_sessions
    WHERE expires_at IS NOT NULL
      AND expires_at < NOW() - (grace_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
COMMENT ON FUNCTION purge_old_messages IS 'DPDP §8(7): Purge messages older than retention period';
COMMENT ON FUNCTION close_stale_conversations IS 'Auto-close conversations with no activity';
COMMENT ON FUNCTION purge_expired_sessions IS 'Remove expired session data';
COMMIT;