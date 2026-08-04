import type { ReactNode } from 'react';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import type { Booking } from '../api';
import { formatBookingDate, formatBookingTime, formatCollectedValue } from '../utils';
import { BookingStatusBadge } from './BookingStatusBadge';
import type { PendingBookingStatusUpdate } from './BookingActionDialog';

interface BookingDetailSheetProps {
  booking: Booking | null;
  onOpenChange: (open: boolean) => void;
  onPendingStatusUpdate: (update: PendingBookingStatusUpdate) => void;
  isUpdating?: boolean;
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b py-2 last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[65%] text-right font-medium">{value}</span>
    </div>
  );
}

export function BookingDetailSheet({
  booking,
  onOpenChange,
  onPendingStatusUpdate,
  isUpdating = false,
}: BookingDetailSheetProps) {
  return (
    <Sheet open={!!booking} onOpenChange={onOpenChange}>
      <SheetContent className="overflow-y-auto sm:max-w-md">
        {booking && (
          <>
            <SheetHeader className="mb-6 border-b pb-4 text-left">
              <SheetTitle>Booking Details</SheetTitle>
              <SheetDescription>
                {booking.customer_name || 'Unknown customer'} - {formatBookingDate(booking, 'PPP')} at {formatBookingTime(booking, 'p')}
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-6">
              <section aria-labelledby="booking-customer-heading">
                <h4 id="booking-customer-heading" className="mb-2 text-sm font-semibold">Customer</h4>
                <div className="rounded-md bg-muted p-3 text-sm">
                  <DetailRow label="Name" value={booking.customer_name || 'Unknown customer'} />
                  <DetailRow label="Phone" value={booking.customer_phone || 'Not saved'} />
                  <DetailRow label="Customer ID" value={booking.customer_id ? `...${booking.customer_id.slice(-8)}` : 'Not saved'} />
                </div>
              </section>

              <section aria-labelledby="booking-appointment-heading">
                <h4 id="booking-appointment-heading" className="mb-2 text-sm font-semibold">Appointment</h4>
                <div className="rounded-md border p-3 text-sm">
                  <DetailRow label="Service" value={<span className="capitalize">{booking.service_type || 'Not specified'}</span>} />
                  <DetailRow label="Date" value={formatBookingDate(booking, 'PPP')} />
                  <DetailRow label="Time" value={formatBookingTime(booking, 'p')} />
                  <DetailRow label="Status" value={<BookingStatusBadge status={booking.status} />} />
                </div>
              </section>

              <section aria-labelledby="booking-answers-heading">
                <h4 id="booking-answers-heading" className="mb-2 text-sm font-semibold">Collected Answers</h4>
                <div className="space-y-2">
                  {booking.collected_fields && Object.keys(booking.collected_fields).length > 0 ? (
                    Object.entries(booking.collected_fields).map(([key, value]) => (
                      <div key={key} className="rounded-md bg-muted p-3 text-sm">
                        <p className="font-medium capitalize text-muted-foreground">{key.replace(/_/g, ' ')}</p>
                        <p className="mt-1 break-words text-foreground">{formatCollectedValue(value)}</p>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">No custom fields collected.</p>
                  )}
                </div>
              </section>

              <section className="space-y-3 border-t pt-5" aria-labelledby="booking-actions-heading">
                <h4 id="booking-actions-heading" className="text-sm font-semibold">Quick Actions</h4>
                {booking.status !== 'cancelled' && (
                  <Button
                    type="button"
                    variant="destructive"
                    className="w-full"
                    onClick={() => onPendingStatusUpdate({
                      id: booking.id,
                      status: 'cancelled',
                      label: 'Cancel Appointment',
                    })}
                    disabled={isUpdating}
                  >
                    Cancel Appointment
                  </Button>
                )}
                {booking.status !== 'confirmed' && (
                  <Button
                    type="button"
                    variant="default"
                    className="w-full"
                    onClick={() => onPendingStatusUpdate({
                      id: booking.id,
                      status: 'confirmed',
                      label: 'Manually Confirm',
                    })}
                    disabled={isUpdating}
                  >
                    Manually Confirm
                  </Button>
                )}
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
