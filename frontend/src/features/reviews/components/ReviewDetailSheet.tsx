import { AlertTriangle, CheckCircle2, Clock3, ExternalLink, RotateCcw, Send, Star, Trash2, XCircle } from 'lucide-react';
import type { ReviewAction, ReviewEvent, ReviewRequest } from '../api';
import type { PendingReviewAction } from '../types';
import { REVIEW_ACTION_COPY, REVIEW_SOURCE_COPY } from '../constants';
import { formatReviewDateTime } from '../utils';
import { Button } from '@/components/ui/button';
import { DetailSheetSkeleton } from '@/components/ui/loading-states';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { ReviewRatingBadge, ReviewStatusBadge } from './ReviewBadges';
import { CopyButton } from '@/components/shared/CopyButton';

interface ReviewDetailSheetProps {
  review: ReviewRequest | null;
  isLoading: boolean;
  events?: ReviewEvent[];
  eventsLoading: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAction: (pending: PendingReviewAction) => void;
  onDelete?: (id: string) => void;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[65%] text-right font-medium">{value}</span>
    </div>
  );
}

function ActionButton({
  review,
  action,
  icon: Icon,
  variant = 'outline',
  onAction,
}: {
  review: ReviewRequest;
  action: ReviewAction;
  icon: typeof Send;
  variant?: 'default' | 'outline' | 'destructive';
  onAction: (pending: PendingReviewAction) => void;
}) {
  const copy = REVIEW_ACTION_COPY[action];

  return (
    <Button
      type="button"
      variant={variant}
      className="h-auto justify-start gap-3 py-3 text-left"
      onClick={() => onAction({
        review,
        action,
        label: copy.confirmLabel,
        tone: variant === 'destructive' ? 'destructive' : 'default',
      })}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="min-w-0">
        <span className="block text-[13px] font-medium">{copy.title}</span>
        <span className="mt-0.5 block text-[11px] font-normal leading-normal opacity-80">{copy.description}</span>
      </span>
    </Button>
  );
}

export function ReviewDetailSheet({
  review,
  isLoading,
  events,
  eventsLoading,
  open,
  onOpenChange,
  onAction,
  onDelete,
}: ReviewDetailSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="overflow-y-auto sm:max-w-xl">
        {!review && isLoading ? (
          <DetailSheetSkeleton />
        ) : review && (
          <>
            <SheetHeader className="mb-6 border-b pb-4 text-left">
              <SheetTitle>Review Request</SheetTitle>
              <SheetDescription>
                {review.customer_name || 'Unlinked customer'} - {REVIEW_SOURCE_COPY[review.source]} request
              </SheetDescription>
            </SheetHeader>
            <div className="space-y-6">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-md border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Status</p>
                  <div className="mt-1"><ReviewStatusBadge status={review.status} /></div>
                </div>
                <div className="rounded-md border bg-card p-3">
                  <p className="text-xs text-muted-foreground">Rating</p>
                  <div className="mt-1"><ReviewRatingBadge rating={review.rating} /></div>
                </div>
              </div>

              <section className="rounded-md border p-3 text-sm" aria-labelledby="review-context-heading">
                <h4 id="review-context-heading" className="mb-2 font-semibold">Context</h4>
                <div className="space-y-2">
                  <DetailRow label="Customer" value={review.customer_name || 'Unlinked'} />
                  <DetailRow label="Phone" value={review.customer_phone || 'No phone'} />
                  <DetailRow label="Source" value={REVIEW_SOURCE_COPY[review.source]} />
                  <DetailRow label="Sent" value={formatReviewDateTime(review.sent_at)} />
                  <DetailRow label="Responded" value={formatReviewDateTime(review.responded_at)} />
                  <DetailRow label="Created" value={formatReviewDateTime(review.created_at)} />
                </div>
                {review.public_review_url && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button asChild variant="outline" size="sm">
                      <a href={review.public_review_url} target="_blank" rel="noreferrer">
                        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                        Open public review link
                      </a>
                    </Button>
                    <CopyButton value={review.public_review_url || ''}>
                      Copy public review link
                    </CopyButton>
                  </div>
                )}
              </section>

              <section className="rounded-md border p-3" aria-labelledby="review-feedback-heading">
                <h4 id="review-feedback-heading" className="text-sm font-semibold">Private feedback</h4>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
                  {review.feedback_text || 'No feedback submitted yet.'}
                </p>
              </section>

              <section className="space-y-2" aria-labelledby="review-actions-heading">
                <h4 id="review-actions-heading" className="text-sm font-semibold">Actions</h4>
                <div className="grid gap-2">
                  {['draft', 'scheduled', 'expired'].includes(review.status) && (
                    <ActionButton review={review} action="send" icon={Send} variant="default" onAction={onAction} />
                  )}
                  {!['skipped', 'expired'].includes(review.status) && (
                    <ActionButton review={review} action="submit_rating" icon={Star} onAction={onAction} />
                  )}
                  {review.status !== 'public_review_requested' && (
                    <ActionButton review={review} action="request_public_review" icon={CheckCircle2} onAction={onAction} />
                  )}
                  {review.status !== 'negative_feedback_escalated' && (
                    <ActionButton review={review} action="escalate_negative" icon={AlertTriangle} onAction={onAction} />
                  )}
                  {!['skipped', 'expired'].includes(review.status) && (
                    <ActionButton review={review} action="skip" icon={XCircle} variant="destructive" onAction={onAction} />
                  )}
                  {!['skipped', 'expired'].includes(review.status) && (
                    <ActionButton review={review} action="expire" icon={Clock3} variant="destructive" onAction={onAction} />
                  )}
                  {['skipped', 'expired'].includes(review.status) && (
                    <ActionButton review={review} action="reopen" icon={RotateCcw} onAction={onAction} />
                  )}
                  {onDelete && (
                    <Button
                      type="button"
                      variant="outline"
                      className="h-auto justify-start gap-3 py-3 text-left border-destructive text-destructive hover:bg-destructive/5 hover:text-destructive"
                      onClick={() => {
                        if (window.confirm('Are you sure you want to delete this review request?')) {
                          onDelete(review.id);
                        }
                      }}
                    >
                      <Trash2 className="h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium text-destructive">Delete request</span>
                        <span className="mt-0.5 block text-[11px] font-normal leading-normal text-destructive/80">Soft-delete this review request from AWA.</span>
                      </span>
                    </Button>
                  )}
                </div>
              </section>

              <section aria-labelledby="review-timeline-heading">
                <h4 id="review-timeline-heading" className="mb-2 text-sm font-semibold">Timeline</h4>
                <div className="space-y-2 rounded-md border p-3">
                  {eventsLoading ? (
                    <p className="text-sm text-muted-foreground">Loading events...</p>
                  ) : events && events.length > 0 ? (
                    events.map((event) => (
                      <div key={event.id} className="flex items-start gap-2 rounded-md bg-muted/40 p-2 text-[12px]">
                        <Clock3 className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                        <div>
                          <p className="font-medium capitalize">{event.event_type.replace(/_/g, ' ')}</p>
                          <p className="text-muted-foreground">{formatReviewDateTime(event.created_at)} by {event.actor_type.replace(/_/g, ' ')}</p>
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-muted-foreground">No events yet.</p>
                  )}
                </div>
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
