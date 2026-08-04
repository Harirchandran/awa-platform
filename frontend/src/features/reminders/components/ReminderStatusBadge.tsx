import { StatusBadge } from '@/components/shared/StatusBadge';
import type { ScheduledMessageStatus } from '../api';
import { STATUS_COPY } from '../constants';

type StatusTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'muted';

function messageStatusTone(status: ScheduledMessageStatus): StatusTone {
  switch (status) {
    case 'sent': return 'success';
    case 'due': return 'warning';
    case 'failed': return 'danger';
    case 'snoozed':
    case 'scheduled': return 'info';
    case 'cancelled':
    case 'skipped': return 'muted';
    default: return 'neutral';
  }
}

/**
 * Badge for a scheduled message's delivery status (Scheduled, Due, Sent, Failed, etc.)
 */
export function ReminderStatusBadge({ status }: { status: ScheduledMessageStatus }) {
  return (
    <StatusBadge
      label={STATUS_COPY[status]}
      tone={messageStatusTone(status)}
      title={STATUS_COPY[status]}
    />
  );
}

/**
 * Badge for a reminder rule's enabled/disabled state.
 */
export function RuleStatusBadge({ isActive }: { isActive: boolean }) {
  return (
    <StatusBadge
      label={isActive ? 'Active' : 'Inactive'}
      tone={isActive ? 'success' : 'muted'}
      title={isActive ? 'This rule is active and will schedule messages automatically' : 'This rule is inactive and will not schedule new messages'}
    />
  );
}
