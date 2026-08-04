import {
  AlarmClock,
  CheckCircle2,
  Clock3,
  RotateCcw,
  Send,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { DetailSheetSkeleton } from '@/components/ui/loading-states';
import type {
  ScheduledMessage,
  ScheduledMessageAction,
  ScheduledMessageEvent,
} from '../api';
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

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}

/* ── props ──────────────────────────────────────────────────── */

interface ReminderDetailSheetProps {
  open: boolean;
  message: ScheduledMessage | null;
  isLoading: boolean;
  events: ScheduledMessageEvent[];
  eventsLoading: boolean;
  onClose: () => void;
  onAction: (
    message: ScheduledMessage,
    action: ScheduledMessageAction,
    label: string,
    tone?: 'default' | 'destructive'
  ) => void;
}

/**
 * Side sheet for scheduled message detail: status card, context info,
 * status action buttons, and timeline events log.
 * Passes action intent up to parent via onAction — no mutations here.
 */
export function ReminderDetailSheet({
  open,
  message,
  isLoading,
  events,
  eventsLoading,
  onClose,
  onAction,
}: ReminderDetailSheetProps) {
  return (
    <Sheet open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <SheetContent className="overflow-y-auto sm:max-w-xl">
        {!message && isLoading ? (
          <>
            <SheetHeader className="mb-6 border-b pb-4">
              <SheetTitle>Scheduled Follow-up</SheetTitle>
              <SheetDescription>Loading message details…</SheetDescription>
            </SheetHeader>
            <DetailSheetSkeleton />
          </>
        ) : message ? (
          <>
            <SheetHeader className="mb-6 border-b pb-4">
              <SheetTitle id="detail-sheet-title">Scheduled Follow-up</SheetTitle>
              <SheetDescription
                id="detail-sheet-desc"
                className="font-mono text-xs break-all"
              >
                ID: {message.id}
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-6">
              {/* Status + scheduled date */}
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-md border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Status</p>
                  <div className="mt-1.5">
                    <ReminderStatusBadge status={message.status} />
                  </div>
                </div>
                <div className="rounded-md border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Scheduled for</p>
                  <p className="mt-1 text-[13px] font-semibold">
                    {formatDateTime(message.scheduled_for)}
                  </p>
                </div>
              </div>

              {/* Message content */}
              <div className="rounded-md border p-3">
                <h4 className="text-[13px] font-semibold">Message</h4>
                <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6">
                  {message.message_text}
                </p>
              </div>

              {/* Context info */}
              <div className="rounded-md border p-3 text-[13px]">
                <h4 className="mb-3 font-semibold">Context</h4>
                <div className="space-y-2">
                  <InfoRow
                    label="Customer"
                    value={message.customer_name || 'Unlinked'}
                  />
                  {message.customer_phone && (
                    <InfoRow label="Phone" value={message.customer_phone} />
                  )}
                  <InfoRow label="Trigger" value={TRIGGER_COPY[message.trigger_type]} />
                  <InfoRow label="Channel" value={CHANNEL_COPY[message.channel]} />
                  <InfoRow label="Attempts" value={String(message.attempt_count)} />
                  {message.rule_name && (
                    <InfoRow label="Rule" value={message.rule_name} />
                  )}
                  {message.booking_service_name && (
                    <InfoRow label="Service" value={message.booking_service_name} />
                  )}
                  {message.booking_date && (
                    <InfoRow label="Booking date" value={message.booking_date} />
                  )}
                  {message.order_status && (
                    <InfoRow label="Order status" value={message.order_status} />
                  )}
                  {message.payment_status && (
                    <InfoRow label="Payment status" value={message.payment_status} />
                  )}
                </div>
                {message.last_error && (
                  <p className="mt-3 rounded-md bg-destructive/10 p-2 text-[12px] text-destructive">
                    Last error: {message.last_error}
                  </p>
                )}
              </div>

              {/* Actions */}
              <div className="space-y-2">
                <h4 className="text-[13px] font-semibold">Actions</h4>
                <div className="grid gap-2">
                  {['scheduled', 'snoozed', 'failed'].includes(message.status) && (
                    <Button
                      className="justify-start"
                      onClick={() => onAction(message, 'mark_due', 'Mark Due')}
                      aria-label="Mark this message as due for delivery"
                    >
                      <AlarmClock className="h-4 w-4" aria-hidden="true" />
                      Mark Due
                    </Button>
                  )}
                  {!['sent', 'cancelled', 'skipped'].includes(message.status) && (
                    <Button
                      variant="outline"
                      className="justify-start"
                      onClick={() => onAction(message, 'mark_sent', 'Mark Sent')}
                      aria-label="Mark as already sent — only do this if delivery was confirmed"
                    >
                      <Send className="h-4 w-4" aria-hidden="true" />
                      Mark Sent
                    </Button>
                  )}
                  {!['sent', 'cancelled', 'skipped'].includes(message.status) && (
                    <Button
                      variant="outline"
                      className="justify-start"
                      onClick={() => onAction(message, 'snooze', 'Snooze Message')}
                      aria-label="Snooze this message — reschedule for a later time"
                    >
                      <Clock3 className="h-4 w-4" aria-hidden="true" />
                      Snooze
                    </Button>
                  )}
                  {message.status === 'failed' && (
                    <Button
                      variant="outline"
                      className="justify-start"
                      onClick={() => onAction(message, 'retry', 'Retry Message')}
                      aria-label="Retry delivery of this failed message"
                    >
                      <RotateCcw className="h-4 w-4" aria-hidden="true" />
                      Retry
                    </Button>
                  )}
                  {!['sent', 'cancelled', 'skipped'].includes(message.status) && (
                    <Button
                      variant="outline"
                      className="justify-start"
                      onClick={() => onAction(message, 'mark_failed', 'Mark Failed')}
                      aria-label="Mark this message as failed"
                    >
                      <XCircle className="h-4 w-4" aria-hidden="true" />
                      Mark Failed
                    </Button>
                  )}
                  {!['sent', 'cancelled', 'skipped'].includes(message.status) && (
                    <Button
                      variant="destructive"
                      className="justify-start"
                      onClick={() =>
                        onAction(message, 'cancel', 'Cancel Message', 'destructive')
                      }
                      aria-label="Cancel this scheduled message — it will not be sent"
                    >
                      <XCircle className="h-4 w-4" aria-hidden="true" />
                      Cancel
                    </Button>
                  )}
                  {!['sent', 'skipped'].includes(message.status) && (
                    <Button
                      variant="outline"
                      className="justify-start"
                      onClick={() => onAction(message, 'skip', 'Skip Message')}
                      aria-label="Skip this message — mark as skipped without sending"
                    >
                      <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                      Skip
                    </Button>
                  )}
                </div>
              </div>

              {/* Timeline */}
              <div>
                <h4 className="mb-2 text-[13px] font-semibold">Timeline</h4>
                <div className="space-y-2 rounded-md border p-3">
                  {eventsLoading ? (
                    <p className="text-[13px] text-muted-foreground">Loading events…</p>
                  ) : events.length > 0 ? (
                    events.map((event) => (
                      <div
                        key={event.id}
                        className="flex items-start gap-2 rounded-md bg-muted/40 p-2 text-[12px]"
                      >
                        <Clock3
                          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground"
                          aria-hidden="true"
                        />
                        <div>
                          <p className="font-medium capitalize">
                            {event.event_type.replace(/_/g, ' ')}
                          </p>
                          <p className="text-muted-foreground">
                            {formatDateTime(event.created_at)} by{' '}
                            {event.actor_type.replace(/_/g, ' ')}
                          </p>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="text-[13px] text-muted-foreground">No events yet.</p>
                  )}
                </div>
              </div>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
