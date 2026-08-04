import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';

interface KnowledgeFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  disabled?: boolean;
}

export function KnowledgeFilters({ search, onSearchChange, disabled = false }: KnowledgeFiltersProps) {
  return (
    <div className="rounded-md border bg-card p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-[15px] font-semibold text-foreground">Knowledge library</h2>
          <p className="mt-1 text-[12px] text-muted-foreground">Search saved answers, imported pages, and uploaded document text.</p>
        </div>
        <SearchInputWithClear
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          onClear={() => onSearchChange('')}
          placeholder="Search saved answers"
          aria-label="Search saved knowledge"
          disabled={disabled}
          className="max-w-none sm:max-w-[320px]"
        />
      </div>
    </div>
  );
}
