import { useSearchParams } from 'react-router-dom';
import { ConversationList } from '@/features/conversations/components/ConversationList';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import { PageHeader } from '@/components/shared/PageHeader';

export function ConversationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const searchTerm = searchParams.get('q') || '';
  const statusFilter = searchParams.get('status') || 'all';

  const handleSearchChange = (val: string) => {
    setSearchParams(
      (prev) => {
        if (val) {
          prev.set('q', val);
        } else {
          prev.delete('q');
        }
        return prev;
      },
      { replace: true }
    );
  };

  const handleStatusChange = (val: string) => {
    setSearchParams(
      (prev) => {
        if (val && val !== 'all') {
          prev.set('status', val);
        } else {
          prev.delete('status');
        }
        return prev;
      },
      { replace: true }
    );
  };

  return (
    <div className="flex-1 space-y-5 flex flex-col h-full">
      <PageHeader
        title="Conversations"
        description="All customer chat threads from AWA Chat and WhatsApp appear here. Needs Team Reply shows only paused handoffs."
        actions={
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
            <SearchInputWithClear
              value={searchTerm}
              onChange={(e) => handleSearchChange(e.target.value)}
              onClear={() => handleSearchChange('')}
              placeholder="Search..."
              className="w-full sm:w-[200px]"
            />
            <Select value={statusFilter} onValueChange={handleStatusChange}>
              <SelectTrigger className="h-9 w-full text-[13px] sm:h-8 sm:w-[120px]">
                <SelectValue placeholder="All" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="closed">Closed</SelectItem>
                <SelectItem value="escalated">Needs reply</SelectItem>
              </SelectContent>
            </Select>
          </div>
        }
      />
      <div className="flex-1 min-h-0">
        <ConversationList searchTerm={searchTerm} statusFilter={statusFilter} />
      </div>
    </div>
  );
}