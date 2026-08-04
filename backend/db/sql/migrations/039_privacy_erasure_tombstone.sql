-- =================================================================
-- Migration: 039_privacy_erasure_tombstone
-- Description: Phase 4 — 72-hour asynchronous tombstone erasure.
--   Adds `status` (TEXT) and `erasure_scheduled_at` (TIMESTAMPTZ)
--   columns to the customers table to support the pending_erasure
--   workflow driven by the privacy_reaper worker.
-- =================================================================

BEGIN;

-- 1. Add status column (plain TEXT, consistent with consent_status pattern).
--    Default 'active' preserves all existing rows transparently.
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

-- 2. Add erasure_scheduled_at column for the 72-hour tombstone timestamp.
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS erasure_scheduled_at TIMESTAMPTZ;

-- 3. Add a partial index so the reaper's query
--    (WHERE status = 'pending_erasure' AND erasure_scheduled_at <= NOW())
--    runs in O(pending) time, not O(all customers).
CREATE INDEX IF NOT EXISTS idx_customers_pending_erasure
    ON customers (erasure_scheduled_at)
    WHERE status = 'pending_erasure';

-- 4. Add a CHECK constraint so only known values can be stored.
--    Done as a named constraint so it can be altered safely later.
ALTER TABLE customers
    DROP CONSTRAINT IF EXISTS chk_customers_status;

ALTER TABLE customers
    ADD CONSTRAINT chk_customers_status
    CHECK (status IN ('active', 'inactive', 'suspended', 'pending_erasure'));

-- 5. Comment the new columns for schema documentation.
COMMENT ON COLUMN customers.status IS
    'Customer lifecycle status: active | inactive | suspended | pending_erasure';
COMMENT ON COLUMN customers.erasure_scheduled_at IS
    'DPDP §12 / GDPR Art.17: Timestamp at which the 72-hour tombstone window expires '
    'and the privacy_reaper will execute the hard-delete cascade.';

COMMIT;
