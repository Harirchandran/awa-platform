import { useParams } from 'react-router-dom';
import { ListChecks, MessageSquare, Send } from 'lucide-react';
import { useConversationMessages } from '../api';
import { Card, CardHeader, CardTitle } from '@/components/ui/card';
import { format } from 'date-fns';
import { ScrollArea } from '@/components/ui/scroll-area';
import { MessageThreadSkeleton } from '@/components/ui/loading-states';
import { useEffect, useRef, useMemo } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { useVirtualizer } from '@tanstack/react-virtual';

export function ChatThread() {
  const { id } = useParams<{ id: string }>();
  const {
    data,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useConversationMessages(id);

  const messages = useMemo(() => {
    if (!data?.pages) return [];
    return data.pages
      .slice()
      .reverse()
      .flatMap((page) => page.items.slice().reverse());
  }, [data]);

  const viewportRef = useRef<HTMLDivElement>(null);
  const scrollAnchorRef = useRef<HTMLDivElement>(null);
  const lastMessageId = messages[messages.length - 1]?.id;
  const prevLastMessageIdRef = useRef<string | null>(null);

  const hasButton = hasNextPage;
  const itemCount = messages.length + (hasButton ? 1 : 0);

  // TanStack Virtual returns imperative helpers; this component keeps them local.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: itemCount,
    getScrollElement: () => viewportRef.current,
    estimateSize: (index) => {
      if (hasButton && index === 0) return 50; // estimate for button height
      return 85; // estimate for standard bubble height
    },
    overscan: 5,
  });

  useEffect(() => {
    if (lastMessageId && lastMessageId !== prevLastMessageIdRef.current) {
      prevLastMessageIdRef.current = lastMessageId;
      rowVirtualizer.scrollToIndex(itemCount - 1, { align: 'end', behavior: 'auto' });
      scrollAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [lastMessageId, itemCount, rowVirtualizer]);

  if (isLoading) {
    return (
      <Card className="h-full min-h-[500px] flex-1 overflow-hidden">
        <CardHeader className="border-b border-border py-3">
          <CardTitle className="text-sm">Message Thread</CardTitle>
        </CardHeader>
        <MessageThreadSkeleton />
      </Card>
    );
  }

  // Fallback for an empty thread
  if (messages.length === 0) {
    return (
      <Card className="h-full flex-1 flex flex-col items-center justify-center min-h-[500px] bg-card/50">
        <MessageSquare className="h-10 w-10 text-muted-foreground/30 mb-4" />
        <p className="text-muted-foreground text-sm">No messages have been exchanged yet.</p>
      </Card>
    );
  }

  return (
    <Card className="h-full flex-1 flex flex-col bg-slate-50 dark:bg-zinc-950 min-h-[600px] border-border overflow-hidden relative">
      <div className="absolute inset-0 bg-muted/30 pointer-events-none"></div>
      <CardHeader className="bg-card border-b border-border z-10 py-3">
         <CardTitle className="text-sm">Message Thread</CardTitle>
      </CardHeader>
      <ScrollArea viewportRef={viewportRef} className="flex-1 p-4 z-10">
        <div
          style={{
            height: `${rowVirtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative',
          }}
        >
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const isBtn = hasButton && virtualRow.index === 0;

            if (isBtn) {
              return (
                <div
                  key="load-older-btn"
                  ref={rowVirtualizer.measureElement}
                  data-index={virtualRow.index}
                  className="flex justify-center pb-2 w-full absolute left-0 top-0"
                  style={{
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 text-[12px] bg-card"
                    onClick={() => fetchNextPage()}
                    disabled={isFetchingNextPage}
                  >
                    {isFetchingNextPage ? 'Loading...' : 'Load older messages'}
                  </Button>
                </div>
              );
            }

            const msgIndex = hasButton ? virtualRow.index - 1 : virtualRow.index;
            const msg = messages[msgIndex];
            if (!msg) return null;
            const isUser = msg.role === 'user';

            return (
              <div
                key={msg.id}
                ref={rowVirtualizer.measureElement}
                data-index={virtualRow.index}
                className={`flex w-full ${isUser ? 'justify-end' : 'justify-start'} pb-4 absolute left-0 top-0`}
                style={{
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                <div
                  className={`relative max-w-[75%] px-4 py-2.5 rounded-2xl text-[15px] shadow-sm
                    ${isUser
                      ? 'bg-primary text-primary-foreground rounded-tr-sm'
                      : 'bg-card border border-border text-foreground rounded-tl-sm'
                    }
                  `}
                >
                  <p className="whitespace-pre-wrap format-base leading-relaxed">{msg.content}</p>
                  {!isUser && msg.message_type === 'interactive' && (
                    <InteractivePreview payload={msg.raw_payload} />
                  )}
                  <div className={`text-[10px] flex items-center justify-end mt-1 space-x-1 ${isUser ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>
                    <span>{format(new Date(msg.sent_at), 'h:mm a')}</span>
                    {/* Add double checkmark simulated read receipts for AI messages */}
                    {!isUser && msg.delivery_status && (
                      <span className="ml-1 uppercase tracking-wider">{msg.delivery_status}</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        <div ref={scrollAnchorRef} />
      </ScrollArea>
      <div className="z-10 border-t border-border bg-card p-3 flex gap-2">
        <Input
          disabled
          placeholder="Admin reply coming soon..."
          className="flex-1 bg-muted/50"
        />
        <Button disabled size="icon" variant="secondary">
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </Card>
  );
}

type InteractiveButton = {
  reply?: {
    id?: string;
    title?: string;
  };
};

type InteractiveRow = {
  id?: string;
  title?: string;
};

type InteractivePayload = {
  type?: 'button' | 'list';
  action?: {
    buttons?: InteractiveButton[];
    sections?: Array<{
      rows?: InteractiveRow[];
    }>;
  };
};

function InteractivePreview({ payload }: { payload: unknown }) {
  const data = payload as InteractivePayload | undefined;
  const buttons = data?.action?.buttons?.map((button) => button.reply?.title).filter(Boolean) ?? [];
  const rows = data?.action?.sections?.flatMap((section) => section.rows ?? []).map((row) => row.title).filter(Boolean) ?? [];
  const options = data?.type === 'list' ? rows : buttons;
  if (!options.length) return null;
  return (
    <div className="mt-3 rounded-md border border-border/70 bg-muted/50 p-2">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
        <ListChecks className="h-3.5 w-3.5" />
        <span>{data?.type === 'list' ? 'WhatsApp list sent' : 'WhatsApp buttons sent'}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {options.slice(0, 6).map((option) => (
          <span key={option} className="rounded border border-border bg-background px-2 py-1 text-[11px] leading-none text-foreground">
            {option}
          </span>
        ))}
        {options.length > 6 && (
          <span className="rounded border border-border bg-background px-2 py-1 text-[11px] leading-none text-muted-foreground">
            +{options.length - 6} more
          </span>
        )}
      </div>
    </div>
  );
}
