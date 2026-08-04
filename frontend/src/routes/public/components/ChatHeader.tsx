import { RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DisplayPreferencesToggle } from '@/components/accessibility/DisplayPreferences';

interface ChatHeaderProps {
  tenantName: string;
  businessName: string;
  isOnline: boolean;
  sessionActive: boolean;
  isEnding: boolean;
  onEndChat: () => void;
  onResetChat: () => void;
}

export function ChatHeader({
  tenantName,
  businessName,
  isOnline,
  sessionActive,
  isEnding,
  onEndChat,
  onResetChat,
}: ChatHeaderProps) {
  return (
    <header className="border-b border-border px-4 py-4">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-emerald-600 text-[14px] font-bold text-white dark:bg-emerald-500 dark:text-zinc-950">
          {tenantName.charAt(0).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[16px] font-semibold text-foreground">{businessName}</h1>
          <div
            className="mt-0.5 flex items-center gap-1.5 text-[12px] text-muted-foreground"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${isOnline ? 'bg-emerald-500 dark:bg-emerald-400' : 'bg-amber-500 dark:bg-amber-400'}`} />
            <span>{isOnline ? 'AWA Chat active' : 'Reconnecting'}</span>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <DisplayPreferencesToggle mode="inline" />
          {sessionActive && (
            <>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-[12px] text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                onClick={onEndChat}
                disabled={isEnding}
              >
                End
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                onClick={onResetChat}
                aria-label="Start new chat"
              >
                <RefreshCcw className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
