import { AlertTriangle, BellRing, Loader2, Plus } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TableSkeleton } from '@/components/ui/loading-states';
import { ResponsiveDataList } from '@/components/shared/ResponsiveDataList';
import { cn } from '@/lib/utils';
import type { ScheduledMessage } from '../api';
import { CHANNEL_COPY, TRIGGER_COPY } from '../constants';
import { ReminderStatusBadge } from './ReminderStatusBadge';

/* ── helpers ────────────────────────────────────────────────── */

function formatDateTime(value?: string | null): string {
  if (!value) return 'Not set';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? 'Not set'
    : parsed.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}

/* ── sub-components ─────────────────────────────────────────── */

/** Desktop table row for a single scheduled message */
function MessageTableRow({
  message,
  onClick,
}: {
  message: ScheduledMessage;
  onClick: () => void;
}) {
  return (
    <TableRow
      className="cursor-pointer hover:bg-muted/50"
      onClick={onClick}
      tabIndex={0}
      role="button"
      aria-label={`View details for message: ${message.message_text.slice(0, 60)}`}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick();
        }
      }}
    >
      <TableCell>
        <p className="line-clamp-1 text-[13px] font-medium">{message.message_text}</p>
        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {message.id.split('-')[0]}
        </p>
      </TableCell>
      <TableCell>
        <p className="text-[13px] font-medium">
          {message.customer_name || 'Unlinked customer'}
        </p>
        <p className="text-xs text-muted-foreground">{message.customer_phone || 'No phone'}</p>
      </TableCell>
      <TableCell className="text-[13px] text-muted-foreground">
        {TRIGGER_COPY[message.trigger_type]}
      </TableCell>
      <TableCell className="text-[13px] text-muted-foreground">
        {CHANNEL_COPY[message.channel]}
      </TableCell>
      <TableCell>
        <ReminderStatusBadge status={message.status} />
      </TableCell>
      <TableCell className="text-[13px] text-muted-foreground">
        {formatDateTime(message.scheduled_for)}
      </TableCell>
    </TableRow>
  );
}

/** Mobile card for a single scheduled message */
function MessageMobileCard({
  message,
  onClick,
}: {
  message: ScheduledMessage;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="w-full rounded-md border bg-card p-3 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={onClick}
      aria-label={`View details for message: ${message.message_text.slice(0, 60)}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="line-clamp-2 flex-1 text-[13px] font-medium leading-snug">
          {message.message_text}
        </p>
        <ReminderStatusBadge status={message.status} />
      </div>
      <div className="mt-2 space-y-0.5">
        <p className="text-[12px] text-muted-foreground">
          <span className="font-medium text-foreground">
            {message.customer_name || 'Unlinked'}
          </span>
          {message.customer_phone && ` · ${message.customer_phone}`}
        </p>
        <p className="text-[11px] text-muted-foreground">
          {TRIGGER_COPY[message.trigger_type]} · {CHANNEL_COPY[message.channel]}
        </p>
        <p className="text-[11px] text-muted-foreground">
          Scheduled: {formatDateTime(message.scheduled_for)}
        </p>
      </div>
    </button>
  );
}

/* ── main component ─────────────────────────────────────────── */

interface ReminderQueueTableProps {
  messages: ScheduledMessage[];
  isLoading: boolean;
  isFetching?: boolean;
  errorMessage: string | null;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  onSelectMessage: (id: string) => void;
  onScheduleMessage: () => void;
}

/**
 * Scheduled messages queue with desktop table and mobile card list views.
 * Includes loading, error, and empty states.
 * Note: counts reflect loaded (paginated) messages, not a global total.
 */
export function ReminderQueueTable({
  messages,
  isLoading,
  isFetching = false,
  errorMessage,
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
  onSelectMessage,
  onScheduleMessage,
}: ReminderQueueTableProps) {
  if (isLoading) {
    return <TableSkeleton columns={6} rows={6} />;
  }

  if (errorMessage) {
    return (
      <div
        role="alert"
        className="flex min-h-[360px] items-center justify-center rounded-md border border-destructive/40 bg-destructive/5"
      >
        <div className="max-w-md px-6 text-center text-destructive">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-destructive/40 bg-background">
            <AlertTriangle className="h-5 w-5" aria-hidden="true" />
          </div>
          <h3 className="mt-4 text-[15px] font-semibold">Scheduled messages unavailable</h3>
          <p className="mt-2 text-[13px] leading-relaxed">{errorMessage}</p>
        </div>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex min-h-[360px] items-center justify-center rounded-md border bg-card">
        <div className="max-w-sm px-6 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
            <BellRing className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          </div>
          <h3 className="mt-4 text-[15px] font-semibold text-foreground">
            No scheduled follow-ups
          </h3>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
            Scheduled follow-ups appear here after staff creates one manually or automation creates
            one from booking, payment, order, review, or inactivity rules.
          </p>
          <div className="mt-4 flex flex-col justify-center gap-2 sm:flex-row">
            <Button type="button" onClick={onScheduleMessage}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              Schedule Message
            </Button>
            <Button asChild variant="outline">
              <Link to="/dashboard/tools">Workflow Tools</Link>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const desktopTable = (
    <div className="overflow-x-auto rounded-md border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Message</TableHead>
            <TableHead scope="col">Customer</TableHead>
            <TableHead scope="col">Trigger</TableHead>
            <TableHead scope="col">Channel</TableHead>
            <TableHead scope="col">Status</TableHead>
            <TableHead scope="col">Scheduled</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {messages.map((message) => (
            <MessageTableRow
              key={message.id}
              message={message}
              onClick={() => onSelectMessage(message.id)}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );

  const mobileCards = (
    <div className="space-y-2" role="list" aria-label="Scheduled messages">
      {messages.map((message) => (
        <div key={message.id} role="listitem">
          <MessageMobileCard
            message={message}
            onClick={() => onSelectMessage(message.id)}
          />
        </div>
      ))}
    </div>
  );

  return (
    <div className={cn("space-y-3 transition-opacity duration-200", isFetching && !isLoading && "opacity-50 pointer-events-none")}>
      <ResponsiveDataList desktop={desktopTable} mobile={mobileCards} />

      {hasNextPage && (
        <div className="flex justify-center pt-2">
          <Button
            variant="outline"
            onClick={onLoadMore}
            disabled={isFetchingNextPage}
            aria-label="Load more scheduled messages"
          >
            {isFetchingNextPage && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            Load More
          </Button>
        </div>
      )}
    </div>
  );
}
