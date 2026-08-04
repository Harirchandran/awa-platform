import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ReviewErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export function ReviewErrorState({
  title = 'Could not load reviews',
  message = 'Check your connection and try again.',
  onRetry,
}: ReviewErrorStateProps) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{title}</p>
          <p className="mt-1 text-[13px] leading-normal text-destructive/90">{message}</p>
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
