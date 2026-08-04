import { RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/shared/PageHeader';
import { StatusBadge } from '@/components/shared/StatusBadge';

interface KnowledgePageHeaderProps {
  loadedCount: number;
  isRefreshing: boolean;
  onRefresh: () => void;
}

export function KnowledgePageHeader({ loadedCount, isRefreshing, onRefresh }: KnowledgePageHeaderProps) {
  return (
    <PageHeader
      title="AI Knowledge"
      description="Add business answers, FAQs, policies, and source content so AWA can respond accurately."
      badge={<StatusBadge label={`${loadedCount} loaded`} tone="info" srLabel="Count reflects the currently loaded page" />}
      actions={
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onRefresh}
          disabled={isRefreshing}
          aria-label="Refresh knowledge"
        >
          <RefreshCcw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
          Refresh
        </Button>
      }
    />
  );
}
