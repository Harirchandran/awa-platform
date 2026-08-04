-- =================================================================
-- Migration: 018_escalation_workflow
-- Description: Durable escalation metadata for human handoff pause/resume.
-- =================================================================
BEGIN;
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS escalation_reason TEXT,
    ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS resume_mode TEXT;
UPDATE conversations
SET resume_mode = 'automatic'
WHERE resume_mode IS NULL;
ALTER TABLE conversations
    ALTER COLUMN resume_mode SET DEFAULT 'automatic',
    ALTER COLUMN resume_mode SET NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'conversations_assigned_to_fkey'
    ) THEN
        ALTER TABLE conversations
            ADD CONSTRAINT conversations_assigned_to_fkey
            FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE SET NULL;
    END IF;
END $$;
ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_resume_mode_check;
ALTER TABLE conversations
    ADD CONSTRAINT conversations_resume_mode_check
    CHECK (resume_mode IN ('automatic', 'customer', 'tenant', 'manual'));
CREATE INDEX IF NOT EXISTS idx_conversations_escalation_queue
    ON conversations(tenant_id, status, escalated_at DESC)
    WHERE status = 'escalated';
COMMIT;