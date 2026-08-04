-- Migration 008: DPDP/GDPR Data Retention Purge Policy
-- This function is executed by the cron-job defined in backend/ops/data_purge.py
-- Requires Super Admin or authorized scheduler role.
CREATE OR REPLACE FUNCTION purge_old_audit_logs()
RETURNS void AS $$
BEGIN
    -- Delete all audit logs strictly older than 90 days.
    -- Future iterations may allow parameterizing the interval per-tenant if
    -- specific SLAs state varying legal retention requirements.
    DELETE FROM audit_logs
    WHERE created_at < NOW() - INTERVAL '90 days';
    -- We can expand this function in the future to similarly prune raw WhatsApp
    -- messages after 1 year, etc., ensuring strict platform data minimization.
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;