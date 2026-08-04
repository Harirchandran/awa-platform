/**
 * Converts an offset_minutes integer to a human-readable string.
 * e.g. 60 → "1 hour", 1440 → "1 day", 30 → "30 minutes"
 * Exported from a dedicated utils file to satisfy react-refresh/only-export-components.
 */
export function formatOffsetMinutes(minutes: number): string {
  if (minutes === 0) return '0 minutes';
  if (minutes % 1440 === 0) {
    const days = minutes / 1440;
    return `${days} day${days !== 1 ? 's' : ''}`;
  }
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return `${hours} hour${hours !== 1 ? 's' : ''}`;
  }
  return `${minutes} minute${minutes !== 1 ? 's' : ''}`;
}
