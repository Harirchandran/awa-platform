import type { KnowledgeSourceSummary } from '@/features/knowledge/api';
import { sourceLabel, formatKnowledgeAge } from '@/features/knowledge/utils';

interface KnowledgeSourceSummaryCardsProps {
  sources: KnowledgeSourceSummary[];
}

export function KnowledgeSourceSummaryCards({ sources }: KnowledgeSourceSummaryCardsProps) {
  if (!sources.length) return null;

  return (
    <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-label="Knowledge sources">
      {sources.map((source) => (
        <div key={source.source} className="rounded-md border bg-card px-3 py-3">
          <p className="text-[12px] font-medium text-muted-foreground">{sourceLabel(source.source)}</p>
          <p className="mt-1 text-[18px] font-semibold text-foreground">{source.enabled_count}</p>
          <p className="text-[11px] text-muted-foreground">{source.disabled_count} disabled</p>
          {source.last_created_at && (
            <p className="mt-1 text-[11px] text-muted-foreground">Last added {formatKnowledgeAge(source.last_created_at)}</p>
          )}
        </div>
      ))}
    </section>
  );
}
