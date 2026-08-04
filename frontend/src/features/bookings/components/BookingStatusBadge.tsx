import { StatusBadge } from '@/components/shared/StatusBadge';
import { formatBookingStatus } from '../utils';

interface BookingStatusBadgeProps {
  status: string;
}

export function BookingStatusBadge({ status }: BookingStatusBadgeProps) {
  const tone = (() => {
    switch (status) {
      case 'confirmed':
      case 'completed':
        return 'success';
      case 'pending':
      case 'awaiting_confirmation':
        return 'warning';
      case 'cancelled':
        return 'muted';
      case 'failed':
      case 'no_show':
        return 'danger';
      case 'rescheduled':
        return 'info';
      case 'draft':
      case 'proposed':
        return 'neutral';
      default:
        return 'neutral';
    }
  })();

  return (
    <StatusBadge
      label={formatBookingStatus(status)}
      tone={tone}
      srLabel={`Booking status: ${formatBookingStatus(status)}`}
    />
  );
}
