import { useState, useEffect, useCallback } from 'react';
import { fetchAuditLogs, type AuditLogEntry, type AuditLogFilters } from '@/features/audit/api';
import { useTenantsList } from '@/features/tenants/api';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollText, ShieldAlert, Filter, Clock, User, Zap, RefreshCw, Calendar, ChevronDown } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { format } from 'date-fns';

const ACTION_COLORS: Record<string, string> = {
  'feature_flag_toggled': 'bg-amber-500/10 text-amber-500 border-amber-500/20',
  'UPDATE_GLOBAL_PROMPT': 'bg-indigo-500/10 text-indigo-500 border-indigo-500/20',
  'UPDATE_PROMPT': 'bg-indigo-500/10 text-indigo-500 border-indigo-500/20',
  'consent_granted': 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20',
  'consent_withdrawn': 'bg-destructive/10 text-destructive border-destructive/20',
  'data_erased': 'bg-destructive/10 text-destructive border-destructive/20',
  'super_admin_tenant_created': 'bg-sky-500/10 text-sky-500 border-sky-500/20',
  'tenant_tool_controls_updated': 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  'super_admin_login': 'bg-rose-500/10 text-rose-500 border-rose-500/20',
};

export function AuditPage() {
  const [actionFilter, setActionFilter] = useState<string>('');
  const [tenantFilter, setTenantFilter] = useState<string>('');
  const [dateFrom, setDateFrom] = useState<string>('');
  const [dateTo, setDateTo] = useState<string>('');

  // Accumulated log state — reset on filter change, appended on Load More
  const [allLogs, setAllLogs] = useState<AuditLogEntry[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isError, setIsError] = useState(false);

  const { data: tenants = [] } = useTenantsList();

  const activeFilters: AuditLogFilters = {
    action_type: actionFilter || undefined,
    tenant_id: tenantFilter || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    isGlobal: true,
  };

  // Fetch first page whenever filters change
  const fetchFirstPage = useCallback(async () => {
    setIsLoading(true);
    setIsError(false);
    setAllLogs([]);
    setNextCursor(null);
    try {
      const result = await fetchAuditLogs(activeFilters, null);
      setAllLogs(result.items);
      setNextCursor(result.next_cursor);
    } catch {
      setIsError(true);
    } finally {
      setIsLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionFilter, tenantFilter, dateFrom, dateTo]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchFirstPage();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [fetchFirstPage]);

  // Append next page without replacing existing rows
  const loadMore = async () => {
    if (!nextCursor || isLoadingMore) return;
    setIsLoadingMore(true);
    try {
      const result = await fetchAuditLogs(activeFilters, nextCursor);
      setAllLogs((prev) => [...prev, ...result.items]);
      setNextCursor(result.next_cursor);
    } catch {
      // non-fatal — keep existing rows, let user retry
    } finally {
      setIsLoadingMore(false);
    }
  };

  const handleResetFilters = () => {
    setActionFilter('');
    setTenantFilter('');
    setDateFrom('');
    setDateTo('');
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Platform-Wide Audit Logs</h1>
          <p className="text-muted-foreground mt-1">
            Global immutable audit trail of privileged operations across all tenants.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void fetchFirstPage()}
          disabled={isLoading}
          className="self-start sm:self-auto"
        >
          <RefreshCw className={`w-4 h-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Filters Panel */}
      <div className="bg-card border border-border rounded-lg p-4 space-y-4">
        <div className="flex items-center text-sm font-medium text-foreground gap-2">
          <Filter className="w-4 h-4 text-indigo-500" />
          Filter Audit Trail
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
          {/* Action Filter */}
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Action Type</label>
            <Select value={actionFilter} onValueChange={(v) => setActionFilter(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="All actions" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All actions</SelectItem>
                <SelectItem value="super_admin_login">Super Admin Login</SelectItem>
                <SelectItem value="feature_flag_toggled">Feature Flag Toggle</SelectItem>
                <SelectItem value="UPDATE_GLOBAL_PROMPT">Prompt Update</SelectItem>
                <SelectItem value="consent_granted">Consent Granted</SelectItem>
                <SelectItem value="consent_withdrawn">Consent Withdrawn</SelectItem>
                <SelectItem value="data_erased">Data Erasure</SelectItem>
                <SelectItem value="super_admin_tenant_created">Tenant Creation</SelectItem>
                <SelectItem value="tenant_tool_controls_updated">Tool Controls Update</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Tenant Filter */}
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Business (Tenant)</label>
            <Select value={tenantFilter} onValueChange={(v) => setTenantFilter(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="All tenants" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All tenants</SelectItem>
                {tenants.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Date From */}
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5" /> Date From
            </label>
            <Input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="w-full"
            />
          </div>

          {/* Date To */}
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground flex items-center gap-1">
              <Calendar className="w-3.5 h-3.5" /> Date To
            </label>
            <Input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="w-full"
            />
          </div>
        </div>

        {(actionFilter || tenantFilter || dateFrom || dateTo) && (
          <div className="flex justify-end pt-2">
            <Button variant="ghost" size="sm" onClick={handleResetFilters} className="text-xs h-7 px-2">
              Clear Filters
            </Button>
          </div>
        )}
      </div>

      {isError && (
        <div className="bg-destructive/10 border border-destructive/20 p-4 rounded-lg flex items-start text-destructive">
          <ShieldAlert className="w-5 h-5 mr-3 mt-0.5 shrink-0" />
          <div>
            <h3 className="font-semibold text-sm">Failed to fetch audit logs</h3>
            <p className="text-xs mt-1 opacity-90">The audit subsystem may be unreachable or permission was denied.</p>
            <Button variant="outline" size="sm" onClick={() => void fetchFirstPage()} className="mt-3 border-destructive/30 hover:bg-destructive/10 text-destructive h-7 text-xs px-3">
              Retry Connection
            </Button>
          </div>
        </div>
      )}

      <div className="rounded-md border border-border bg-card overflow-hidden">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="w-[150px]">
                <div className="flex items-center"><Clock className="w-3.5 h-3.5 mr-1.5" /> Timestamp</div>
              </TableHead>
              <TableHead className="w-[180px]">
                <div className="flex items-center"><Zap className="w-3.5 h-3.5 mr-1.5" /> Action</div>
              </TableHead>
              <TableHead className="w-[180px]">Tenant</TableHead>
              <TableHead className="w-[120px]">Resource</TableHead>
              <TableHead className="w-[180px]">
                <div className="flex items-center"><User className="w-3.5 h-3.5 mr-1.5" /> Actor</div>
              </TableHead>
              <TableHead>Details (Redacted)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array(6).fill(0).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-5 w-28" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-16" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                </TableRow>
              ))
            ) : allLogs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-32 text-center text-muted-foreground">
                  <ScrollText className="w-8 h-8 mx-auto mb-2 opacity-30" />
                  No matching audit events found.
                </TableCell>
              </TableRow>
            ) : (
              allLogs.map((log) => (
                <TableRow key={log.id} className="hover:bg-muted/30 transition-colors">
                  <TableCell className="text-xs text-muted-foreground font-mono">
                    {format(new Date(log.created_at), 'MMM d, HH:mm:ss')}
                  </TableCell>
                  <TableCell>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium border ${
                      ACTION_COLORS[log.action] || 'bg-muted text-muted-foreground border-border'
                    }`}>
                      {log.action}
                    </span>
                  </TableCell>
                  <TableCell className="text-sm font-medium">
                    {log.tenant_name || <span className="text-muted-foreground text-xs italic">System</span>}
                  </TableCell>
                  <TableCell className="text-xs">
                    <span className="font-mono text-muted-foreground">{log.resource_type}</span>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground truncate max-w-[150px]">
                    {log.actor_email || `${log.actor_id?.slice(0, 8)}...`}
                  </TableCell>
                  <TableCell className="text-xs">
                    {log.details ? (
                      <div className="max-w-[400px] overflow-hidden truncate hover:text-clip hover:overflow-visible hover:whitespace-pre-wrap transition-all duration-200">
                        <pre className="text-[10px] font-mono whitespace-pre-wrap bg-muted/40 p-2 rounded border border-border/60 max-h-[150px] overflow-y-auto">
                          {JSON.stringify(log.details, null, 2)}
                        </pre>
                      </div>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Load More — only shown when the server has more pages */}
      {nextCursor && !isLoading && (
        <div className="flex justify-center pt-1">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void loadMore()}
            disabled={isLoadingMore}
            className="gap-2 text-xs"
          >
            <ChevronDown className={`w-3.5 h-3.5 ${isLoadingMore ? 'animate-bounce' : ''}`} />
            {isLoadingMore ? 'Loading…' : `Load More (${allLogs.length} loaded)`}
          </Button>
        </div>
      )}
    </div>
  );
}
