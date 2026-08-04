-- =================================================================
-- Migration: 021_conversation_sequence_integrity
-- Description: Ensures conversation sequence numbers exist and stay non-negative.
-- =================================================================
BEGIN;
ALTER TABLE conversation_sessions
    ADD COLUMN IF NOT EXISTS sequence_number INT NOT NULL DEFAULT 0;
ALTER TABLE conversation_sessions
    DROP CONSTRAINT IF EXISTS chk_conversation_sessions_sequence_number;
ALTER TABLE conversation_sessions
    ADD CONSTRAINT chk_conversation_sessions_sequence_number
    CHECK (sequence_number >= 0);
COMMIT;