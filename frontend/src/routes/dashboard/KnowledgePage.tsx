import { useMemo, useRef, useState } from 'react';
import { KnowledgeSkeleton } from '@/components/ui/loading-states';
import { useKnowledgeEntries, useKnowledgeSourceSummary } from '@/features/knowledge/api';
import { KnowledgeCreatePanel } from '@/features/knowledge/components/KnowledgeCreatePanel';
import { KnowledgeEmptyState } from '@/features/knowledge/components/KnowledgeEmptyState';
import { KnowledgeErrorState } from '@/features/knowledge/components/KnowledgeErrorState';
import { KnowledgeFilters } from '@/features/knowledge/components/KnowledgeFilters';
import { KnowledgeLibrary } from '@/features/knowledge/components/KnowledgeLibrary';
import { KnowledgeMetricsCards } from '@/features/knowledge/components/KnowledgeMetricsCards';
import { KnowledgePageHeader } from '@/features/knowledge/components/KnowledgePageHeader';
import { KnowledgeSafetyNotice } from '@/features/knowledge/components/KnowledgeSafetyNotice';
import { KnowledgeSourceSummaryCards } from '@/features/knowledge/components/KnowledgeSourceSummaryCards';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import {
  DOCUMENT_UPLOAD_TITLE_ID,
  MANUAL_KNOWLEDGE_TITLE_ID,
  WEBSITE_IMPORT_URL_ID,
} from '@/features/knowledge/components/KnowledgeCreatePanel';
import { getKnowledgeMetrics } from '@/features/knowledge/utils';

function focusField(id: string) {
  const input = document.getElementById(id);
  if (input) {
    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => input.focus(), 300);
  }
}

export function KnowledgePage() {
  const [search, setSearch] = useState('');
  const createPanelRef = useRef<HTMLDivElement | null>(null);
  const { data: toolsData, isLoading: isToolsLoading } = useTenantTools();
  const entriesQuery = useKnowledgeEntries(search);
  const sourcesQuery = useKnowledgeSourceSummary();
  const entries = useMemo(() => entriesQuery.data ?? [], [entriesQuery.data]);
  const sourceSummary = sourcesQuery.data ?? [];
  const metrics = getKnowledgeMetrics(entries, sourceSummary);
  const hasSearch = search.trim().length > 0;
  const faqAccess = getModuleEntitlement(toolsData, 'faq');

  const focusCreateField = (id: string) => {
    if (!faqAccess.allowedByPlan) {
      return;
    }
    createPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(() => focusField(id), 250);
  };

  return (
    <div className="space-y-5">
      <KnowledgePageHeader
        loadedCount={entries.length}
        isRefreshing={entriesQuery.isFetching}
        onRefresh={() => entriesQuery.refetch()}
      />

      <KnowledgeMetricsCards {...metrics} />
      <KnowledgeSafetyNotice activeCount={metrics.active} disabledCount={metrics.disabled} />

      {isToolsLoading ? (
        <KnowledgeSkeleton />
      ) : faqAccess.allowedByPlan ? (
        <div ref={createPanelRef}>
          <KnowledgeCreatePanel />
        </div>
      ) : (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-[13px] leading-relaxed text-amber-800 dark:text-amber-100">
          {faqAccess.lockedReason}
        </div>
      )}

      <KnowledgeSourceSummaryCards sources={sourceSummary} />

      <KnowledgeFilters search={search} onSearchChange={setSearch} disabled={entriesQuery.isLoading} />

      {entriesQuery.isError && (
        <KnowledgeErrorState onRetry={() => entriesQuery.refetch()} isRetrying={entriesQuery.isFetching} />
      )}

      {entriesQuery.isLoading ? (
        <KnowledgeSkeleton />
      ) : entries.length ? (
        faqAccess.allowedByPlan ? <KnowledgeLibrary entries={entries} /> : null
      ) : (
        <KnowledgeEmptyState
          hasSearch={hasSearch}
          onCreateManual={() => faqAccess.allowedByPlan && focusCreateField(MANUAL_KNOWLEDGE_TITLE_ID)}
          onImportWebsite={() => faqAccess.allowedByPlan && focusCreateField(WEBSITE_IMPORT_URL_ID)}
          onUploadDocument={() => faqAccess.allowedByPlan && focusCreateField(DOCUMENT_UPLOAD_TITLE_ID)}
          onClearSearch={() => setSearch('')}
        />
      )}
    </div>
  );
}
