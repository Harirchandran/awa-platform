import { useSearchParams } from 'react-router-dom';
import { useCustomersList } from '@/features/customers/api';
import { CustomersTable } from '@/features/customers/components/CustomersTable';
import { Input } from '@/components/ui/input';
import { AlertTriangle, Search } from 'lucide-react';
import { useDebounce } from '@/hooks/use-debounce';
import { getApiErrorMessage } from '@/lib/api-errors';
import { PageHeader } from '@/components/shared/PageHeader';

export function CustomersPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const searchTerm = searchParams.get('q') || '';
  const debouncedSearch = useDebounce(searchTerm, 500);

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

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isError, error, isFetching } = useCustomersList(debouncedSearch);
  const customers = data?.pages.flatMap(page => page.items) || [];

  return (
    <div className="flex-1 space-y-5 flex flex-col h-full">
      <PageHeader
        title="Customers"
        description="Customer records are created from chat and contact events, then connect activity across chats and workflows."
        actions={
          <div className="relative w-full sm:w-auto">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-[14px] w-[14px] text-muted-foreground" />
            <Input
              placeholder="Search name or phone..."
              className="h-9 w-full pl-8 text-[13px] sm:h-8 sm:w-[240px]"
              value={searchTerm}
              onChange={(e) => handleSearchChange(e.target.value)}
            />
          </div>
        }
      />
      <div className="flex-1">
        {isError ? (
          <div role="alert" className="flex min-h-[320px] items-center justify-center rounded-md border border-destructive/40 bg-destructive/5">
            <div className="max-w-md px-6 text-center text-destructive">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-destructive/40 bg-background">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <h3 className="mt-4 text-[15px] font-semibold">Customers unavailable</h3>
              <p className="mt-2 text-[13px] leading-relaxed">
                {getApiErrorMessage(error, 'Customer data could not be loaded.')}
              </p>
            </div>
          </div>
        ) : (
          <CustomersTable customers={customers} isLoading={isLoading} isFetching={isFetching} hasNextPage={hasNextPage} fetchNextPage={fetchNextPage} isFetchingNextPage={isFetchingNextPage} />
        )}
      </div>
    </div>
  );
}