import { useState } from 'react';
import { toast } from 'react-hot-toast';
import { useUpdateBooking } from '../api';
import type { Booking } from '../api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { BookingList } from './BookingList';
import { BookingDetailSheet } from './BookingDetailSheet';
import { BookingActionDialog } from './BookingActionDialog';
import type { PendingBookingStatusUpdate } from './BookingActionDialog';

interface BookingsTableProps {
  bookings: Booking[];
  isLoading: boolean;
  isFetching?: boolean;
  isError?: boolean;
  refetch?: () => void;
  hasNextPage?: boolean;
  fetchNextPage: () => void;
  isFetchingNextPage: boolean;
}

export function BookingsTable({
  bookings,
  isLoading,
  isFetching = false,
  isError = false,
  refetch,
  hasNextPage,
  fetchNextPage,
  isFetchingNextPage,
}: BookingsTableProps) {
  const [selectedBooking, setSelectedBooking] = useState<Booking | null>(null);
  const [pendingStatusUpdate, setPendingStatusUpdate] = useState<PendingBookingStatusUpdate | null>(null);
  const updateMutation = useUpdateBooking();

  const handleStatusUpdate = (pendingUpdate: PendingBookingStatusUpdate) => {
    updateMutation.mutate(
      { id: pendingUpdate.id, updates: { status: pendingUpdate.status } },
      {
        onSuccess: () => {
          toast.success(`Booking marked as ${pendingUpdate.status}`);
          setSelectedBooking(null);
          setPendingStatusUpdate(null);
        },
        onError: (error: unknown) => {
          toast.error(getApiErrorMessage(error, 'Failed to update booking status'));
        },
      },
    );
  };

  return (
    <>
      <BookingList
        bookings={bookings}
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        onRetry={refetch}
        hasNextPage={hasNextPage}
        fetchNextPage={fetchNextPage}
        isFetchingNextPage={isFetchingNextPage}
        onSelectBooking={setSelectedBooking}
      />
      <BookingDetailSheet
        booking={selectedBooking}
        onOpenChange={(open) => !open && setSelectedBooking(null)}
        onPendingStatusUpdate={setPendingStatusUpdate}
        isUpdating={updateMutation.isPending}
      />
      <BookingActionDialog
        pendingUpdate={pendingStatusUpdate}
        onOpenChange={(open) => !open && setPendingStatusUpdate(null)}
        onConfirm={() => {
          if (pendingStatusUpdate) {
            handleStatusUpdate(pendingStatusUpdate);
          }
        }}
        isLoading={updateMutation.isPending}
      />
    </>
  );
}
