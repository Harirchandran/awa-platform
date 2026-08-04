-- =================================================================
-- Migration: 043_reminders_upgrade
-- Description: Upgrades reminder rules and scheduled messages channel/trigger constraints.
-- =================================================================

BEGIN;

-- Migrate legacy channel values to 'manual'
UPDATE reminder_rules
SET channel = 'manual'
WHERE channel NOT IN ('whatsapp', 'email', 'manual');

UPDATE scheduled_messages
SET channel = 'manual'
WHERE channel NOT IN ('whatsapp', 'email', 'manual');

-- Drop old constraints on reminder_rules
ALTER TABLE reminder_rules DROP CONSTRAINT IF EXISTS reminder_rules_channel_check;
ALTER TABLE reminder_rules DROP CONSTRAINT IF EXISTS reminder_rules_trigger_check;

-- Drop old constraints on scheduled_messages
ALTER TABLE scheduled_messages DROP CONSTRAINT IF EXISTS scheduled_messages_channel_check;
ALTER TABLE scheduled_messages DROP CONSTRAINT IF EXISTS scheduled_messages_trigger_check;

-- Add updated constraints to reminder_rules
ALTER TABLE reminder_rules ADD CONSTRAINT reminder_rules_channel_check
    CHECK (channel IN ('whatsapp', 'email', 'manual'));

ALTER TABLE reminder_rules ADD CONSTRAINT reminder_rules_trigger_check
    CHECK (trigger_type IN (
        'manual',
        'before_booking',
        'after_booking',
        'before_payment_expiry',
        'after_payment_failed',
        'after_order_completed',
        'after_payment_completed',
        'inactive_conversation',
        'custom'
    ));

-- Add updated constraints to scheduled_messages
ALTER TABLE scheduled_messages ADD CONSTRAINT scheduled_messages_channel_check
    CHECK (channel IN ('whatsapp', 'email', 'manual'));

ALTER TABLE scheduled_messages ADD CONSTRAINT scheduled_messages_trigger_check
    CHECK (trigger_type IN (
        'manual',
        'before_booking',
        'after_booking',
        'before_payment_expiry',
        'after_payment_failed',
        'after_order_completed',
        'after_payment_completed',
        'inactive_conversation',
        'custom'
    ));

COMMIT;
