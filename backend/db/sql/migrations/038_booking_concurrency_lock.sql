-- Migration 038: Booking Concurrency Lock
CREATE UNIQUE INDEX IF NOT EXISTS uniq_bookings_slot
    ON bookings (tenant_id, booking_date, booking_time)
    WHERE (status = 'confirmed');
