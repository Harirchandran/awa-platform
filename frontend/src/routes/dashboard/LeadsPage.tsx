import { useState } from 'react';
import { format } from 'date-fns';
import toast from 'react-hot-toast';
import { useLeadsList, useUpdateLeadStatus } from '@/features/leads/api';
import type { Lead, LeadStatus } from '@/features/leads/api';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { TableSkeleton } from '@/components/ui/loading-states';
import { AlertTriangle, Loader2, Megaphone, ArrowRight, UserCheck, PhoneCall, XCircle, TrendingUp } from 'lucide-react';
import { getApiErrorMessage } from '@/lib/api-errors';
import { ResponsiveDataList } from '@/components/shared/ResponsiveDataList';
import { PageHeader } from '@/components/shared/PageHeader';
import { Link, useSearchParams } from 'react-router';
import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All Statuses' },
  { value: 'new', label: 'New' },
  { value: 'contacted', label: 'Contacted' },
  { value: 'converted', label: 'Converted' },
  { value: 'lost', label: 'Lost' },
];

function getStatusClasses(status: string): string {
  switch (status) {
    case 'new': return 'bg-blue-500/10 text-blue-500';
    case 'contacted': return 'bg-amber-500/10 text-amber-600';
    case 'converted': return 'bg-emerald-500/10 text-emerald-500';
    case 'lost': return 'bg-red-500/10 text-red-500';
    default: return 'bg-muted text-muted-foreground';
  }
}

function getStatusIcon(status: string) {
  switch (status) {
    case 'new': return <Megaphone className="h-3 w-3" />;
    case 'contacted': return <PhoneCall className="h-3 w-3" />;
    case 'converted': return <TrendingUp className="h-3 w-3" />;
    case 'lost': return <XCircle className="h-3 w-3" />;
    default: return null;
  }
}

function formatDate(dateStr: string): string {
  try {
    return format(new Date(dateStr), 'MMM d, yyyy');
  } catch {
    return '—';
  }
}

function LeadDetailsSheet({
  lead,
  open,
  onClose,
  canMutate,
}: {
  lead: Lead | null;
  open: boolean;
  onClose: () => void;
  canMutate: boolean;
}) {
  const updateMutation = useUpdateLeadStatus();
  const [pendingStatus, setPendingStatus] = useState<{ status: string; label: string } | null>(null);

  if (!lead) return null;

  const ALL_STATUSES: { status: LeadStatus; label: string }[] = [
    { status: 'new', label: 'Mark as New' },
    { status: 'contacted', label: 'Mark as Contacted' },
    { status: 'converted', label: 'Mark as Converted' },
    { status: 'lost', label: 'Mark as Lost' },
  ];
  const NEXT_STATUSES = ALL_STATUSES.filter((s) => s.status !== lead.status);

  const confirmStatusUpdate = (status: string) => {
    updateMutation.mutate(
      { id: lead.id, status },
      {
        onSuccess: () => {
          toast.success(`Lead marked as ${status}`);
          setPendingStatus(null);
          onClose();
        },
        onError: (err: unknown) => {
          toast.error(getApiErrorMessage(err, 'Failed to update lead status'));
          setPendingStatus(null);
        },
      }
    );
  };

  let extraData: Record<string, unknown> | null = null;
  try {
    extraData = typeof lead.metadata === 'string'
      ? JSON.parse(lead.metadata)
      : (lead.metadata as Record<string, unknown> | null);
  } catch (e) {
    console.error('Failed to parse lead metadata:', e);
  }

  const getDisplayFields = () => {
    if (!extraData) return [];
    const fields: { key: string; label: string; value: string }[] = [];

    if (extraData.preferred_contact_time) {
      fields.push({
        key: 'preferred_contact_time',
        label: 'Preferred Contact Time',
        value: String(extraData.preferred_contact_time),
      });
    }

    if (extraData.step_data && typeof extraData.step_data === 'object') {
      Object.entries(extraData.step_data as Record<string, unknown>).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') {
          fields.push({
            key: `step_${k}`,
            label: k.replace(/_/g, ' '),
            value: String(v),
          });
        }
      });
    }

    Object.entries(extraData).forEach(([k, v]) => {
      if (k !== 'preferred_contact_time' && k !== 'step_data') {
        if (v !== undefined && v !== null && typeof v !== 'object') {
          fields.push({
            key: k,
            label: k.replace(/_/g, ' '),
            value: String(v),
          });
        }
      }
    });

    return fields;
  };

  const displayFields = getDisplayFields();

  return (
    <>
      <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
        <SheetContent className="overflow-y-auto sm:max-w-md">
          <SheetHeader className="mb-6 border-b pb-4">
            <SheetTitle>Lead Details</SheetTitle>
            <SheetDescription className="font-mono text-xs">{lead.id}</SheetDescription>
          </SheetHeader>
          <div className="space-y-6">
            {/* Status */}
            <div>
              <h4 className="mb-2 text-sm font-semibold">Status</h4>
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize ${getStatusClasses(lead.status)}`}>
                {getStatusIcon(lead.status)}
                {lead.status}
              </span>
            </div>

            {/* Contact info */}
            <div>
              <h4 className="mb-2 text-sm font-semibold">Contact</h4>
              <div className="rounded-md border p-3 text-sm space-y-2">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Name</span>
                  <span className="font-medium">{lead.name || lead.customer_name || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Phone</span>
                  <span>{lead.phone || lead.customer_phone || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Email</span>
                  <span>{lead.email || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Source</span>
                  <span className="capitalize">{lead.source_channel || 'chat'}</span>
                </div>
              </div>
            </div>

            {/* Interest */}
            {lead.interest && (
              <div>
                <h4 className="mb-2 text-sm font-semibold">Interest</h4>
                <p className="rounded-md bg-muted p-3 text-sm">{lead.interest}</p>
              </div>
            )}

            {/* Extra metadata */}
            {displayFields.length > 0 && (
              <div>
                <h4 className="mb-2 text-sm font-semibold">Collected Fields</h4>
                <div className="space-y-2">
                  {displayFields.map((field) => (
                    <div key={field.key} className="rounded-md bg-muted p-2 text-sm">
                      <span className="block capitalize text-muted-foreground">{field.label}:</span>
                      <span className="font-medium">{field.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Dates */}
            <div className="rounded-md border p-3 text-sm space-y-2">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Created</span>
                <span>{formatDate(lead.created_at)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Updated</span>
                <span>{formatDate(lead.updated_at)}</span>
              </div>
            </div>

            {/* Actions */}
            <div className="space-y-2 border-t pt-4">
              {canMutate ? (
                <>
                  <h4 className="mb-2 text-sm font-semibold">Update Status</h4>
                  {NEXT_STATUSES.map(({ status, label }) => (
                    <Button
                      key={status}
                      variant={status === 'lost' ? 'destructive' : status === 'converted' ? 'default' : 'outline'}
                      className="w-full"
                      disabled={updateMutation.isPending}
                      onClick={() => setPendingStatus({ status, label })}
                    >
                      {updateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                      {label}
                    </Button>
                  ))}
                </>
              ) : (
                <p className="text-[13px] text-muted-foreground">Lead status updates are unavailable on this plan.</p>
              )}
              {lead.customer_id && (
                <Button asChild variant="ghost" className="w-full" size="sm">
                  <Link to={`/dashboard/customers`}>
                    <UserCheck className="h-3.5 w-3.5" />
                    View Customer Profile
                    <ArrowRight className="h-3.5 w-3.5 ml-auto" />
                  </Link>
                </Button>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>

      {/* Confirm dialog */}
      <Dialog open={!!pendingStatus} onOpenChange={(o) => !o && setPendingStatus(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Update lead status?</DialogTitle>
            <DialogDescription>
              This will change the lead status to <strong>{pendingStatus?.status}</strong>. You can always update it again later.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingStatus(null)} disabled={updateMutation.isPending}>
              Cancel
            </Button>
            <Button
              variant={pendingStatus?.status === 'lost' ? 'destructive' : 'default'}
              onClick={() => pendingStatus && confirmStatusUpdate(pendingStatus.status)}
              disabled={updateMutation.isPending}
            >
              {updateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {pendingStatus?.label ?? 'Confirm'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function LeadsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get('status') || 'all';
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);
  const [search, setSearch] = useState('');
  const { data: toolsData } = useTenantTools();
  const leadAccess = getModuleEntitlement(toolsData, 'lead_capture');
  const canMutateLeads = Boolean(toolsData && leadAccess.canMutateRuntimeData);

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

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    error,
  } = useLeadsList(statusFilter !== 'all' ? statusFilter : undefined, search);

  const leads: Lead[] = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex h-full flex-1 flex-col space-y-5">
      <PageHeader
        title="Leads"
        description="Track captured customer interest, follow-up needs, and lead details."
        actions={
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
            <SearchInputWithClear
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onClear={() => setSearch('')}
              placeholder="Search by Name, Phone, or Email..."
              aria-label="Search leads"
              className="w-full sm:w-[220px]"
            />
            <div className="w-full sm:w-[180px]">
              <Select value={statusFilter} onValueChange={handleStatusChange}>
                <SelectTrigger className="h-8 w-full text-[13px]">
                  <SelectValue placeholder="All Statuses" />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        }
      />

      {/* Content */}
      <div className="flex-1">
        {isError ? (
          <div
            role="alert"
            className="flex min-h-[320px] items-center justify-center rounded-md border border-destructive/40 bg-destructive/5"
          >
            <div className="max-w-md px-6 text-center text-destructive">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-destructive/40 bg-background">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <h3 className="mt-4 text-[15px] font-semibold">Leads unavailable</h3>
              <p className="mt-2 text-[13px] leading-relaxed">
                {getApiErrorMessage(error, 'Lead data could not be loaded. Make sure the Leads API is running.')}
              </p>
            </div>
          </div>
        ) : isLoading ? (
          <TableSkeleton columns={5} rows={6} />
        ) : leads.length === 0 ? (
          <div className="rounded-lg border bg-card/70 p-8 text-center sm:p-12">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-primary/20 bg-primary/10">
              <Megaphone className="h-5 w-5 text-primary" />
            </div>
            <h3 className="mt-4 text-[16px] font-semibold">No leads yet</h3>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
              {leadAccess.allowedByPlan
                ? 'Leads are captured when customers express interest through AWA Chat. Enable Lead Capture in your Business Setup and your first enquiries will appear here.'
                : 'Lead Capture is not available on this plan. Existing lead records remain available when present.'}
            </p>
            {leadAccess.canConfigure && toolsData && (
              <div className="mt-5 flex flex-col justify-center gap-2 sm:flex-row">
                <Button asChild size="sm" variant="outline">
                  <Link to="/dashboard/tools">
                    Configure Lead Capture
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                </Button>
              </div>
            )}
          </div>
        ) : (
          <>
            <ResponsiveDataList
              mobile={
                <div className="space-y-3">
              {leads.map((lead) => (
                <button
                  key={lead.id}
                  type="button"
                  className="w-full rounded-md border bg-card p-4 text-left transition-colors hover:bg-muted/40"
                  onClick={() => setSelectedLead(lead)}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-[14px] font-semibold">
                        {lead.name || lead.customer_name || 'Unknown'}
                      </p>
                      <p className="mt-0.5 text-[12px] text-muted-foreground">
                        {lead.phone || lead.customer_phone || lead.email || 'No contact info'}
                      </p>
                    </div>
                    <span
                      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-1 text-[11px] font-medium capitalize ${getStatusClasses(lead.status)}`}
                    >
                      {getStatusIcon(lead.status)}
                      {lead.status}
                    </span>
                  </div>
                  {lead.interest && (
                    <p className="mt-3 truncate text-[12px] text-muted-foreground">
                      Interest: {lead.interest}
                    </p>
                  )}
                  <p className="mt-3 font-mono text-[11px] text-muted-foreground">
                    {formatDate(lead.created_at)}
                  </p>
                </button>
              ))}
                </div>
              }
              desktop={
                <div className="overflow-hidden rounded-md border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Contact</TableHead>
                    <TableHead>Interest</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Captured</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {leads.map((lead) => (
                    <TableRow
                      key={lead.id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => setSelectedLead(lead)}
                    >
                      <TableCell className="font-medium">
                        {lead.name || lead.customer_name || (
                          <span className="text-muted-foreground italic">Unknown</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="text-sm">{lead.phone || lead.customer_phone || '—'}</div>
                        {lead.email && (
                          <div className="text-xs text-muted-foreground">{lead.email}</div>
                        )}
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate text-sm text-muted-foreground">
                        {lead.interest || '—'}
                      </TableCell>
                      <TableCell className="capitalize text-sm">
                        {lead.source_channel || 'chat'}
                      </TableCell>
                      <TableCell>
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize ${getStatusClasses(lead.status)}`}
                        >
                          {getStatusIcon(lead.status)}
                          {lead.status}
                        </span>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDate(lead.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
                </div>
              }
            />

            {/* Load more */}
            {hasNextPage && (
              <div className="flex justify-center pt-4">
                <Button variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
                  {isFetchingNextPage && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  {isFetchingNextPage ? 'Loading more…' : 'Load More Leads'}
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Detail sheet */}
      <LeadDetailsSheet
        lead={selectedLead}
        open={!!selectedLead}
        onClose={() => setSelectedLead(null)}
        canMutate={canMutateLeads}
      />
    </div>
  );
}
