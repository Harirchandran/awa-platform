-- Migration: 040_event_recovery
-- Description: Adds locked_at column to business_events table to support claiming orphaned events.

BEGIN;

ALTER TABLE business_events
ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;

COMMIT;
