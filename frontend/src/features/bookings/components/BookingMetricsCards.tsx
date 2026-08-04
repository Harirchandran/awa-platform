import type { Booking } from '../api';
import { getBookingMetrics } from '../utils';
import { CalendarCheck, CheckCircle2, ClipboardList, XCircle } from 'lucide-react';

interface BookingMetricsCardsProps {
  bookings: Booking[];
}

export function BookingMetricsCards({ bookings }: BookingMetricsCardsProps) {
  const metrics = getBookingMetrics(bookings);
  const cards = [
    { label: 'Loaded bookings', value: metrics.loaded, helper: 'From the current loaded pages', icon: ClipboardList },
    { label: 'Confirmed', value: metrics.confirmed, helper: 'Ready or already accepted', icon: CheckCircle2 },
    { label: "Today's bookings", value: metrics.today, helper: 'Based on saved booking date', icon: CalendarCheck },
    { label: 'Cancelled', value: metrics.cancelled, helper: 'Cancelled appointment records', icon: XCircle },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Booking overview counts">
      {cards.map((card) => {
        const Icon = card.icon;
        return (
        <div key={card.label} className="motion-soft rounded-md border bg-card p-4">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[12px] font-medium text-muted-foreground">{card.label}</p>
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-emerald-500/10">
              <Icon className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            </div>
          </div>
          <p className="mt-2 text-[24px] font-semibold leading-none text-foreground">{card.value}</p>
          <p className="mt-2 text-[12px] leading-normal text-muted-foreground">{card.helper}</p>
        </div>
        );
      })}
    </div>
  );
}
