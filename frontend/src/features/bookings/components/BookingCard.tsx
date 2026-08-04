import type { Booking } from '../api';
import { formatBookingDate, formatBookingTime } from '../utils';
import { BookingStatusBadge } from './BookingStatusBadge';

interface BookingCardProps {
  booking: Booking;
  onSelect: (booking: Booking) => void;
}

export function BookingCard({ booking, onSelect }: BookingCardProps) {
  const customerName = booking.customer_name || 'Unknown customer';

  return (
    <button
      type="button"
      onClick={() => onSelect(booking)}
      className="w-full rounded-md border bg-card p-4 text-left transition-all duration-200 hover:-translate-y-[2px] hover:shadow-md hover:border-primary/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      aria-label={`Open details for ${customerName} booking`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[14px] font-semibold">{customerName}</p>
          <p className="mt-0.5 text-[12px] text-muted-foreground">{booking.customer_phone || 'Phone not saved'}</p>
        </div>
        <BookingStatusBadge status={booking.status} />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-[12px]">
        <div>
          <p className="text-muted-foreground">Service</p>
          <p className="mt-1 truncate font-medium capitalize text-foreground">{booking.service_type || 'General'}</p>
        </div>
        <div>
          <p className="text-muted-foreground">When</p>
          <p className="mt-1 font-medium text-foreground">{formatBookingDate(booking, 'MMM d')}</p>
          <p className="text-muted-foreground">{formatBookingTime(booking)}</p>
        </div>
      </div>
      <p className="mt-4 text-[12px] font-medium text-primary">View booking details</p>
    </button>
  );
}
