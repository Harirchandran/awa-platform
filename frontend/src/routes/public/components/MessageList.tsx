import { AlertTriangle, Clock3, Loader2, RefreshCcw, ShieldCheck, WifiOff } from 'lucide-react';
import { type PublicChatMessage } from '@/features/awa-chat/api';
import { MessageBubble } from './MessageBubble';
import { type PendingReply } from './chat-utils';
import { TypingDots } from './TypingDots';

interface MessageListProps {
  messages: PublicChatMessage[];
  lastAssistantMessage?: PublicChatMessage;
  privacyNote: string;
  isOnline: boolean;
  isError: boolean;
  isFetching: boolean;
  onRetry: () => void;
  processingDelayed: boolean;
  processingDelayMessage: string | null;
  businessName: string;
  suggestedReplies?: string[];
  inputLocked: boolean;
  onQuickReply: (messageId: string, reply: string) => void;
  pendingQuickReply: string | null;
  sendMessagePending: boolean;
  pendingReply: PendingReply | null;
  showSlowReply: boolean;
  scrollAnchorRef: React.RefObject<HTMLDivElement | null>;
  showScrollButton: boolean;
  onScrollToBottom: () => void;
  children?: React.ReactNode;
  conversationStatus?: string;
}

export function MessageList({
  messages,
  lastAssistantMessage,
  privacyNote,
  isOnline,
  isError,
  isFetching,
  onRetry,
  processingDelayed,
  processingDelayMessage,
  businessName,
  suggestedReplies,
  inputLocked,
  onQuickReply,
  pendingQuickReply,
  sendMessagePending,
  pendingReply,
  showSlowReply,
  scrollAnchorRef,
  showScrollButton,
  onScrollToBottom,
  children,
  conversationStatus,
}: MessageListProps) {
  return (
    <div className="relative flex-1 flex flex-col min-h-0">
      <section className="flex-1 space-y-3 overflow-y-auto overscroll-contain px-4 py-4 relative">
        <div className="rounded-md border border-emerald-200 dark:border-emerald-500/20 bg-emerald-50 dark:bg-emerald-500/10 p-3 text-[12px] leading-5 text-emerald-800 dark:text-emerald-100">
          <div className="flex gap-2">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
            <p>{privacyNote}</p>
          </div>
        </div>
        {!isOnline && (
          <div
            className="rounded-md border border-amber-200 dark:border-amber-500/20 bg-amber-50 dark:bg-amber-500/10 p-3 text-[12px] leading-5 text-amber-800 dark:text-amber-100"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <div className="flex gap-2">
              <WifiOff className="mt-0.5 h-4 w-4 shrink-0" />
              <p>You are offline. Messages are paused until the connection returns.</p>
            </div>
          </div>
        )}
        {isError && (
          <div
            className="rounded-md border border-amber-200 dark:border-amber-500/20 bg-amber-50 dark:bg-amber-500/10 p-3 text-[12px] leading-5 text-amber-800 dark:text-amber-100"
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <p>Trying to reconnect to this chat.</p>
              </div>
              <button type="button" className="flex items-center gap-1.5 font-semibold text-amber-900 dark:text-amber-50" onClick={onRetry} disabled={isFetching}>
                <RefreshCcw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
                Retry
              </button>
            </div>
          </div>
        )}
        {processingDelayed && (
          <div
            className="rounded-md border border-amber-200 dark:border-amber-500/20 bg-amber-50 dark:bg-amber-500/10 p-3 text-[12px] leading-5 text-amber-800 dark:text-amber-100"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex gap-2">
                <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-300" />
                <p>{processingDelayMessage || 'Replies are currently delayed. You can wait here, or leave contact details below and we will follow up.'}</p>
              </div>
            </div>
          </div>
        )}
        {children}
        {!messages.length && (
          <div className="flex flex-col items-center justify-center space-y-6 pt-12 pb-8 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-500/20 text-2xl font-bold text-emerald-600 dark:text-emerald-400 ring-4 ring-emerald-500/10">
              {businessName.charAt(0).toUpperCase()}
            </div>
            <div className="space-y-2">
              <h2 className="text-xl font-medium text-foreground">Welcome to {businessName}</h2>
              <p className="text-[14px] text-muted-foreground max-w-[280px] mx-auto">
                How can we help you today? Choose an option below or type a message to start.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-2 max-w-[400px]">
              {suggestedReplies?.map((reply) => (
                <button
                  key={reply}
                  type="button"
                  onClick={() => onQuickReply('suggested', reply)}
                  disabled={inputLocked || !isOnline}
                  className="inline-flex items-center gap-1.5 rounded-full border border-emerald-300 dark:border-emerald-500/20 bg-emerald-50 dark:bg-emerald-500/10 px-4 py-2.5 text-[14px] text-emerald-800 dark:text-emerald-100 transition hover:bg-emerald-100 dark:hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-60 shadow-sm"
                >
                  {pendingQuickReply === `suggested:${reply}` && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {reply}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="space-y-3" aria-live="polite" aria-atomic="false">
          {messages.map((message, index) => {
            const prevMessage = messages[index - 1];
            const nextMessage = messages[index + 1];
            const isSameDirectionPrev = prevMessage?.direction === message.direction;
            const isSameDirectionNext = nextMessage?.direction === message.direction;
            const timeDiffPrev = prevMessage ? new Date(message.created_at).getTime() - new Date(prevMessage.created_at).getTime() : Infinity;
            const timeDiffNext = nextMessage ? new Date(nextMessage.created_at).getTime() - new Date(message.created_at).getTime() : Infinity;
            const isGroupedWithPrev = isSameDirectionPrev && timeDiffPrev < 60000;
            const isGroupedWithNext = isSameDirectionNext && timeDiffNext < 60000;
            const isFirstInGroup = !isGroupedWithPrev;
            const isLastInGroup = !isGroupedWithNext;
            return (
              <MessageBubble
                key={message.id}
                message={message}
                messages={messages}
                lastAssistantMessage={lastAssistantMessage}
                isFirstInGroup={isFirstInGroup}
                isLastInGroup={isLastInGroup}
                businessName={businessName}
                inputLocked={inputLocked || !isOnline}
                pendingQuickReply={pendingQuickReply}
                onQuickReply={onQuickReply}
              />
            );
          })}
          {pendingReply && showSlowReply && !processingDelayed && (
            <div className="flex justify-start">
              <div className="max-w-[86%] rounded-2xl rounded-tl-sm border border-amber-200 dark:border-amber-500/20 bg-amber-50 dark:bg-amber-500/10 px-3 py-2.5 text-[13px] leading-5 text-amber-800 dark:text-amber-100">
                <div className="flex gap-2">
                  <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-300" />
                  <p>Your message is saved. AWA is taking longer than usual, so you can wait here, add more context, or ask for a person.</p>
                </div>
              </div>
            </div>
          )}
          {conversationStatus !== 'escalated' && (sendMessagePending || (pendingReply && !showSlowReply)) && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-tl-sm border border-border bg-card px-3 py-2.5 text-[13px] text-muted-foreground">
                <TypingDots />
              </div>
            </div>
          )}
          <div ref={scrollAnchorRef} />
        </div>
      </section>
      {showScrollButton && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-10">
          <button
            type="button"
            onClick={onScrollToBottom}
            className="flex items-center gap-1.5 rounded-full border border-border bg-background/90 px-4 py-1.5 text-[12px] font-medium text-foreground shadow-lg backdrop-blur-sm transition hover:bg-muted/90"
          >
            New message
          </button>
        </div>
      )}
    </div>
  );
}
