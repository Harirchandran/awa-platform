import { useMemo, useState, useEffect } from 'react';
import { Link } from 'react-router';
import { format, formatDistanceToNow } from 'date-fns';
import { AlertTriangle, CheckCircle2, Clock3, LifeBuoy, MessageSquare, RefreshCcw, Send } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { MessageThreadSkeleton } from '@/components/ui/loading-states';
import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import {
  useConversation,
  useConversationMessages,
  useEscalationAction,
  useEscalationReply,
  useEscalationsList,
  type Conversation,
  type EscalationReplyResult,
  type Message,
} from '@/features/conversations/api';
import { getConversationChannelLabel, getConversationStatusLabel, isConversationAwaChat } from '@/features/conversations/display';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import { cn } from '@/lib/utils';
function displayName(conversation?: Conversation) {
  return conversation?.customer_name || conversation?.customer_phone || 'Unknown customer';
}
function channelLabel(channel?: string | null) {
  return getConversationChannelLabel(channel);
}
function priorityLabel(conversation?: Conversation) {
  const reason = conversation?.escalation_reason?.toLowerCase() || '';
  if (reason.includes('emergency') || reason.includes('urgent')) return 'Urgent';
  return 'Waiting';
}
function assignmentLabel(conversation?: Conversation) {
  return conversation?.assigned_to ? 'Assigned' : 'Unassigned';
}
function formatReplyNotice(result: EscalationReplyResult) {
  if (result.delivery_status === 'pending_customer_contact') {
    return 'Customer left without contact details.';
  }
  if (result.delivery_channel === 'email') {
    return result.delivery_status === 'email_queued'
      ? 'Customer left. Email fallback queued.'
      : `Email fallback: ${result.delivery_status}`;
  }
  return `${channelLabel(result.delivery_channel)}: ${result.delivery_status}`;
}
type PendingConfirmation = {
  kind: 'return-to-awa' | 'resolve' | 'reply';
  title: string;
  description: string;
  confirmLabel: string;
  variant?: 'default' | 'destructive';
};
function MessageBubble({ message }: { message: Message }) {
  const isCustomer = message.role === 'user';
  return (
    <div className={cn('flex w-full', isCustomer ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[78%] rounded-2xl px-3 py-2 text-[13px] leading-6 shadow-sm',
          isCustomer
            ? 'rounded-tr-sm bg-primary text-primary-foreground'
            : 'rounded-tl-sm border border-border bg-background text-foreground'
        )}
      >
        <p className="whitespace-pre-wrap">{message.content}</p>
        <p className={cn('mt-1 text-[10px]', isCustomer ? 'text-primary-foreground/70' : 'text-muted-foreground')}>
          {format(new Date(message.sent_at), 'h:mm a')}
        </p>
      </div>
    </div>
  );
}
export function EscalationsPage() {
  const [searchTerm, setSearchTerm] = useState('');
  const [manualSelectedId, setManualSelectedId] = useState<string | null>(null);
  const [replyText, setReplyText] = useState('');
  const [saveToKnowledge, setSaveToKnowledge] = useState(false);
  const [replyNotice, setReplyNotice] = useState<string | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<PendingConfirmation | null>(null);
  const [mountedAt] = useState(() => Date.now());
  const escalationsQuery = useEscalationsList(searchTerm);
  const action = useEscalationAction();
  const reply = useEscalationReply();
  const { data: toolsData } = useTenantTools();
  const supportAccess = getModuleEntitlement(toolsData, 'support');
  const handoffAccess = getModuleEntitlement(toolsData, 'human_handoff');
  const faqAccess = getModuleEntitlement(toolsData, 'faq');
  const canMutateHandoffs = Boolean(
    toolsData && (supportAccess.canMutateRuntimeData || handoffAccess.canMutateRuntimeData)
  );
  const canSaveToKnowledge = Boolean(toolsData && faqAccess.canConfigure);
  const isReturningToAwa = action.isPending && action.variables?.action === 'return-to-awa';
  const isResolving = action.isPending && action.variables?.action === 'resolve';
  const escalations = useMemo(
    () => escalationsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [escalationsQuery.data]
  );

  const waitingCount = escalations.length;
  const now = escalationsQuery.dataUpdatedAt || mountedAt;

  // Browser document title count updater with cleanup on unmount
  useEffect(() => {
    const originalTitle = document.title;
    document.title = waitingCount > 0 ? `(${waitingCount}) Escalations — AWA` : 'Escalations — AWA';
    return () => {
      document.title = originalTitle;
    };
  }, [waitingCount]);

  const selectedId = useMemo(() => {
    if (!escalations.length) {
      return null;
    }
    if (manualSelectedId) {
      const exists = escalations.some((item) => item.id === manualSelectedId);
      if (exists) {
        return manualSelectedId;
      }
      return null; // Safe selection clearing guard (derived state)
    }
    return escalations[0].id;
  }, [escalations, manualSelectedId]);
  const selectedFromList = escalations.find((item) => item.id === selectedId);
  const conversationQuery = useConversation(selectedId || undefined);
  const messagesQuery = useConversationMessages(selectedId || undefined);
  const selectedConversation = conversationQuery.data || selectedFromList;
  const messages = useMemo(() => {
    if (!messagesQuery.data?.pages) return [];
    return messagesQuery.data.pages
      .slice()
      .reverse()
      .flatMap((page) => page.items.slice().reverse());
  }, [messagesQuery.data]);
  const canReply = canMutateHandoffs && Boolean(selectedId && replyText.trim()) && !reply.isPending && !action.isPending;
  const replyHelpText = !canMutateHandoffs
    ? 'Human handoff actions are unavailable on this plan.'
    : !replyText.trim()
    ? 'Enter a reply before sending to the customer.'
    : saveToKnowledge
      ? 'This reply will be sent and saved for future AWA answers.'
      : 'This reply will be sent to the customer.';
  const returnToAwa = () => {
    if (!selectedId || !canMutateHandoffs) return;
    setPendingConfirmation({
      kind: 'return-to-awa',
      title: 'Return chat to AWA?',
      description: 'Future customer messages in this conversation will be handled by AWA automation again. Use this only after the customer no longer needs a human reply.',
      confirmLabel: 'Return to AWA',
    });
  };
  const resolve = () => {
    if (!selectedId || !canMutateHandoffs) return;
    setPendingConfirmation({
      kind: 'resolve',
      title: 'Resolve handoff?',
      description: 'This removes the conversation from Needs Team Reply. The full thread remains available in Conversations for history.',
      confirmLabel: 'Resolve',
    });
  };
  const sendReply = () => {
    if (!selectedId || !replyText.trim() || !canMutateHandoffs || (saveToKnowledge && !canSaveToKnowledge)) return;
    setPendingConfirmation({
      kind: 'reply',
      title: saveToKnowledge ? 'Send and save reply?' : 'Send reply?',
      description: saveToKnowledge
        ? 'This sends your reply to the customer and saves it as approved Knowledge for future AWA answers.'
        : 'This sends your reply to the customer through the available channel. AWA remains paused until you resolve or return the chat to AWA.',
      confirmLabel: 'Send Reply',
    });
  };
  const confirmPendingAction = () => {
    if (!selectedId || !pendingConfirmation || !canMutateHandoffs) return;
    if (pendingConfirmation.kind === 'reply' && saveToKnowledge && !canSaveToKnowledge) return;
    if (pendingConfirmation.kind === 'return-to-awa') {
      setPendingConfirmation(null);
      action.mutate(
        { conversationId: selectedId, action: 'return-to-awa' },
        {
          onSuccess: () => {
            toast.success('Conversation returned to AWA');
            escalationsQuery.refetch();
          },
          onError: () => {
            toast.error('Failed to return conversation to AWA');
          },
        }
      );
      return;
    }
    if (pendingConfirmation.kind === 'resolve') {
      setPendingConfirmation(null);
      action.mutate(
        { conversationId: selectedId, action: 'resolve' },
        {
          onSuccess: () => {
            toast.success('Escalation resolved');
            escalationsQuery.refetch();
          },
          onError: () => {
            toast.error('Failed to resolve escalation');
          },
        }
      );
      return;
    }
    if (!replyText.trim()) return;
    setReplyNotice(null);
    setPendingConfirmation(null);
    reply.mutate(
      {
        conversationId: selectedId,
        messageText: replyText.trim(),
        saveToKnowledge,
      },
      {
        onSuccess: (result) => {
          setReplyText('');
          setSaveToKnowledge(false);
          setReplyNotice(formatReplyNotice(result));
          toast.success('Reply sent successfully');
        },
        onError: () => {
          setReplyNotice(null);
          toast.error('Failed to send reply');
        },
      }
    );
  };
  return (
    <div className="flex h-[calc(100vh-132px)] min-h-[620px] flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-[16px] font-semibold text-foreground">Needs Team Reply</h2>
            {waitingCount > 0 && (
              <span className="rounded-full bg-destructive px-2 py-0.5 text-[11px] font-semibold text-destructive-foreground animate-pulse" aria-label={`${waitingCount} pending handoffs`}>
                {waitingCount}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            {waitingCount} paused chat{waitingCount === 1 ? '' : 's'} waiting for human action. Resolved or returned chats move back into normal conversation flow.
          </p>
        </div>
        <SearchInputWithClear
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          onClear={() => setSearchTerm('')}
          placeholder="Search handoffs"
          className="w-full sm:w-[260px]"
        />
      </div>
      <div className="grid min-h-0 flex-1 overflow-hidden rounded-md border bg-card lg:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="min-h-0 border-b border-border lg:border-b-0 lg:border-r">
          <div className="flex h-11 items-center justify-between border-b border-border px-3">
            <div className="flex items-center gap-2 text-[13px] font-medium">
              <LifeBuoy className="h-4 w-4 text-primary" />
              Paused handoffs
            </div>
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => escalationsQuery.refetch()} disabled={escalationsQuery.isFetching} aria-label="Refresh handoffs">
              <RefreshCcw className={cn('h-3.5 w-3.5', escalationsQuery.isFetching && 'animate-spin')} />
            </Button>
          </div>
          <ScrollArea className="h-[260px] lg:h-[calc(100vh-220px)]">
            {escalationsQuery.isError ? (
              <div className="p-4 flex flex-col items-center justify-center text-center gap-2">
                <AlertTriangle className="h-8 w-8 text-destructive" />
                <p className="text-[13px] font-medium text-foreground">Failed to load handoffs</p>
                <Button size="sm" variant="outline" className="mt-1 flex items-center gap-1.5" onClick={() => escalationsQuery.refetch()}>
                  <RefreshCcw className="h-3 w-3" /> Retry
                </Button>
              </div>
            ) : escalationsQuery.isLoading ? (
              <div className="motion-enter space-y-2 p-3" aria-busy="true" aria-label="Loading handoffs">
                {Array.from({ length: 5 }).map((_, index) => (
                  <div key={index} className="rounded-md border border-border/60 bg-card p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1 space-y-2">
                        <Skeleton className="h-3.5 w-2/3" />
                        <Skeleton className="h-3 w-full" />
                        <Skeleton className="h-3 w-4/5" />
                      </div>
                      <Skeleton className="h-5 w-14 rounded-full" />
                    </div>
                    <div className="mt-3 flex justify-between">
                      <Skeleton className="h-3 w-16" />
                      <Skeleton className="h-3 w-20" />
                    </div>
                  </div>
                ))}
              </div>
            ) : escalations.length ? (
              <div className="divide-y divide-border">
                {escalations.map((conversation) => {
                  const active = conversation.id === selectedId;
                  const priority = priorityLabel(conversation);

                  // Safe parse of escalated_at with 15 minutes recent highlight
                  const escalatedAtStr = conversation.escalated_at;
                  let isRecent = false;
                  if (escalatedAtStr) {
                    try {
                      const escalatedTime = new Date(escalatedAtStr).getTime();
                      if (!isNaN(escalatedTime)) {
                        isRecent = (now - escalatedTime) < 15 * 60 * 1000;
                      }
                    } catch {
                      // Do not crash, skip the "New" highlight
                    }
                  }

                  return (
                    <button
                      key={conversation.id}
                      type="button"
                      onClick={() => setManualSelectedId(conversation.id)}
                      className={cn(
                        'relative block w-full px-3 py-3 text-left transition hover:bg-muted/60',
                        active && 'bg-muted',
                        isRecent && 'border-l-2 border-l-destructive pl-[10px]'
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-[13px] font-medium text-foreground">{displayName(conversation)}</p>
                          <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-muted-foreground">
                            {conversation.escalation_reason || 'Human review requested'}
                          </p>
                        </div>
                        <div className="flex flex-col items-end gap-1 shrink-0">
                          <span
                            className={cn(
                              'rounded-full px-2 py-0.5 text-[10px] font-medium',
                              priority === 'Urgent' ? 'bg-destructive/10 text-destructive' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                            )}
                          >
                            {priority}
                          </span>
                          {isRecent && (
                            <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold text-destructive uppercase tracking-wider">
                              <span className="h-1.5 w-1.5 rounded-full bg-destructive animate-ping" />
                              New
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
                        <span>{channelLabel(conversation.channel_type)}</span>
                        <span>
                          {conversation.escalated_at
                            ? formatDistanceToNow(new Date(conversation.escalated_at), { addSuffix: true })
                            : 'Just now'}
                        </span>
                      </div>
                      <div className="mt-1 text-[11px] text-muted-foreground">{assignmentLabel(conversation)}</div>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="flex h-[260px] flex-col items-center justify-center px-6 text-center lg:h-[calc(100vh-220px)]">
                <CheckCircle2 className="h-9 w-9 text-emerald-500" />
                <p className="mt-3 text-[14px] font-medium text-foreground">No human handoffs right now</p>
                <p className="mt-1 max-w-sm text-[12px] leading-5 text-muted-foreground">
                  New handoffs will appear here when AWA needs a team member.
                </p>
                <Button variant="outline" size="sm" className="mt-4 flex items-center gap-1.5" onClick={() => escalationsQuery.refetch()}>
                  <RefreshCcw className="h-3.5 w-3.5" />
                  Refresh
                </Button>
              </div>
            )}
          </ScrollArea>
        </aside>
        <section className="min-h-0">
          {selectedConversation ? (() => {
            const isNotEscalated = selectedConversation.status !== 'escalated';
            return (
              <div className="flex h-full min-h-0 flex-col">
                <div className="flex flex-col gap-3 border-b border-border px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate text-[14px] font-semibold text-foreground">{displayName(selectedConversation)}</h3>
                      <span className="rounded-md bg-secondary px-2 py-0.5 text-[11px] font-medium text-secondary-foreground">
                        {channelLabel(selectedConversation.channel_type)}
                      </span>
                      <span className={cn(
                        "rounded-md px-2 py-0.5 text-[11px] font-medium",
                        selectedConversation.status === 'escalated'
                          ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                          : selectedConversation.status === 'closed'
                          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                          : "bg-blue-500/10 text-blue-600 dark:text-blue-400"
                      )}>
                        {getConversationStatusLabel(selectedConversation.status)}
                      </span>
                      <span className="rounded-md bg-secondary px-2 py-0.5 text-[11px] font-medium text-secondary-foreground">
                        {assignmentLabel(selectedConversation)}
                      </span>
                    </div>
                    <p className="mt-1 text-[12px] text-muted-foreground">
                      {selectedConversation.escalation_reason || 'Human review requested'} Automation is paused until you reply, resolve this, or return it to AWA.
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={returnToAwa}
                      disabled={action.isPending || isNotEscalated || !canMutateHandoffs}
                      title={action.isPending ? 'Handoff action is already in progress' : isNotEscalated ? 'This conversation is no longer waiting for a team reply' : 'Return future messages to AWA automation'}
                    >
                      <RefreshCcw className={cn('h-3.5 w-3.5', isReturningToAwa && 'animate-spin')} />
                      Return to AWA
                    </Button>
                    <Button
                      size="sm"
                      onClick={resolve}
                      disabled={action.isPending || isNotEscalated || !canMutateHandoffs}
                      title={action.isPending ? 'Handoff action is already in progress' : isNotEscalated ? 'This conversation is no longer waiting for a team reply' : 'Resolve and remove from Needs Team Reply'}
                    >
                      <CheckCircle2 className={cn('h-3.5 w-3.5', isResolving && 'animate-pulse')} />
                      Resolve
                    </Button>
                    <Button asChild variant="ghost" size="sm">
                      <Link to={`/dashboard/conversations/${selectedConversation.id}`}>
                        <MessageSquare className="h-3.5 w-3.5" />
                        Full Thread
                      </Link>
                    </Button>
                  </div>
                </div>
                <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_280px]">
                  <div className="flex min-h-0 flex-col bg-muted/30">
                    <ScrollArea className="h-[340px] flex-1 lg:h-auto">
                      <div className="space-y-3 p-4">
                        {messagesQuery.isError ? (
                          <div className="flex h-[280px] flex-col items-center justify-center text-center gap-2">
                            <AlertTriangle className="h-8 w-8 text-destructive" />
                            <p className="text-[13px] font-medium text-destructive">Could not load conversation messages</p>
                            <Button size="sm" variant="outline" className="mt-1 flex items-center gap-1.5" onClick={() => messagesQuery.refetch()}>
                              <RefreshCcw className="h-3 w-3" /> Retry
                            </Button>
                          </div>
                        ) : messagesQuery.isPending ? (
                          <MessageThreadSkeleton className="p-0" />
                        ) : messages.length ? (
                          <>
                            {messagesQuery.hasNextPage && (
                              <div className="flex justify-center pb-2">
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="h-8 text-[12px] bg-card"
                                  onClick={() => messagesQuery.fetchNextPage()}
                                  disabled={messagesQuery.isFetchingNextPage}
                                >
                                  {messagesQuery.isFetchingNextPage ? 'Loading...' : 'Load older messages'}
                                </Button>
                              </div>
                            )}
                            {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
                          </>
                        ) : (
                          <div className="flex h-[280px] items-center justify-center text-[13px] text-muted-foreground">
                            No messages found.
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                    <div className="border-t border-border bg-card p-3">
                      {isNotEscalated && (
                        <div className="rounded-md border border-amber-200 bg-amber-50 dark:border-amber-900/30 dark:bg-amber-950/20 p-3 text-[12px] leading-5 text-amber-800 dark:text-amber-400 mb-3 flex gap-2">
                          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                          <span>This conversation is no longer waiting for a team reply.</span>
                        </div>
                      )}
                      {!canMutateHandoffs && (
                        <p className="mb-3 text-[12px] text-muted-foreground">Human handoff actions are unavailable on this plan.</p>
                      )}
                      <Textarea
                        value={replyText}
                        onChange={(event) => setReplyText(event.target.value)}
                        maxLength={2000}
                        rows={3}
                        placeholder="Reply as tenant"
                        disabled={isNotEscalated || !canMutateHandoffs}
                        className="min-h-[82px] resize-none text-[13px]"
                      />
                      <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        {canSaveToKnowledge && (
                          <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
                            <Switch checked={saveToKnowledge} onCheckedChange={setSaveToKnowledge} disabled={isNotEscalated || !canMutateHandoffs} aria-label="Save for AWA" />
                            <span>Save for AWA</span>
                          </label>
                        )}
                        <div className="flex items-center justify-end gap-2">
                          {replyNotice && <span className="text-[12px] text-muted-foreground">{replyNotice}</span>}
                          <Button
                            size="sm"
                            onClick={sendReply}
                            disabled={!canReply || isNotEscalated}
                            aria-describedby="escalation-reply-help"
                            title={canReply ? 'Review and send this reply' : replyHelpText}
                          >
                            {reply.isPending ? <RefreshCcw className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                            Send
                          </Button>
                        </div>
                      </div>
                      <p id="escalation-reply-help" className="mt-1 text-[12px] text-muted-foreground">{replyHelpText}</p>
                      {reply.isError && (
                        <p className="mt-2 text-[12px] text-destructive">Could not send this reply.</p>
                      )}
                    </div>
                  </div>
                  <div className="border-t border-border p-4 lg:border-l lg:border-t-0">
                    <div className="space-y-4">
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Customer</p>
                        <p className="mt-1 text-[13px] font-medium text-foreground">{displayName(selectedConversation)}</p>
                        <p className="text-[12px] text-muted-foreground">{selectedConversation.customer_phone}</p>
                        {selectedConversation.customer_email && (
                          <p className="text-[12px] text-muted-foreground">{selectedConversation.customer_email}</p>
                        )}
                        {isConversationAwaChat(selectedConversation.channel_type) && !selectedConversation.customer_email && (
                          <p className="mt-1 text-[12px] text-amber-600 dark:text-amber-400">No follow-up email captured yet.</p>
                        )}
                      </div>
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Waiting since</p>
                        <p className="mt-1 flex items-center gap-1.5 text-[13px] text-foreground">
                          <Clock3 className="h-3.5 w-3.5 text-muted-foreground" />
                          {selectedConversation.escalated_at
                            ? format(new Date(selectedConversation.escalated_at), 'MMM d, h:mm a')
                            : '-'}
                        </p>
                      </div>
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Reason</p>
                        <p className="mt-1 rounded-md border bg-background p-2 text-[12px] leading-5 text-foreground">
                          {selectedConversation.escalation_reason || 'Human review requested'}
                        </p>
                      </div>
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Customer-facing state</p>
                        <p className="mt-1 rounded-md border bg-background p-2 text-[12px] leading-5 text-foreground">
                          AWA has paused automation and asked the team to step in. No external staff notification is implied here.
                        </p>
                      </div>
                      {action.isError && (
                        <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-[12px] leading-5 text-destructive">
                          <div className="flex gap-2">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            <span>Could not update this handoff.</span>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })() : (
            <div className="flex h-full min-h-[420px] flex-col items-center justify-center px-6 text-center">
              <LifeBuoy className="h-10 w-10 text-muted-foreground/40" />
              <p className="mt-3 text-[14px] font-medium text-foreground">Select a chat that needs reply</p>
              <p className="mt-1 max-w-sm text-[12px] leading-5 text-muted-foreground">Customer handoffs and paused conversations appear in this workspace.</p>
            </div>
          )}
        </section>
      </div>
      <Dialog
        open={!!pendingConfirmation}
        onOpenChange={(open) => {
          if (!open && !action.isPending && !reply.isPending) {
            setPendingConfirmation(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pendingConfirmation?.title || 'Confirm action'}</DialogTitle>
            <DialogDescription>{pendingConfirmation?.description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingConfirmation(null)} disabled={action.isPending || reply.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              variant={pendingConfirmation?.variant || 'default'}
              onClick={confirmPendingAction}
              disabled={action.isPending || reply.isPending}
            >
              {(action.isPending || reply.isPending) && <RefreshCcw className="h-3.5 w-3.5 animate-spin" />}
              {pendingConfirmation?.confirmLabel || 'Confirm'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
