import type { ReminderTrigger, ReminderChannel, ScheduledMessageStatus } from './api';

export const STATUS_COPY: Record<ScheduledMessageStatus, string> = {
  scheduled: 'Scheduled',
  due: 'Due',
  snoozed: 'Snoozed',
  sent: 'Sent',
  failed: 'Failed',
  cancelled: 'Cancelled',
  skipped: 'Skipped',
};

export const TRIGGER_COPY: Record<ReminderTrigger, string> = {
  manual: 'Manual',
  before_booking: 'Before booking',
  after_booking: 'After booking',
  before_payment_expiry: 'Before payment expiry',
  after_payment_failed: 'After payment failed',
  after_order_completed: 'After order completed',
  inactive_conversation: 'Inactive conversation',
  custom: 'Custom',
};

export const CHANNEL_COPY: Record<ReminderChannel, string> = {
  whatsapp: 'WhatsApp',
  email: 'Email',
  manual: 'Manual',
};
