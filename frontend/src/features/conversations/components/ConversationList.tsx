import { Link, useNavigate } from 'react-router-dom';
import { useConversationsList } from '../api';
import type { Conversation } from '../api';
import { getConversationAiModeLabel, getConversationChannelLabel } from '../display';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { formatDistanceToNow } from 'date-fns';
import { ArrowRight, Loader2, MessageSquare, PlugZap, RefreshCcw } from 'lucide-react';
import { TableSkeleton } from '@/components/ui/loading-states';
interface ConversationListProps {
  searchTerm: string;
  statusFilter: string;
}
export function ConversationList({ searchTerm, statusFilter }: ConversationListProps) {
  const navigate = useNavigate();

  const {
    data,
    error,
    fetchNextPage,
    hasNextPage,
    isError,
    isFetching,
    isFetchingNextPage,
    isLoading,
    refetch,
  } = useConversationsList(searchTerm, statusFilter);
  if (isLoading) {
    return <TableSkeleton columns={6} rows={6} mobileCards={false} />;
  }
  if (isError) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-card/70 p-8 text-center sm:p-12">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-destructive/20 bg-destructive/10">
          <MessageSquare className="h-5 w-5 text-destructive" />
        </div>
        <h3 className="mt-4 text-[16px] font-semibold">Could not load conversations</h3>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
          {error?.message || 'The conversation feed did not respond. Try refreshing the list.'}
        </p>
        <Button className="mt-5" size="sm" variant="outline" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCcw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>
    );
  }
  const items = data?.pages.flatMap(page => page.items) || [];
  if (items.length === 0) {
    const filtered = Boolean(searchTerm.trim() || statusFilter !== 'all');
    return (
      <div className="rounded-lg border bg-card/70 p-8 text-center sm:p-12">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-primary/20 bg-primary/10">
          <MessageSquare className="h-5 w-5 text-primary" />
        </div>
        <h3 className="mt-4 text-[16px] font-semibold">
          {filtered ? 'No matching conversations' : 'No conversations yet'}
        </h3>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
          {filtered
            ? 'Try a different search or status filter to find older customer chats. Paused human handoffs also remain visible from Needs Team Reply.'
            : 'Share your AWA Chat link or connect WhatsApp so customer messages start appearing here. If AWA pauses a chat for human help, it also appears in Needs Team Reply.'}
        </p>
        <div className="mt-5 flex flex-col justify-center gap-2 sm:flex-row">
          <Button asChild size="sm">
            <Link to="/dashboard/awa-chat">
              Open AWA Chat
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/dashboard/whatsapp">
              <PlugZap className="h-3.5 w-3.5" />
              Connect WhatsApp
            </Link>
          </Button>
        </div>
      </div>
    );
  }
  const getStatusColor = (status: string) => {
    switch(status) {
      case 'active': return 'bg-emerald-500/10 text-emerald-500';
      case 'escalated': return 'bg-yellow-500/10 text-yellow-500';
      case 'closed': return 'bg-muted text-muted-foreground';
      default: return 'bg-muted text-muted-foreground';
    }
  };
  const openConversation = (conversationId: string) => {
    navigate(`/dashboard/conversations/${conversationId}`);
  };
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCcw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>
      <div className="overflow-hidden rounded-md border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Customer</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Channel</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>AI Mode</TableHead>
              <TableHead>Last Active</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((conv: Conversation) => (
              <TableRow
                key={conv.id}
                className="cursor-pointer hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                tabIndex={0}
                aria-label={`Open conversation for ${conv.customer_name || conv.customer_phone || 'unknown customer'}`}
                onClick={() => openConversation(conv.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    openConversation(conv.id);
                  }
                }}
              >
                <TableCell>
                  <Link
                    to={`/dashboard/conversations/${conv.id}`}
                    className="font-medium hover:underline focus:outline-none focus:underline"
                  >
                    {conv.customer_name || 'Unknown'}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{conv.customer_phone}</TableCell>
                <TableCell>
                  <span className="rounded-md bg-secondary px-2 py-1 text-xs font-medium text-foreground">
                    {getConversationChannelLabel(conv.channel_type)}
                  </span>
                </TableCell>
                <TableCell>
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(conv.status)}`}>
                    {conv.status}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="px-2 py-1 rounded-md bg-primary/10 text-primary text-xs tracking-wide uppercase">
                    {getConversationAiModeLabel(conv.ai_mode)}
                  </span>
                </TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {conv.last_message_at ? formatDistanceToNow(new Date(conv.last_message_at), { addSuffix: true }) : 'No messages yet'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {hasNextPage && (
        <div className="flex justify-center pt-2">
          <Button variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
            {isFetchingNextPage && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isFetchingNextPage ? 'Loading more' : 'Load Older Conversations'}
          </Button>
        </div>
      )}
    </div>
  );
}
