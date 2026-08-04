import { formatOffsetMinutes } from '../utils';

interface ReminderTimingDisplayProps {
  offsetMinutes: number;
  className?: string;
}

/**
 * Inline display of a reminder offset in human-readable terms.
 * e.g. 60 → "1 hour", 1440 → "1 day", 30 → "30 minutes"
 */
export function ReminderTimingDisplay({ offsetMinutes, className }: ReminderTimingDisplayProps) {
  return <span className={className}>{formatOffsetMinutes(offsetMinutes)}</span>;
}
