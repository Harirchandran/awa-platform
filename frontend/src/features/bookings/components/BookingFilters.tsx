import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

interface BookingFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
  disabled?: boolean;
}

export function BookingFilters({
  search,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  disabled = false,
}: BookingFiltersProps) {
  return (
    <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
      <SearchInputWithClear
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        onClear={() => onSearchChange('')}
        placeholder="Search by Name, Phone, or Email..."
        aria-label="Search bookings"
        disabled={disabled}
        className="max-w-none sm:max-w-[240px]"
      />
      <Select value={statusFilter} onValueChange={onStatusFilterChange} disabled={disabled}>
        <SelectTrigger className="h-9 w-full text-[13px] sm:w-[150px]">
          <SelectValue placeholder="All statuses" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All statuses</SelectItem>
          <SelectItem value="confirmed">Confirmed</SelectItem>
          <SelectItem value="cancelled">Cancelled</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}
