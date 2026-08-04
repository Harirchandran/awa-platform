import type { Booking } from '../api';
import { ResponsiveDataList } from '@/components/shared/ResponsiveDataList';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { TableSkeleton } from '@/components/ui/loading-states';
import { Loader2 } from 'lucide-react';
import { formatBookingDate, formatBookingTime } from '../utils';
import { BookingStatusBadge } from './BookingStatusBadge';
import { BookingCard } from './BookingCard';
import { BookingEmptyState } from './BookingEmptyState';
import { BookingErrorState } from './BookingErrorState';

import { cn } from '@/lib/utils';

interface BookingListProps {
  bookings: Booking[];
  isLoading: boolean;
  isFetching?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  hasNextPage?: boolean;
  fetchNextPage: () => void;
  isFetchingNextPage: boolean;
  onSelectBooking: (booking: Booking) => void;
}

export function BookingList({
  bookings,
  isLoading,
  isFetching = false,
  isError = false,
  onRetry,
  hasNextPage,
  fetchNextPage,
  isFetchingNextPage,
  onSelectBooking,
}: BookingListProps) {
  if (isLoading) {
    return <TableSkeleton columns={5} rows={5} />;
  }

  if (isError) {
    return <BookingErrorState onRetry={onRetry} />;
  }

  if (!bookings || bookings.length === 0) {
    return <BookingEmptyState />;
  }


  const desktop = (
    <div className="overflow-hidden rounded-md border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Customer</TableHead>
            <TableHead>Service</TableHead>
            <TableHead>Date / Time</TableHead>
            <TableHead>Contact</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {bookings.map((booking) => (
            <TableRow
              key={booking.id}
              className="cursor-pointer hover:bg-muted/50 transition-colors"
              onClick={() => onSelectBooking(booking)}
            >
              <TableCell>
                <div className="font-medium text-left">
                  {booking.customer_name || 'Unknown customer'}
                </div>
                <div className="text-xs text-muted-foreground">Booking {booking.id.split('-')[0]}</div>
              </TableCell>
              <TableCell className="capitalize">{booking.service_type || 'General'}</TableCell>
              <TableCell>
                <div className="font-medium">{formatBookingDate(booking, 'MMM d, yyyy')}</div>
                <div className="text-xs text-muted-foreground">{formatBookingTime(booking)}</div>
              </TableCell>
              <TableCell>{booking.customer_phone || <span className="text-muted-foreground">Not saved</span>}</TableCell>
              <TableCell>
                <BookingStatusBadge status={booking.status} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );

  const mobile = (
    <div className="space-y-3">
      {bookings.map((booking) => (
        <BookingCard key={booking.id} booking={booking} onSelect={onSelectBooking} />
      ))}
    </div>
  );

  return (
    <div className={cn("space-y-4 transition-opacity duration-200", isFetching && !isLoading && "opacity-50 pointer-events-none")}>
      <ResponsiveDataList desktop={desktop} mobile={mobile} />
      {hasNextPage && (
        <div className="flex justify-center pt-1">
          <Button type="button" variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
            {isFetchingNextPage && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            {isFetchingNextPage ? 'Loading more' : 'Load More Bookings'}
          </Button>
        </div>
      )}
    </div>
  );
}
