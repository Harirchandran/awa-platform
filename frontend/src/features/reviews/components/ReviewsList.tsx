import { Loader2 } from 'lucide-react';
import type { ReviewRequest } from '../api';
import { REVIEW_SOURCE_COPY } from '../constants';
import { formatReviewDateTime } from '../utils';
import { ResponsiveDataList } from '@/components/shared/ResponsiveDataList';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { TableSkeleton } from '@/components/ui/loading-states';
import { ReviewRatingBadge, ReviewStatusBadge } from './ReviewBadges';
import { ReviewCard } from './ReviewCard';
import { ReviewEmptyState } from './ReviewEmptyState';
import { ReviewErrorState } from './ReviewErrorState';

import { cn } from '@/lib/utils';

interface ReviewsListProps {
  reviews: ReviewRequest[];
  isLoading: boolean;
  isFetching?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  onCreate: () => void;
  onSelectReview: (review: ReviewRequest) => void;
  hasNextPage?: boolean;
  fetchNextPage: () => void;
  isFetchingNextPage: boolean;
}

export function ReviewsList({
  reviews,
  isLoading,
  isFetching = false,
  isError = false,
  onRetry,
  onCreate,
  onSelectReview,
  hasNextPage,
  fetchNextPage,
  isFetchingNextPage,
}: ReviewsListProps) {
  if (isLoading) {
    return <TableSkeleton columns={6} rows={6} />;
  }

  if (isError) {
    return <ReviewErrorState onRetry={onRetry} />;
  }

  if (reviews.length === 0) {
    return <ReviewEmptyState onCreate={onCreate} />;
  }


  const desktop = (
    <div className="overflow-hidden rounded-md border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Customer</TableHead>
            <TableHead>Feedback</TableHead>
            <TableHead>Rating</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Created</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {reviews.map((review) => (
            <TableRow
              key={review.id}
              className="hover:bg-muted/50"
            >
              <TableCell>
                <button
                  type="button"
                  onClick={() => onSelectReview(review)}
                  className="text-[13px] font-medium text-left hover:underline focus:outline-none focus:underline"
                >
                  {review.customer_name || 'Unlinked customer'}
                </button>
                <p className="text-xs text-muted-foreground">{review.customer_phone || 'No phone'}</p>
              </TableCell>
              <TableCell>
                <p className="line-clamp-2 max-w-[320px] text-[13px]">{review.feedback_text || 'Awaiting response'}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">Request {review.id.split('-')[0]}</p>
              </TableCell>
              <TableCell><ReviewRatingBadge rating={review.rating} /></TableCell>
              <TableCell><ReviewStatusBadge status={review.status} /></TableCell>
              <TableCell className="text-[13px]">{REVIEW_SOURCE_COPY[review.source]}</TableCell>
              <TableCell className="text-sm text-muted-foreground">{formatReviewDateTime(review.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );

  const mobile = (
    <div className="space-y-3">
      {reviews.map((review) => (
        <ReviewCard key={review.id} review={review} onSelect={onSelectReview} />
      ))}
    </div>
  );

  return (
    <div className={cn("space-y-4 transition-opacity duration-200", isFetching && !isLoading && "opacity-50 pointer-events-none")}>
      <ResponsiveDataList desktop={desktop} mobile={mobile} />
      {hasNextPage && (
        <div className="flex justify-center pt-2">
          <Button type="button" variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
            {isFetchingNextPage && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Load More Reviews
          </Button>
        </div>
      )}
    </div>
  );
}
