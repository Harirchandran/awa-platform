import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { BadgeIndianRupee, RefreshCw, Save, Settings2, SlidersHorizontal, Building2, History } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { normalizeAllowedAiModes } from '@/lib/ai-mode';
interface PlanLimits {
  monthly_messages: number | null;
  daily_messages: number | null;
  monthly_ai_tokens: number | null;
  monthly_credits: number | null;
  allowed_ai_modes: string[];
  allowed_modules: string[];
  trial_days?: number | null;
  grace_period_days?: number | null;
  tool_credit_cost?: number | null;
  message_credit_cost?: number | null;
}
interface AdminPlan {
  id: string;
  plan_code: string | null;
  name: string;
  monthly_price_cents: number;
  currency: string;
  razorpay_plan_id: string;
  is_active: boolean;
  features_json: string[] | string | null;
  limits_json: Partial<PlanLimits> | string | null;
}
interface PlanDraft {
  id?: string;
  plan_code: string;
  name: string;
  monthly_price_cents: number;
  currency: string;
  razorpay_plan_id: string;
  is_active: boolean;
  features: string;
  limits: PlanLimits;
}
interface TenantBilling {
  id: string;
  name: string;
  slug: string | null;
  status: 'trial' | 'active' | 'suspended' | 'expired';
  plan_code: string;
  trial_ends_at: string | null;
  current_period_end: string | null;
  razorpay_customer_id: string | null;
  razorpay_subscription_id: string | null;
  plan_name: string | null;
  monthly_price_cents: number | null;
  currency: string | null;
  razorpay_plan_id: string | null;
  month_message_count: number;
  month_ai_tokens_used: number;
  month_credits_used: number;
  month_tool_action_count: number;
  month_cost: number;
}
type TenantBillingEditableField =
  | 'plan_code'
  | 'status'
  | 'trial_ends_at'
  | 'current_period_end'
  | 'razorpay_customer_id'
  | 'razorpay_subscription_id';
type TenantBillingEdit = Partial<Pick<TenantBilling, TenantBillingEditableField>> & { reason?: string };
interface BillingEvent {
  id: string;
  tenant_name: string | null;
  action: string;
  resource_type: string;
  details: Record<string, unknown> | null;
  created_at: string;
}
const blankLimits: PlanLimits = {
  monthly_messages: 1000,
  daily_messages: 150,
  monthly_ai_tokens: 300000,
  monthly_credits: 3000,
  allowed_ai_modes: ['standard'],
  allowed_modules: ['booking', 'faq', 'human_handoff'],
  trial_days: 7,
  grace_period_days: 3,
  tool_credit_cost: 1,
  message_credit_cost: 0.1,
};
const blankDraft: PlanDraft = {
  plan_code: 'starter',
  name: 'Starter',
  monthly_price_cents: 9900,
  currency: 'INR',
  razorpay_plan_id: 'plan_xxxxx',
  is_active: true,
  features: 'Basic AI agent\n1000 messages/month\nBooking automation',
  limits: blankLimits,
};
function parseFeatures(value: AdminPlan['features_json']) {
  if (!value) return [];
  if (Array.isArray(value)) return value.filter((feature): feature is string => typeof feature === 'string');
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter((feature): feature is string => typeof feature === 'string') : [];
  } catch {
    return [];
  }
}
function parseLimits(value: AdminPlan['limits_json']): PlanLimits {
  const raw = typeof value === 'string' ? safeJson(value) : value;
  const limits = { ...blankLimits, ...(raw && typeof raw === 'object' ? raw : {}) };
  return { ...limits, allowed_ai_modes: normalizeAllowedAiModes(limits.allowed_ai_modes) };
}
function safeJson(value: string) {
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}
function planToDraft(plan: AdminPlan): PlanDraft {
  return {
    id: plan.id,
    plan_code: plan.plan_code || '',
    name: plan.name,
    monthly_price_cents: plan.monthly_price_cents,
    currency: plan.currency,
    razorpay_plan_id: plan.razorpay_plan_id,
    is_active: plan.is_active,
    features: parseFeatures(plan.features_json).join('\n'),
    limits: parseLimits(plan.limits_json),
  };
}
function draftPayload(draft: PlanDraft) {
  return {
    name: draft.name,
    plan_code: draft.plan_code.trim().toLowerCase(),
    monthly_price_cents: Number(draft.monthly_price_cents),
    currency: draft.currency.trim().toUpperCase(),
    razorpay_plan_id: draft.razorpay_plan_id.trim(),
    is_active: draft.is_active,
    features_json: draft.features.split('\n').map((f) => f.trim()).filter(Boolean),
    limits_json: {
      ...draft.limits,
      allowed_ai_modes: normalizeAllowedAiModes(draft.limits.allowed_ai_modes),
    },
  };
}
function setNumberOrNull(value: string) {
  return value.trim() === '' ? null : Number(value);
}
function csv(value: string[]) {
  return value.join(', ');
}
function fromCsv(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}
function formatMoney(cents?: number | null, currency = 'INR') {
  return `${currency} ${((cents || 0) / 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}
function toDateTimeLocal(value: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toISOString().slice(0, 16);
}
function optionalText(value?: string | null) {
  const cleaned = value?.trim();
  return cleaned ? cleaned : null;
}
const tenantBillingFieldLabels: Record<TenantBillingEditableField, string> = {
  plan_code: 'Plan',
  status: 'Status',
  trial_ends_at: 'Trial end',
  current_period_end: 'Period end',
  razorpay_customer_id: 'Customer ID',
  razorpay_subscription_id: 'Subscription ID',
};
const hasField = (edit: TenantBillingEdit, field: TenantBillingEditableField) =>
  Object.prototype.hasOwnProperty.call(edit, field);
function getTenantBillingChanges(tenant: TenantBilling, edit: TenantBillingEdit) {
  const changes: Partial<Pick<TenantBilling, TenantBillingEditableField>> = {};
  if (hasField(edit, 'plan_code') && edit.plan_code && edit.plan_code !== tenant.plan_code) {
    changes.plan_code = edit.plan_code;
  }
  if (hasField(edit, 'status') && edit.status && edit.status !== tenant.status) {
    changes.status = edit.status;
  }
  (['trial_ends_at', 'current_period_end'] as const).forEach((field) => {
    if (!hasField(edit, field)) return;
    const nextValue = edit[field] ?? null;
    if (toDateTimeLocal(nextValue) !== toDateTimeLocal(tenant[field])) {
      changes[field] = nextValue;
    }
  });
  (['razorpay_customer_id', 'razorpay_subscription_id'] as const).forEach((field) => {
    if (!hasField(edit, field)) return;
    const nextValue = optionalText(edit[field]);
    if (nextValue !== optionalText(tenant[field])) {
      changes[field] = nextValue;
    }
  });
  return changes;
}
function describeTenantChanges(changes: Partial<Pick<TenantBilling, TenantBillingEditableField>>) {
  return (Object.keys(changes) as TenantBillingEditableField[])
    .map((field) => tenantBillingFieldLabels[field])
    .join(', ');
}
function planPayloadSignature(draftValue: PlanDraft) {
  return JSON.stringify(draftPayload(draftValue));
}
function PlanPriceInput({
  valueCents,
  onChangeCents,
  error,
}: {
  valueCents: number;
  onChangeCents: (val: number) => void;
  error?: string | null;
}) {
  const [prevValueCents, setPrevValueCents] = useState(valueCents);
  const [localVal, setLocalVal] = useState(String(valueCents / 100));

  if (valueCents !== prevValueCents) {
    setPrevValueCents(valueCents);
    setLocalVal(String(valueCents / 100));
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setLocalVal(val);
    const parsed = parseFloat(val);
    if (!isNaN(parsed)) {
      onChangeCents(Math.round(parsed * 100));
    }
  };

  const handleBlur = () => {
    const parsed = parseFloat(localVal) || 0;
    setLocalVal(parsed.toFixed(2));
    onChangeCents(Math.round(parsed * 100));
  };

  return (
    <>
      <Input
        type="text"
        value={localVal}
        onChange={handleChange}
        onBlur={handleBlur}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? 'plan-price-error' : undefined}
      />
      {error && <p id="plan-price-error" className="text-[12px] text-destructive">{error}</p>}
    </>
  );
}
export function PricingPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<PlanDraft>(blankDraft);
  const [tenantSearch, setTenantSearch] = useState('');
  const [tenantEdits, setTenantEdits] = useState<Record<string, TenantBillingEdit>>({});
  const plansQuery = useQuery({
    queryKey: ['admin_plans'],
    queryFn: async (): Promise<AdminPlan[]> => (await api.get<AdminPlan[]>('/api/v1/admin/plans')).data,
  });
  const tenantsQuery = useQuery({
    queryKey: ['admin_billing_tenants'],
    queryFn: async (): Promise<TenantBilling[]> => (await api.get<TenantBilling[]>('/api/v1/admin/billing/tenants')).data,
  });
  const eventsQuery = useQuery({
    queryKey: ['admin_billing_events'],
    queryFn: async (): Promise<BillingEvent[]> => (await api.get<BillingEvent[]>('/api/v1/admin/billing/events')).data,
  });
  const isRefreshingBilling =
    plansQuery.isFetching || tenantsQuery.isFetching || eventsQuery.isFetching;
  const savePlan = useMutation({
    mutationFn: async (value: PlanDraft) => {
      const payload = draftPayload(value);
      if (value.id) {
        return (await api.patch<AdminPlan>(`/api/v1/admin/plans/${value.id}`, payload)).data;
      }
      return (await api.post<AdminPlan>('/api/v1/admin/plans', payload)).data;
    },
    onSuccess: (saved) => {
      toast.success('Billing plan saved.');
      setDraft(planToDraft(saved));
      queryClient.invalidateQueries({ queryKey: ['admin_plans'] });
      queryClient.invalidateQueries({ queryKey: ['admin_billing_tenants'] });
    },
    onError: (error: unknown) => toast.error(`Plan save failed: ${getApiErrorMessage(error)}`),
  });
  const updateTenant = useMutation({
    mutationFn: async ({ tenantId, payload }: { tenantId: string; payload: Record<string, unknown> }) =>
      (await api.patch(`/api/v1/admin/billing/tenants/${tenantId}`, payload)).data,
    onSuccess: (_data, variables) => {
      toast.success('Tenant billing updated.');
      setTenantEdits((current) => {
        const next = { ...current };
        delete next[variables.tenantId];
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ['admin_billing_tenants'] });
      queryClient.invalidateQueries({ queryKey: ['admin_billing_events'] });
    },
    onError: (error: unknown) => toast.error(`Tenant update failed: ${getApiErrorMessage(error)}`),
  });
  const filteredTenants = useMemo(() => {
    const query = tenantSearch.trim().toLowerCase();
    if (!query) return tenantsQuery.data || [];
    return (tenantsQuery.data || []).filter((tenant) =>
      tenant.name.toLowerCase().includes(query) ||
      tenant.slug?.toLowerCase().includes(query) ||
      tenant.plan_code.toLowerCase().includes(query) ||
      tenant.status.toLowerCase().includes(query)
    );
  }, [tenantSearch, tenantsQuery.data]);
  const selectedPlan = useMemo(
    () => (draft.id ? plansQuery.data?.find((plan) => plan.id === draft.id) : null),
    [draft.id, plansQuery.data]
  );
  const planHasChanges = useMemo(() => {
    if (!draft.id) return true;
    if (!selectedPlan) return true;
    return planPayloadSignature(draft) !== planPayloadSignature(planToDraft(selectedPlan));
  }, [draft, selectedPlan]);
  const planPriceError = useMemo(() => {
    if (!Number.isFinite(draft.monthly_price_cents)) return 'Enter a valid plan price.';
    if (draft.monthly_price_cents < 0) return 'Plan price cannot be negative.';
    return null;
  }, [draft.monthly_price_cents]);
  const setLimit = <K extends keyof PlanLimits>(key: K, value: PlanLimits[K]) => {
    setDraft((current) => ({ ...current, limits: { ...current.limits, [key]: value } }));
  };
  const patchTenantEdit = (tenantId: string, patch: TenantBillingEdit) => {
    setTenantEdits((current) => ({
      ...current,
      [tenantId]: { ...current[tenantId], ...patch },
    }));
  };
  const resetTenantEdit = (tenantId: string) => {
    setTenantEdits((current) => {
      const next = { ...current };
      delete next[tenantId];
      return next;
    });
  };
  const handleSavePlan = () => {
    if (planPriceError) {
      toast.error(planPriceError);
      return;
    }
    if (!planHasChanges) {
      toast('No plan changes to save.');
      return;
    }
    savePlan.mutate(draft);
  };
  const submitTenant = (tenant: TenantBilling) => {
    const edit = tenantEdits[tenant.id] || {};
    const changes = getTenantBillingChanges(tenant, edit);
    if (Object.keys(changes).length === 0) {
      toast('No tenant billing changes to save.');
      resetTenantEdit(tenant.id);
      return;
    }
    updateTenant.mutate({
      tenantId: tenant.id,
      payload: {
        ...changes,
        reason: edit.reason || 'manual_super_admin_update',
      },
    });
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Billing Control</h1>
          <p className="text-[13px] text-muted-foreground mt-1">Plans, tenant billing state, Razorpay mappings, limits, and payment events.</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            queryClient.invalidateQueries({ queryKey: ['admin_plans'] });
            queryClient.invalidateQueries({ queryKey: ['admin_billing_tenants'] });
            queryClient.invalidateQueries({ queryKey: ['admin_billing_events'] });
          }}
        >
          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isRefreshingBilling ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>
      <Tabs defaultValue="plans" className="space-y-4">
        <TabsList>
          <TabsTrigger value="plans"><SlidersHorizontal className="w-3.5 h-3.5 mr-1.5" />Plans</TabsTrigger>
          <TabsTrigger value="tenants"><Building2 className="w-3.5 h-3.5 mr-1.5" />Tenants</TabsTrigger>
          <TabsTrigger value="events"><History className="w-3.5 h-3.5 mr-1.5" />Events</TabsTrigger>
        </TabsList>
        <TabsContent value="plans" className="space-y-4">
          <div className="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center text-[15px]">
                  <Settings2 className="w-4 h-4 mr-2" />
                  {draft.id ? 'Edit Plan' : 'Create Plan'}
                </CardTitle>
                <CardDescription>Business pricing is DB-driven; gateway secrets stay in env.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Plan Code">
                    <Input value={draft.plan_code} onChange={(e) => setDraft({ ...draft, plan_code: e.target.value })} />
                  </Field>
                  <Field label="Display Name">
                    <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                  </Field>
                </div>
                <Field label="Razorpay Plan ID">
                  <Input value={draft.razorpay_plan_id} onChange={(e) => setDraft({ ...draft, razorpay_plan_id: e.target.value })} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Currency">
                    <Input value={draft.currency} onChange={(e) => setDraft({ ...draft, currency: e.target.value })} />
                  </Field>
                  <Field label="Price (Monthly)">
                    <PlanPriceInput
                      valueCents={draft.monthly_price_cents}
                      onChangeCents={(val) => setDraft({ ...draft, monthly_price_cents: val })}
                      error={planPriceError}
                    />
                  </Field>
                </div>
                <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
                  <div>
                    <Label>Visible to tenants</Label>
                    <p className="text-[12px] text-muted-foreground">Inactive plans stay stored but hidden from checkout.</p>
                  </div>
                  <Switch checked={draft.is_active} onCheckedChange={(checked) => setDraft({ ...draft, is_active: checked })} />
                </div>
                <Field label="Features">
                  <Textarea rows={4} value={draft.features} onChange={(e) => setDraft({ ...draft, features: e.target.value })} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <LimitInput label="Monthly Messages" value={draft.limits.monthly_messages} onChange={(v) => setLimit('monthly_messages', v)} />
                  <LimitInput label="Daily Messages" value={draft.limits.daily_messages} onChange={(v) => setLimit('daily_messages', v)} />
                  <LimitInput label="Monthly AI Tokens" value={draft.limits.monthly_ai_tokens} onChange={(v) => setLimit('monthly_ai_tokens', v)} />
                  <LimitInput label="Monthly Credits" value={draft.limits.monthly_credits} onChange={(v) => setLimit('monthly_credits', v)} />
                  <LimitInput label="Trial Days" value={draft.limits.trial_days ?? null} onChange={(v) => setLimit('trial_days', v)} />
                  <LimitInput label="Grace Days" value={draft.limits.grace_period_days ?? null} onChange={(v) => setLimit('grace_period_days', v)} />
                </div>
                <Field label="Allowed AI Capability Profiles">
                  <Input value={csv(draft.limits.allowed_ai_modes)} onChange={(e) => setLimit('allowed_ai_modes', fromCsv(e.target.value))} />
                  <p className="text-[12px] text-muted-foreground">Profiles use the unified AWA agent; this list gates capability/profile access, not engine routing.</p>
                </Field>
                <Field label="Allowed Modules">
                  <Input value={csv(draft.limits.allowed_modules)} onChange={(e) => setLimit('allowed_modules', fromCsv(e.target.value))} />
                </Field>
                <div className="flex gap-2">
                  <Button className="flex-1" disabled={savePlan.isPending || !planHasChanges || Boolean(planPriceError)} onClick={handleSavePlan}>
                    <Save className="w-3.5 h-3.5 mr-1.5" />
                    {savePlan.isPending ? 'Saving Plan' : draft.id && !planHasChanges ? 'No Changes' : 'Save Plan'}
                  </Button>
                  <Button variant="outline" onClick={() => setDraft(blankDraft)}>New</Button>
                </div>
                <p className="text-[12px] text-muted-foreground">
                  {draft.id
                    ? planHasChanges
                      ? 'Unsaved plan changes will update tenant checkout and plan limits after save.'
                      : 'No plan changes detected.'
                    : 'Creating a plan can upsert by Razorpay plan ID; review values before saving.'}
                </p>
              </CardContent>
            </Card>
            <div className="space-y-3">
              {plansQuery.isLoading ? <Skeleton className="h-48 w-full" /> : plansQuery.data?.map((plan) => {
                const limits = parseLimits(plan.limits_json);
                return (
                  <Card key={plan.id} className="border-l-4 border-l-primary/50">
                    <CardContent className="p-4">
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-[15px] font-semibold">{plan.name}</h3>
                            <span className="rounded bg-muted px-2 py-0.5 text-[11px] uppercase">{plan.plan_code}</span>
                            {!plan.is_active ? <span className="rounded bg-destructive/10 px-2 py-0.5 text-[11px] text-destructive">Inactive</span> : null}
                          </div>
                          <p className="mt-1 font-mono text-[12px] text-muted-foreground">{plan.razorpay_plan_id}</p>
                          <p className="mt-2 flex items-center text-[13px] font-medium"><BadgeIndianRupee className="w-3.5 h-3.5 mr-1" />{formatMoney(plan.monthly_price_cents, plan.currency)}</p>
                        </div>
                        <Button size="sm" variant="outline" onClick={() => setDraft(planToDraft(plan))}>Edit</Button>
                      </div>
                      <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-2 text-[12px]">
                        <Metric label="Messages" value={limits.monthly_messages ?? 'Unlimited'} />
                        <Metric label="Daily" value={limits.daily_messages ?? 'Unlimited'} />
                        <Metric label="Tokens" value={limits.monthly_ai_tokens ?? 'Unlimited'} />
                        <Metric label="Credits" value={limits.monthly_credits ?? 'Unlimited'} />
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          </div>
        </TabsContent>
        <TabsContent value="tenants" className="space-y-3">
          <Input className="max-w-sm" placeholder="Search tenant, plan, status..." value={tenantSearch} onChange={(e) => setTenantSearch(e.target.value)} />
          <div className="rounded-md border border-border bg-card overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tenant</TableHead>
                  <TableHead>Plan</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Usage</TableHead>
                  <TableHead>Razorpay</TableHead>
                  <TableHead>Periods</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tenantsQuery.isLoading ? (
                  Array.from({ length: 4 }).map((_, i) => (
                    <TableRow key={i}><TableCell colSpan={7}><Skeleton className="h-8 w-full" /></TableCell></TableRow>
                  ))
                ) : filteredTenants.map((tenant) => {
                  const edit = tenantEdits[tenant.id] || {};
                  const tenantChanges = getTenantBillingChanges(tenant, edit);
                  const changeCount = Object.keys(tenantChanges).length;
                  const isDirty = changeCount > 0;
                  const changeSummary = describeTenantChanges(tenantChanges);
                  const isSavingRow = updateTenant.isPending && updateTenant.variables?.tenantId === tenant.id;
                  return (
                    <TableRow key={tenant.id}>
                      <TableCell>
                        <p className="font-medium">{tenant.name}</p>
                        <p className="text-[11px] text-muted-foreground">{tenant.slug}</p>
                      </TableCell>
                      <TableCell>
                        <Select value={edit.plan_code ?? tenant.plan_code} onValueChange={(v) => patchTenantEdit(tenant.id, { plan_code: v })} disabled={isSavingRow}>
                          <SelectTrigger className="h-8 w-[140px]"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {(plansQuery.data || []).map((plan) => (
                              <SelectItem key={plan.id} value={plan.plan_code || plan.razorpay_plan_id}>{plan.name}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <p className="mt-1 text-[11px] text-muted-foreground">{tenant.plan_name || 'No plan row'}</p>
                      </TableCell>
                      <TableCell>
                        <Select value={edit.status ?? tenant.status} onValueChange={(v) => patchTenantEdit(tenant.id, { status: v as TenantBilling['status'] })} disabled={isSavingRow}>
                          <SelectTrigger className="h-8 w-[120px]"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="trial">trial</SelectItem>
                            <SelectItem value="active">active</SelectItem>
                            <SelectItem value="suspended">suspended</SelectItem>
                            <SelectItem value="expired">expired</SelectItem>
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell className="text-[12px]">
                        <p>{tenant.month_message_count.toLocaleString()} messages</p>
                        <p className="text-muted-foreground">{tenant.month_ai_tokens_used.toLocaleString()} tokens</p>
                        <p className="text-muted-foreground">{Number(tenant.month_credits_used).toLocaleString()} credits</p>
                      </TableCell>
                      <TableCell className="min-w-[210px] space-y-1">
                        <Input className="h-8 text-[12px]" placeholder="Customer ID" value={edit.razorpay_customer_id ?? tenant.razorpay_customer_id ?? ''} onChange={(e) => patchTenantEdit(tenant.id, { razorpay_customer_id: e.target.value })} disabled={isSavingRow} />
                        <Input className="h-8 text-[12px]" placeholder="Subscription ID" value={edit.razorpay_subscription_id ?? tenant.razorpay_subscription_id ?? ''} onChange={(e) => patchTenantEdit(tenant.id, { razorpay_subscription_id: e.target.value })} disabled={isSavingRow} />
                      </TableCell>
                      <TableCell className="min-w-[190px] space-y-1">
                        <Input className="h-8 text-[12px]" type="datetime-local" value={toDateTimeLocal(edit.trial_ends_at ?? tenant.trial_ends_at)} onChange={(e) => patchTenantEdit(tenant.id, { trial_ends_at: e.target.value ? new Date(e.target.value).toISOString() : null })} disabled={isSavingRow} />
                        <Input className="h-8 text-[12px]" type="datetime-local" value={toDateTimeLocal(edit.current_period_end ?? tenant.current_period_end)} onChange={(e) => patchTenantEdit(tenant.id, { current_period_end: e.target.value ? new Date(e.target.value).toISOString() : null })} disabled={isSavingRow} />
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex flex-col items-end gap-1.5">
                          <Button
                            size="sm"
                            disabled={!isDirty || updateTenant.isPending}
                            onClick={() => submitTenant(tenant)}
                            title={isDirty ? `Save ${changeSummary}` : 'No billing changes to save'}
                          >
                            {isSavingRow ? 'Saving' : isDirty ? 'Save Changes' : 'Saved'}
                          </Button>
                          {isDirty && (
                            <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-[11px]" onClick={() => resetTenantEdit(tenant.id)} disabled={updateTenant.isPending}>
                              Reset
                            </Button>
                          )}
                          <p className={`max-w-[160px] text-[11px] leading-4 ${isDirty ? 'text-amber-600' : 'text-muted-foreground'}`}>
                            {isDirty ? `${changeCount} pending: ${changeSummary}` : 'No changes'}
                          </p>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
        <TabsContent value="events">
          <div className="rounded-md border border-border bg-card overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Tenant</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead>Details</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {eventsQuery.isLoading ? (
                  <TableRow><TableCell colSpan={4}><Skeleton className="h-10 w-full" /></TableCell></TableRow>
                ) : eventsQuery.data?.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="text-[12px] text-muted-foreground">{new Date(event.created_at).toLocaleString()}</TableCell>
                    <TableCell>{event.tenant_name || 'Platform'}</TableCell>
                    <TableCell><span className="rounded bg-muted px-2 py-1 text-[12px]">{event.action}</span></TableCell>
                    <TableCell className="max-w-[420px] truncate font-mono text-[11px] text-muted-foreground">{JSON.stringify(event.details || {})}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
function LimitInput({ label, value, onChange }: { label: string; value: number | null; onChange: (value: number | null) => void }) {
  return (
    <Field label={label}>
      <Input
        type="number"
        placeholder="blank = unlimited"
        value={value ?? ''}
        onChange={(e) => onChange(setNumberOrNull(e.target.value))}
      />
    </Field>
  );
}
function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md bg-muted/50 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 font-medium">{typeof value === 'number' ? value.toLocaleString() : value}</p>
    </div>
  );
}
