import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface BookingErrorStateProps {
  onRetry?: () => void;
}

export function BookingErrorState({ onRetry }: BookingErrorStateProps) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">Could not load bookings</p>
          <p className="mt-1 text-[13px] leading-normal text-destructive/90">
            Check your connection and try again.
          </p>
        </div>
        {onRetry && (
          <Button type="button" size="sm" variant="outline" onClick={onRetry} className="shrink-0">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        )}
      </div>
    </div>
  );
}
