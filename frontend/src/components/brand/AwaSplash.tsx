import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
type AwaSplashProps = {
  compact?: boolean;
  className?: string;
};
export function AwaSplash({ compact = false, className }: AwaSplashProps) {
  return (
    <div
      className={cn(
        'flex min-h-[100dvh] items-center justify-center bg-background px-6 text-foreground',
        compact && 'min-h-[260px]',
        className
      )}
    >
      <div className="motion-enter flex flex-col items-center text-center">
        <div className="motion-splash-mark mb-4 flex h-12 w-12 items-center justify-center rounded-md border border-border bg-card text-[15px] font-bold shadow-sm">
          AWA
        </div>
        <p className="text-[13px] font-semibold text-foreground">Always Working Assistant</p>
        <p className="mt-1 text-[12px] text-muted-foreground">Customer conversations in one workspace.</p>
        <Loader2 className="mt-4 h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />
      </div>
    </div>
  );
}
