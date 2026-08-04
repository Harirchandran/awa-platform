import { Star } from 'lucide-react';
import { StatusBadge } from '@/components/shared/StatusBadge';
import type { ReviewStatus } from '../api';
import { REVIEW_STATUS_COPY } from '../constants';

export function ReviewStatusBadge({ status }: { status: ReviewStatus }) {
  const tone = (() => {
    switch (status) {
      case 'draft':
        return 'neutral';
      case 'scheduled':
        return 'warning';
      case 'sent':
      case 'responded':
        return 'info';
      case 'public_review_requested':
        return 'success';
      case 'negative_feedback_escalated':
        return 'danger';
      case 'skipped':
      case 'expired':
        return 'muted';
      default:
        return 'neutral';
    }
  })();

  return (
    <StatusBadge
      label={REVIEW_STATUS_COPY[status] ?? status.replace(/_/g, ' ')}
      tone={tone}
      srLabel={`Review status: ${REVIEW_STATUS_COPY[status] ?? status}`}
    />
  );
}

export function ReviewRatingBadge({ rating }: { rating?: number | null }) {
  if (!rating) {
    return <StatusBadge label="No rating" tone="muted" srLabel="No customer rating submitted" />;
  }

  const tone = rating <= 2 ? 'danger' : rating === 3 ? 'warning' : rating === 4 ? 'info' : 'success';

  return (
    <StatusBadge
      label={`${rating}/5`}
      tone={tone}
      icon={Star}
      srLabel={`Customer rating ${rating} out of 5`}
    />
  );
}
