import { BookOpenCheck, Globe2, Plus, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { KNOWLEDGE_EMPTY_COPY } from '@/features/knowledge/constants';

interface KnowledgeEmptyStateProps {
  hasSearch: boolean;
  onCreateManual: () => void;
  onImportWebsite: () => void;
  onUploadDocument: () => void;
  onClearSearch: () => void;
}

export function KnowledgeEmptyState({
  hasSearch,
  onCreateManual,
  onImportWebsite,
  onUploadDocument,
  onClearSearch,
}: KnowledgeEmptyStateProps) {
  return (
    <div className="flex min-h-[320px] flex-col items-center justify-center rounded-md border border-dashed px-6 py-8 text-center">
      <BookOpenCheck className="h-10 w-10 text-muted-foreground/40" aria-hidden="true" />
      <p className="mt-3 text-[14px] font-medium text-foreground">
        {hasSearch ? 'No matching knowledge found' : 'No knowledge yet'}
      </p>
      <p className="mt-1 max-w-md text-[13px] leading-6 text-muted-foreground">
        {hasSearch ? 'No matching knowledge found. Try a different keyword or clear the search.' : KNOWLEDGE_EMPTY_COPY}
      </p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {hasSearch ? (
          <Button type="button" variant="outline" onClick={onClearSearch}>
            Clear search
          </Button>
        ) : (
          <>
            <Button type="button" onClick={onCreateManual}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Create manual answer
            </Button>
            <Button type="button" variant="outline" onClick={onImportWebsite}>
              <Globe2 className="h-3.5 w-3.5" aria-hidden="true" />
              Import website
            </Button>
            <Button type="button" variant="outline" onClick={onUploadDocument}>
              <Upload className="h-3.5 w-3.5" aria-hidden="true" />
              Upload document
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
