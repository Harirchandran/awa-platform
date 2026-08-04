import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';

export interface PendingBookingStatusUpdate {
  id: string;
  status: string;
  label: string;
}

interface BookingActionDialogProps {
  pendingUpdate: PendingBookingStatusUpdate | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isLoading?: boolean;
}

export function BookingActionDialog({
  pendingUpdate,
  onOpenChange,
  onConfirm,
  isLoading = false,
}: BookingActionDialogProps) {
  return (
    <ConfirmActionDialog
      open={!!pendingUpdate}
      onOpenChange={onOpenChange}
      title="Confirm booking update"
      description="This immediately changes the booking record and may affect staff scheduling. Confirm only after checking the appointment details."
      confirmLabel={pendingUpdate?.label || 'Confirm Update'}
      variant={pendingUpdate?.status === 'cancelled' ? 'destructive' : 'default'}
      isLoading={isLoading}
      onConfirm={onConfirm}
    />
  );
}
