DO $$
BEGIN
    RAISE EXCEPTION 'Deprecated split production schema. Use backend/db/sql/production/000_apply_canonical_schema.sql instead.';
END $$;
\quit 3
-- 001_enums.sql
-- Deprecated legacy split schema retained only as a historical reference.
BEGIN;
CREATE TYPE tenant_status AS ENUM ('trial', 'active', 'suspended', 'expired');
CREATE TYPE conversation_status AS ENUM ('active', 'closed', 'escalated');
CREATE TYPE message_direction AS ENUM ('inbound', 'outbound');
CREATE TYPE booking_status AS ENUM ('confirmed', 'cancelled', 'completed');
CREATE TYPE processing_status AS ENUM (
    'received',
    'queued',
    'processing',
    'completed',
    'failed',
    'dlq'
);
COMMIT;
