BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_booking_completed
ON business_events (tenant_id, event_type, aggregate_id)
WHERE event_type = 'booking.completed' AND aggregate_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_review_requested_causation
ON business_events (tenant_id, event_type, causation_id)
WHERE event_type = 'review.requested' AND causation_id IS NOT NULL;

COMMIT;
