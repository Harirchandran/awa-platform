-- Migration 041: Drop Booking Lock
-- Description: Drop unique index uniq_bookings_slot to allow concurrent bookings across a tenant.

BEGIN;

DROP INDEX IF EXISTS uniq_bookings_slot;

COMMIT;
