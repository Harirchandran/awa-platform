BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_conversation_turn_message
ON business_events (
    tenant_id,
    event_type,
    aggregate_id
)
WHERE event_type = 'conversation.turn.received'
  AND aggregate_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_awa_processing_recovery
ON messages (
    tenant_id,
    processing_status,
    created_at
)
WHERE channel_type = 'awa_chat'
  AND direction = 'inbound';

COMMIT;
