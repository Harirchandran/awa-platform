import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { REVIEW_SOURCE_COPY, REVIEW_STATUS_COPY } from '../constants';

interface ReviewFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
  sourceFilter: string;
  onSourceFilterChange: (value: string) => void;
  ratingFilter: string;
  onRatingFilterChange: (value: string) => void;
  disabled?: boolean;
}

export function ReviewFilters({
  search,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  sourceFilter,
  onSourceFilterChange,
  ratingFilter,
  onRatingFilterChange,
  disabled = false,
}: ReviewFiltersProps) {
  return (
    <div className="flex flex-col gap-2 xl:flex-row xl:items-center">
      <SearchInputWithClear
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        onClear={() => onSearchChange('')}
        placeholder="Search feedback or customer"
        aria-label="Search reviews by feedback or customer"
        disabled={disabled}
        className="max-w-none xl:w-[250px]"
      />
      <Select value={statusFilter} onValueChange={onStatusFilterChange} disabled={disabled}>
        <SelectTrigger className="h-9 text-[13px] xl:w-[180px]"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All status</SelectItem>
          {Object.entries(REVIEW_STATUS_COPY).map(([value, label]) => (
            <SelectItem key={value} value={value}>{label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={sourceFilter} onValueChange={onSourceFilterChange} disabled={disabled}>
        <SelectTrigger className="h-9 text-[13px] xl:w-[140px]"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All sources</SelectItem>
          {Object.entries(REVIEW_SOURCE_COPY).map(([value, label]) => (
            <SelectItem key={value} value={value}>{label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={ratingFilter} onValueChange={onRatingFilterChange} disabled={disabled}>
        <SelectTrigger className="h-9 text-[13px] xl:w-[125px]"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All ratings</SelectItem>
          {[5, 4, 3, 2, 1].map((rating) => (
            <SelectItem key={rating} value={String(rating)}>{rating} star</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
