import type { ReviewRequest } from '../api';
import { REVIEW_SOURCE_COPY } from '../constants';
import { formatReviewDateTime } from '../utils';
import { ReviewRatingBadge, ReviewStatusBadge } from './ReviewBadges';

interface ReviewCardProps {
  review: ReviewRequest;
  onSelect: (review: ReviewRequest) => void;
}

export function ReviewCard({ review, onSelect }: ReviewCardProps) {
  const customerName = review.customer_name || 'Unlinked customer';

  return (
    <button
      type="button"
      onClick={() => onSelect(review)}
      className="w-full rounded-md border bg-card p-4 text-left transition-all duration-200 hover:-translate-y-[2px] hover:shadow-md hover:border-primary/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      aria-label={`Open review request for ${customerName}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[14px] font-semibold">{customerName}</p>
          <p className="mt-0.5 text-[12px] text-muted-foreground">{review.customer_phone || 'No phone'}</p>
        </div>
        <ReviewRatingBadge rating={review.rating} />
      </div>
      <p className="mt-3 line-clamp-2 text-[13px] leading-normal text-muted-foreground">
        {review.feedback_text || 'Awaiting customer response.'}
      </p>
      <div className="mt-4 grid grid-cols-2 gap-3 text-[12px]">
        <div>
          <p className="text-muted-foreground">Status</p>
          <div className="mt-1"><ReviewStatusBadge status={review.status} /></div>
        </div>
        <div>
          <p className="text-muted-foreground">Source</p>
          <p className="mt-1 font-medium text-foreground">{REVIEW_SOURCE_COPY[review.source]}</p>
        </div>
        <div className="col-span-2">
          <p className="text-muted-foreground">Created</p>
          <p className="mt-1 font-medium text-foreground">{formatReviewDateTime(review.created_at)}</p>
        </div>
      </div>
      <p className="mt-4 text-[12px] font-medium text-primary">View review details</p>
    </button>
  );
}
