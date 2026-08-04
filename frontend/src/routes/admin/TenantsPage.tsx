import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useTenantsList, useCreateTenant, useAdminBusinessTypes, impersonateTenant, toggleTenantStatus, usePlatformMetrics } from '@/features/tenants/api';
import type { CreateTenantPayload, CreateTenantResult, TenantRecord } from '@/features/tenants/api';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle, DialogTrigger
} from '@/components/ui/dialog';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ShieldAlert, Plus, Search, Building2, Phone, CalendarDays, CreditCard, CheckCircle2, KeyRound, UserPlus, ExternalLink, Loader2, MoreHorizontal, LogIn, ShieldOff, ShieldCheck, TrendingUp, DollarSign } from 'lucide-react';
import { format } from 'date-fns';
import { Skeleton } from '@/components/ui/skeleton';
import { isMockAuthEnabled, setDevMockToken } from '@/lib/dev-auth';
import { getApiErrorMessage } from '@/lib/api-errors';
import { authClient } from '@/lib/auth';
import toast from 'react-hot-toast';
const DEFAULT_TENANT_FORM: CreateTenantPayload = {
  name: '',
  slug: '',
  plan_code: 'trial',
  business_type: 'general',
  tenant_admin_email: '',
  tenant_admin_password: '',
  tenant_admin_name: '',
  whatsapp_phone_number_id: '',
  whatsapp_phone_number: '',
  whatsapp_display_name: '',
};
function getTenantAdminEmailError(value?: string) {
  const trimmed = value?.trim() || '';
  if (!trimmed) return null;
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
    return 'Enter a valid tenant admin email address.';
  }
  return null;
}
export function TenantsPage() {
  const { data: tenants, isLoading, isError } = useTenantsList();
  const { data: metrics, isLoading: isMetricsLoading } = usePlatformMetrics();
  const createTenantMutation = useCreateTenant();
  const { mutate: createTenant, isPending } = createTenantMutation;
  const { data: businessTypes = [], isLoading: isLoadingBusinessTypes } = useAdminBusinessTypes();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [lastCreated, setLastCreated] = useState<CreateTenantResult | null>(null);
  const [form, setForm] = useState<CreateTenantPayload>(DEFAULT_TENANT_FORM);
  // Tracks which tenant row has an in-flight action (impersonate / toggle status)
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);

  const handleImpersonate = async (tenant: TenantRecord) => {
    if (actionLoadingId) return;
    setActionLoadingId(tenant.id);
    try {
      const token = await impersonateTenant(tenant.id);
      // Store the impersonation token as the active session and hard-reload
      // into the tenant dashboard. The hard reload clears React Query's cache
      // so the new tenant context is picked up cleanly.
      authClient.setSession(token, '');
      queryClient.clear();
      navigate('/dashboard', { replace: true });
    } catch (err) {
      toast.error(`Impersonation failed: ${getApiErrorMessage(err)}`);
      setActionLoadingId(null);
    }
  };

  const handleToggleStatus = async (tenant: TenantRecord) => {
    if (actionLoadingId) return;
    const isSuspending = tenant.status === 'active';
    if (isSuspending) {
      const confirmed = window.confirm(
        `Suspend "${tenant.name}"?\n\nThis will prevent all tenant users from logging in until the tenant is reactivated.`,
      );
      if (!confirmed) return;
    }
    setActionLoadingId(tenant.id);
    try {
      await toggleTenantStatus(tenant.id, tenant.status);
      toast.success(
        isSuspending
          ? `"${tenant.name}" has been suspended.`
          : `"${tenant.name}" has been reactivated.`,
      );
      void queryClient.invalidateQueries({ queryKey: ['tenants'] });
    } catch (err) {
      toast.error(`Status update failed: ${getApiErrorMessage(err)}`);
    } finally {
      setActionLoadingId(null);
    }
  };

  const filteredTenants = tenants?.filter((t) =>
    t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    t.slug?.toLowerCase().includes(searchQuery.toLowerCase())
  );
  const tenantAdminEmailError = getTenantAdminEmailError(form.tenant_admin_email);
  const resetCreateForm = () => {
    setForm(DEFAULT_TENANT_FORM);
    setFormError(null);
  };
  const handleDialogOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) {
      setFormError(null);
      if (!isPending) {
        createTenantMutation.reset();
      }
      return;
    }
    resetCreateForm();
  };
  const handleSubmit = () => {
    if (isPending) return;
    setFormError(null);
    if (!form.name.trim()) {
      setFormError('Enter a business name before creating the tenant.');
      return;
    }
    const tenantAdminEmail = form.tenant_admin_email?.trim() || '';
    if (!tenantAdminEmail) {
      setFormError('Add the tenant admin email so this business has an owner who can sign in.');
      return;
    }
    const emailError = getTenantAdminEmailError(tenantAdminEmail);
    if (emailError) {
      setFormError(emailError);
      return;
    }
    const password = form.tenant_admin_password?.trim() || '';
    if (!password || password.length < 8 || !/[A-Za-z]/.test(password) || !/\d/.test(password)) {
      setFormError('Tenant admin password must be at least 8 characters and include a letter and a number.');
      return;
    }
    const payload: CreateTenantPayload = {
      name: form.name.trim(),
      plan_code: form.plan_code,
      business_type: form.business_type || 'general',
      tenant_admin_email: tenantAdminEmail.toLowerCase(),
      tenant_admin_password: password,
    };
    if (form.tenant_admin_name?.trim()) payload.tenant_admin_name = form.tenant_admin_name.trim();
    if (form.slug?.trim()) payload.slug = form.slug.trim().toLowerCase().replace(/\s+/g, '_');
    if (form.whatsapp_phone_number_id?.trim()) payload.whatsapp_phone_number_id = form.whatsapp_phone_number_id.trim();
    if (form.whatsapp_phone_number?.trim()) payload.whatsapp_phone_number = form.whatsapp_phone_number.trim();
    if (form.whatsapp_display_name?.trim()) payload.whatsapp_display_name = form.whatsapp_display_name.trim();
    createTenant(payload, {
      onSuccess: (created) => {
        setFormError(null);
        setLastCreated(created);
        setOpen(false);
        resetCreateForm();
      },
      onError: (error) => {
        setFormError(getApiErrorMessage(error, 'Failed to create the business. Please review the fields and try again.'));
      },
    });
  };
  const openAsTenant = () => {
    const token = lastCreated?.admin_access.dev_session_token;
    if (!token) return;
    setDevMockToken(token);
    navigate('/dashboard');
  };
  return (
    <div className="space-y-6">
      {/* ---- Global C-Suite Telemetry ---- */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {/* Active Tenants */}
        <Card>
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Active Tenants
            </CardTitle>
            <Building2 className="w-4 h-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {isMetricsLoading ? (
              <Skeleton className="h-8 w-20 mt-1" />
            ) : (
              <p className="text-3xl font-bold">{metrics?.active_tenants ?? '--'}</p>
            )}
            <p className="text-xs text-muted-foreground mt-1">Live count</p>
          </CardContent>
        </Card>

        {/* MTD Messages */}
        <Card>
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Messages (MTD)
            </CardTitle>
            <TrendingUp className="w-4 h-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {isMetricsLoading ? (
              <Skeleton className="h-8 w-24 mt-1" />
            ) : (
              <p className="text-3xl font-bold">
                {metrics?.mtd_messages != null
                  ? metrics.mtd_messages.toLocaleString()
                  : '--'}
              </p>
            )}
            <p className="text-xs text-muted-foreground mt-1">Calendar month to date</p>
          </CardContent>
        </Card>

        {/* MTD Cost */}
        <Card>
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              AI Cost (MTD)
            </CardTitle>
            <DollarSign className="w-4 h-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {isMetricsLoading ? (
              <Skeleton className="h-8 w-24 mt-1" />
            ) : (
              <p className="text-3xl font-bold">
                {metrics?.mtd_cost_usd != null
                  ? `$${metrics.mtd_cost_usd.toFixed(2)}`
                  : '--'}
              </p>
            )}
            <p className="text-xs text-muted-foreground mt-1">USD, calendar month to date</p>
          </CardContent>
        </Card>
      </div>
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Tenants</h1>
          <p className="text-muted-foreground mt-1">
            Manage your customer organizations and their active AI agents.
          </p>
        </div>
        <Dialog open={open} onOpenChange={handleDialogOpenChange}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="w-4 h-4 mr-2" />
              Create New Business
            </Button>
          </DialogTrigger>
          <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[560px]">
            <DialogHeader>
              <DialogTitle className="flex items-center">
                <Building2 className="w-5 h-5 mr-2 text-indigo-500" />
                Provision New Business
              </DialogTitle>
              <DialogDescription>
                Create a business, set its tenant admin, and optionally link a WhatsApp Business number.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4" aria-busy={isPending}>
              {formError && (
                <Alert variant="destructive">
                  <AlertDescription className="text-[13px]">{formError}</AlertDescription>
                </Alert>
              )}
              {isPending && (
                <Alert>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <AlertDescription className="text-[13px]">
                    Creating the business, tenant admin access, and starter configuration...
                  </AlertDescription>
                </Alert>
              )}
              {/* Business Name */}
              <div className="space-y-2">
                <Label htmlFor="name">Business Name *</Label>
                <Input
                  id="name"
                  placeholder="e.g. SmileDent Dental Clinic"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  disabled={isPending}
                />
              </div>
              {/* Slug */}
              <div className="space-y-2">
                <Label htmlFor="slug">URL Slug</Label>
                <Input
                  id="slug"
                  placeholder="e.g. smiledent_clinic (auto-generated if empty)"
                  value={form.slug}
                  onChange={(e) => setForm({ ...form, slug: e.target.value })}
                  className="font-mono text-sm"
                  disabled={isPending}
                />
              </div>
              {/* Business Type */}
              <div className="space-y-2">
                <Label>Business Type</Label>
                <Select value={form.business_type} onValueChange={(v) => setForm({ ...form, business_type: v })} disabled={isPending}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {isLoadingBusinessTypes ? (
                      <SelectItem value="general" disabled>Loading…</SelectItem>
                    ) : businessTypes.length === 0 ? (
                      <SelectItem value="general">General Business</SelectItem>
                    ) : (
                      businessTypes.map((bt) => (
                        <SelectItem key={bt.code} value={bt.code}>{bt.label}</SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </div>
              {/* Plan */}
              <div className="space-y-2">
                <Label>Pricing Plan</Label>
                <Select value={form.plan_code} onValueChange={(v) => setForm({ ...form, plan_code: v })} disabled={isPending}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="trial">Trial</SelectItem>
                    <SelectItem value="starter">Starter</SelectItem>
                    <SelectItem value="pro">Pro</SelectItem>
                    <SelectItem value="enterprise">Enterprise</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {/* Tenant Admin Section */}
              <div className="pt-2 border-t border-border">
                <div className="flex items-center mb-3">
                  <UserPlus className="w-4 h-4 mr-2 text-indigo-500" />
                  <span className="text-sm font-medium">Tenant Admin Access</span>
                </div>
                <div className="space-y-3">
                  <div className="space-y-1">
                    <Label htmlFor="tenant_admin_email" className="text-xs text-muted-foreground">Admin Email *</Label>
                    <Input
                      id="tenant_admin_email"
                      type="email"
                      placeholder="owner@business.com"
                      value={form.tenant_admin_email}
                      onChange={(e) => setForm({ ...form, tenant_admin_email: e.target.value })}
                      className="text-sm"
                      disabled={isPending}
                      aria-invalid={Boolean(tenantAdminEmailError)}
                      aria-describedby={tenantAdminEmailError ? 'tenant_admin_email-error' : undefined}
                    />
                    {tenantAdminEmailError && (
                      <p id="tenant_admin_email-error" className="text-xs text-destructive">
                        {tenantAdminEmailError}
                      </p>
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="tenant_admin_password" className="text-xs text-muted-foreground">Initial Password *</Label>
                    <Input
                      id="tenant_admin_password"
                      type="password"
                      placeholder="8+ characters with letters and numbers"
                      value={form.tenant_admin_password}
                      onChange={(e) => setForm({ ...form, tenant_admin_password: e.target.value })}
                      className="text-sm"
                      disabled={isPending}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="tenant_admin_name" className="text-xs text-muted-foreground">Admin Name</Label>
                    <Input
                      id="tenant_admin_name"
                      placeholder="e.g. Priya Sharma"
                      value={form.tenant_admin_name}
                      onChange={(e) => setForm({ ...form, tenant_admin_name: e.target.value })}
                      className="text-sm"
                      disabled={isPending}
                    />
                  </div>
                  <p className="text-xs leading-5 text-muted-foreground">
                    AWA creates a tenant-admin account for this email. The password is never shown again after creation.
                  </p>
                </div>
              </div>
              {/* WhatsApp Section */}
              <div className="pt-2 border-t border-border">
                <div className="flex items-center mb-3">
                  <Phone className="w-4 h-4 mr-2 text-emerald-500" />
                  <span className="text-sm font-medium">WhatsApp Integration (Optional)</span>
                </div>
                <div className="space-y-3">
                  <div className="space-y-1">
                    <Label htmlFor="wa_phone" className="text-xs text-muted-foreground">Phone Number</Label>
                    <Input
                      id="wa_phone"
                      placeholder="+91 98765 43210"
                      value={form.whatsapp_phone_number}
                      onChange={(e) => setForm({ ...form, whatsapp_phone_number: e.target.value })}
                      className="text-sm"
                      disabled={isPending}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="wa_id" className="text-xs text-muted-foreground">Phone Number ID (Meta)</Label>
                    <Input
                      id="wa_id"
                      placeholder="e.g. 1013394988526222"
                      value={form.whatsapp_phone_number_id}
                      onChange={(e) => setForm({ ...form, whatsapp_phone_number_id: e.target.value })}
                      className="font-mono text-sm"
                      disabled={isPending}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="wa_name" className="text-xs text-muted-foreground">Display Name</Label>
                    <Input
                      id="wa_name"
                      placeholder="e.g. SmileDent Support"
                      value={form.whatsapp_display_name}
                      onChange={(e) => setForm({ ...form, whatsapp_display_name: e.target.value })}
                      className="text-sm"
                      disabled={isPending}
                    />
                  </div>
                </div>
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => handleDialogOpenChange(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleSubmit}
                disabled={isPending}
                className="bg-indigo-600 hover:bg-indigo-700 text-white"
              >
                {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {isPending ? 'Creating' : 'Create Business'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
      {lastCreated && (
        <Alert className="border-emerald-200 bg-emerald-50 text-emerald-900">
          <CheckCircle2 className="h-4 w-4 text-emerald-700" />
          <AlertTitle>{lastCreated.name} is ready for tenant admin access</AlertTitle>
          <AlertDescription>
            <div className="mt-2 space-y-3">
              <p>
                Tenant admin <span className="font-medium">{lastCreated.admin_access.email}</span> was created and can sign in to administer this business.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button asChild size="sm" variant="outline">
                  <Link to="/login">
                    <ExternalLink className="h-3.5 w-3.5" />
                    Login Page
                  </Link>
                </Button>
                <Button asChild size="sm" variant="outline">
                  <Link to="/admin/tools">
                    <KeyRound className="h-3.5 w-3.5" />
                    Configure Tools
                  </Link>
                </Button>
                {lastCreated.admin_access.dev_session_token && isMockAuthEnabled() && (
                  <Button type="button" size="sm" onClick={openAsTenant}>
                    <UserPlus className="h-3.5 w-3.5" />
                    Open as Tenant
                  </Button>
                )}
              </div>
            </div>
          </AlertDescription>
        </Alert>
      )}
      <div className="flex items-center space-x-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search business name..."
            className="pl-9"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>
      {isError && (
        <div className="bg-destructive/10 border border-destructive p-4 rounded-lg flex items-start text-destructive">
          <ShieldAlert className="w-5 h-5 mr-3 mt-0.5" />
          <div>
            <h3 className="font-medium">Failed to fetch tenants</h3>
            <p className="text-sm mt-1 opacity-90">There was a problem communicating with the master database.</p>
          </div>
        </div>
      )}
      <div className="space-y-3 md:hidden">
        {isLoading ? (
          Array(5).fill(0).map((_, i) => (
            <div key={i} className="rounded-md border border-border bg-card p-4">
              <Skeleton className="h-4 w-36" />
              <Skeleton className="mt-2 h-3 w-28" />
              <div className="mt-4 grid grid-cols-2 gap-3">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            </div>
          ))
        ) : filteredTenants?.length === 0 ? (
          <div className="rounded-md border border-border bg-card p-8 text-center text-sm text-muted-foreground">
            No tenants found.
          </div>
        ) : (
          filteredTenants?.map((tenant) => (
            <TenantMobileCard key={tenant.id} tenant={tenant} />
          ))
        )}
      </div>
      <div className="hidden rounded-md border border-border bg-card overflow-hidden md:block">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead>Business Name</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead>Plan</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Created At</TableHead>
              <TableHead className="w-[52px]" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
               Array(5).fill(0).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                  <TableCell><Skeleton className="h-5 w-16" /></TableCell>
                  <TableCell><Skeleton className="h-5 w-16" /></TableCell>
                  <TableCell className="text-right"><Skeleton className="h-4 w-24 ml-auto" /></TableCell>
                  <TableCell />
                </TableRow>
              ))
            ) : filteredTenants?.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-32 text-center text-muted-foreground">
                  No tenants found.
                </TableCell>
              </TableRow>
            ) : (
              filteredTenants?.map((tenant) => (
                <TableRow key={tenant.id} className="hover:bg-muted/50 transition-colors">
                  <TableCell className="font-medium">{tenant.name}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{tenant.slug}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-indigo-500/10 text-indigo-500 uppercase">
                      {tenant.plan_code}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                      tenant.status === 'active' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-destructive/10 text-destructive'
                    }`}>
                      {tenant.status}
                    </span>
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground text-sm">
                    {format(new Date(tenant.created_at), 'MMM d, yyyy')}
                  </TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          disabled={actionLoadingId === tenant.id}
                          aria-label={`Actions for ${tenant.name}`}
                        >
                          {actionLoadingId === tenant.id
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <MoreHorizontal className="h-3.5 w-3.5" />}
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-48">
                        <DropdownMenuItem
                          onClick={() => void handleImpersonate(tenant)}
                          className="gap-2 cursor-pointer"
                        >
                          <LogIn className="h-3.5 w-3.5 text-indigo-500" />
                          Impersonate Tenant
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          onClick={() => void handleToggleStatus(tenant)}
                          className={`gap-2 cursor-pointer ${
                            tenant.status === 'active'
                              ? 'text-destructive focus:text-destructive'
                              : 'text-emerald-600 focus:text-emerald-600'
                          }`}
                        >
                          {tenant.status === 'active'
                            ? <><ShieldOff className="h-3.5 w-3.5" /> Suspend Tenant</>
                            : <><ShieldCheck className="h-3.5 w-3.5" /> Activate Tenant</>}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
function TenantMobileCard({ tenant }: { tenant: TenantRecord }) {
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">{tenant.name}</p>
          <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{tenant.slug}</p>
        </div>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${statusClass(tenant.status)}`}>
          {tenant.status}
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <CreditCard className="h-3.5 w-3.5" />
            Plan
          </div>
          <p className="mt-1 text-sm font-semibold uppercase text-foreground">{formatPlan(tenant.plan_code)}</p>
        </div>
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <CalendarDays className="h-3.5 w-3.5" />
            Created
          </div>
          <p className="mt-1 text-sm font-semibold text-foreground">{format(new Date(tenant.created_at), 'MMM d, yyyy')}</p>
        </div>
      </div>
    </div>
  );
}
function statusClass(status: TenantRecord['status']) {
  return status === 'active'
    ? 'bg-emerald-500/10 text-emerald-500'
    : 'bg-destructive/10 text-destructive';
}
function formatPlan(planCode: string) {
  return planCode.replace(/_/g, ' ');
}
