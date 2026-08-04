import { AlarmClock } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import { CHANNEL_COPY, STATUS_COPY, TRIGGER_COPY } from '../constants';

interface ReminderQueueFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  statusFilter: string;
  onStatusChange: (value: string) => void;
  triggerFilter: string;
  onTriggerChange: (value: string) => void;
  channelFilter: string;
  onChannelChange: (value: string) => void;
}

/**
 * Filter bar for the scheduled messages queue:
 * text search, status, trigger, and channel selects.
 */
export function ReminderQueueFilters({
  search,
  onSearchChange,
  statusFilter,
  onStatusChange,
  triggerFilter,
  onTriggerChange,
  channelFilter,
  onChannelChange,
}: ReminderQueueFiltersProps) {
  return (
    <div
      className="flex flex-col justify-between gap-3 rounded-md border bg-card p-3 md:flex-row md:items-center"
      role="search"
      aria-label="Filter scheduled follow-up messages"
    >
      <div className="flex items-center gap-2">
        <AlarmClock className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <p className="text-[13px] font-medium text-foreground">Scheduled follow-ups</p>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <SearchInputWithClear
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          onClear={() => onSearchChange('')}
          placeholder="Search message or customer"
          className="w-full sm:w-[240px]"
          aria-label="Search scheduled follow-up messages"
        />

        <Select value={statusFilter} onValueChange={onStatusChange}>
          <SelectTrigger className="sm:w-[138px]" aria-label="Filter by status">
            <SelectValue placeholder="All status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All status</SelectItem>
            {Object.entries(STATUS_COPY).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={triggerFilter} onValueChange={onTriggerChange}>
          <SelectTrigger className="sm:w-[170px]" aria-label="Filter by trigger">
            <SelectValue placeholder="All triggers" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All triggers</SelectItem>
            {Object.entries(TRIGGER_COPY).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={channelFilter} onValueChange={onChannelChange}>
          <SelectTrigger className="sm:w-[132px]" aria-label="Filter by channel">
            <SelectValue placeholder="All channels" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All channels</SelectItem>
            {Object.entries(CHANNEL_COPY).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}
