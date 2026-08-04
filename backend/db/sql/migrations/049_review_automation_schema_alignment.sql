-- Align persisted schema authority with completed-booking review automation.
-- This migration is data-preserving and safe to rerun.

ALTER TYPE booking_status ADD VALUE IF NOT EXISTS 'completed';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM review_requests
        WHERE source NOT IN (
            'manual', 'booking', 'order', 'reminder', 'automation',
            'booking_completed_automation'
        )
    ) THEN
        RAISE EXCEPTION 'review_requests contains a source outside the authoritative set';
    END IF;

    ALTER TABLE review_requests DROP CONSTRAINT IF EXISTS review_requests_source_check;
    ALTER TABLE review_requests ADD CONSTRAINT review_requests_source_check CHECK (
        source IN (
            'manual', 'booking', 'order', 'reminder', 'automation',
            'booking_completed_automation'
        )
    );
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM scheduled_messages
        WHERE trigger_type NOT IN (
            'manual', 'before_booking', 'after_booking',
            'before_payment_expiry', 'after_payment_failed',
            'after_order_completed', 'after_payment_completed',
            'inactive_conversation', 'custom', 'review_requested'
        )
    ) THEN
        RAISE EXCEPTION 'scheduled_messages contains a trigger outside the authoritative set';
    END IF;

    ALTER TABLE scheduled_messages DROP CONSTRAINT IF EXISTS scheduled_messages_trigger_check;
    ALTER TABLE scheduled_messages ADD CONSTRAINT scheduled_messages_trigger_check CHECK (
        trigger_type IN (
            'manual', 'before_booking', 'after_booking',
            'before_payment_expiry', 'after_payment_failed',
            'after_order_completed', 'after_payment_completed',
            'inactive_conversation', 'custom', 'review_requested'
        )
    );
END $$;
