import { useReviewMetrics, type ReviewRequest } from '../api';

export function ReviewMetricsCards({ reviews }: { reviews?: ReviewRequest[] }) {
  const { data: metrics = [], isLoading } = useReviewMetrics();
  void reviews;

  if (isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Review overview counts">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="rounded-md border bg-card p-4 animate-pulse">
            <div className="h-3 w-24 bg-muted-foreground/10 rounded"></div>
            <div className="mt-2 h-6 w-12 bg-muted-foreground/10 rounded"></div>
            <div className="mt-2 h-3 w-32 bg-muted-foreground/10 rounded"></div>
          </div>
        ))}
      </div>
    );
  }

  const total = metrics.reduce((sum, m) => sum + m.count, 0);
  const pending = metrics.reduce((sum, m) => ['draft', 'scheduled', 'sent'].includes(m.status) ? sum + m.count : sum, 0);

  const totalRated = metrics.reduce((sum, m) => sum + m.rated_count, 0);
  const totalRatingSum = metrics.reduce((sum, m) => sum + (m.average_rating || 0) * m.rated_count, 0);
  const average = totalRated > 0 ? (totalRatingSum / totalRated).toFixed(1) : '0.0';

  const lowRatings = metrics.reduce((sum, m) => sum + m.low_ratings_count, 0);
  const escalated = metrics.reduce((sum, m) => m.status === 'negative_feedback_escalated' ? sum + m.count : sum, 0);

  const cards = [
    { label: 'Total requests', value: total, helper: 'Across all pages' },
    { label: 'Pending requests', value: pending, helper: 'Draft, scheduled, or sent' },
    { label: 'Average rating', value: average, helper: 'All rated requests' },
    { label: 'Low ratings', value: lowRatings + escalated, helper: 'Needs private follow-up' },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Review overview counts">
      {cards.map((card) => (
        <div key={card.label} className="rounded-md border bg-card p-4">
          <p className="text-[12px] font-medium text-muted-foreground">{card.label}</p>
          <p className="mt-2 text-[24px] font-semibold leading-none text-foreground">{card.value}</p>
          <p className="mt-2 text-[12px] leading-normal text-muted-foreground">{card.helper}</p>
        </div>
      ))}
    </div>
  );
}
