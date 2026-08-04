import { AlertTriangle, RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface KnowledgeErrorStateProps {
  onRetry: () => void;
  isRetrying?: boolean;
}

export function KnowledgeErrorState({ onRetry, isRetrying = false }: KnowledgeErrorStateProps) {
  return (
    <div className="rounded-md border border-destructive/20 bg-destructive/10 p-4 text-[13px] text-destructive">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load saved answers.</p>
            <p className="mt-1 text-[12px] opacity-90">Check your connection and try again.</p>
          </div>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={isRetrying}>
          <RefreshCcw className={`h-3.5 w-3.5 ${isRetrying ? 'animate-spin' : ''}`} aria-hidden="true" />
          Retry
        </Button>
      </div>
    </div>
  );
}
