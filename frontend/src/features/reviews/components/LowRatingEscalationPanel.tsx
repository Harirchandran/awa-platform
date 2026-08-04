import { AlertTriangle } from 'lucide-react';
import type { ReviewRequest } from '../api';
import { getReviewMetrics } from '../utils';

interface LowRatingEscalationPanelProps {
  reviews: ReviewRequest[];
  escalationEnabled: boolean;
  positiveThreshold: string;
}

export function LowRatingEscalationPanel({
  reviews,
  escalationEnabled,
  positiveThreshold,
}: LowRatingEscalationPanelProps) {
  const metrics = getReviewMetrics(reviews);

  return (
    <div className="rounded-md border bg-card p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border bg-background">
          <AlertTriangle className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold">Low-rating follow-up</h3>
          <p className="mt-1 text-[12px] leading-normal text-muted-foreground">
            Low ratings stay private for team follow-up. Public review links are requested only when the workflow asks for a public review.
          </p>
        </div>
      </div>
      <div className="mt-4 grid gap-2 text-[12px]">
        <div className="flex justify-between gap-4 rounded-md bg-muted/50 px-3 py-2">
          <span className="text-muted-foreground">Handoff automation</span>
          <span className="font-medium">{escalationEnabled ? 'Enabled' : 'Disabled'}</span>
        </div>
        <div className="flex justify-between gap-4 rounded-md bg-muted/50 px-3 py-2">
          <span className="text-muted-foreground">Positive threshold</span>
          <span className="font-medium">{positiveThreshold || '4'} stars and above</span>
        </div>
        <div className="flex justify-between gap-4 rounded-md bg-muted/50 px-3 py-2">
          <span className="text-muted-foreground">Low ratings needing reply</span>
          <span className="font-medium">{metrics.lowRatings + metrics.escalated}</span>
        </div>
      </div>
    </div>
  );
}
