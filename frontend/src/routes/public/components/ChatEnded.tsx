import { CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ChatEndedProps {
  onResetChat: () => void;
}

export function ChatEnded({ onResetChat }: ChatEndedProps) {
  return (
    <section className="flex flex-1 items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm rounded-md border border-border bg-card p-5 text-center">
        <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-300">
          <CheckCircle2 className="h-5 w-5" />
        </div>
        <h2 className="mt-4 text-[18px] font-semibold text-foreground">Chat ended</h2>
        <p className="mt-2 text-[13px] leading-6 text-muted-foreground">
          This chat is closed on this device. Start fresh before sharing the device with someone else.
        </p>
        <div className="mt-5 grid gap-2">
          <Button type="button" className="h-10 bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-zinc-950 dark:hover:bg-emerald-400" onClick={onResetChat}>
            Start new chat
          </Button>
        </div>
      </div>
    </section>
  );
}