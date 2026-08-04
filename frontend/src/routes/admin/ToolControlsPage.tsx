import { useMemo, useState } from 'react';
import { Building2, CheckCircle2, Lock, Save, Search, Settings2, Wand2 } from 'lucide-react';
import {
  useAdminToolControlsList,
  useTenantToolControls,
  useUpdateTenantToolControls,
  type ToolControlTenantSummary,
} from '@/features/tenants/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
function toolPillLabel(key: string) {
  return key.replace(/_/g, ' ');
}
function matchesTenant(tenant: ToolControlTenantSummary, query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return (
    tenant.name.toLowerCase().includes(normalized) ||
    tenant.slug?.toLowerCase().includes(normalized) ||
    tenant.business_type?.toLowerCase().includes(normalized) ||
    tenant.plan_code.toLowerCase().includes(normalized)
  );
}
interface ToolDraft {
  tenantId: string;
  configVersion?: number | null;
  modules: string[];
  businessType?: string;
}
export function ToolControlsPage() {
  const listQuery = useAdminToolControlsList();
  const updateTools = useUpdateTenantToolControls();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTenantId, setSelectedTenantId] = useState<string | undefined>();
  const [draft, setDraft] = useState<ToolDraft | null>(null);
  const tenants = useMemo(() => listQuery.data?.tenants ?? [], [listQuery.data?.tenants]);
  const filteredTenants = useMemo(
    () => tenants.filter((tenant) => matchesTenant(tenant, searchQuery)),
    [tenants, searchQuery],
  );
  const effectiveTenantId = useMemo(() => {
    if (selectedTenantId && filteredTenants.some((tenant) => tenant.id === selectedTenantId)) {
      return selectedTenantId;
    }
    return filteredTenants[0]?.id;
  }, [filteredTenants, selectedTenantId]);
  const detailQuery = useTenantToolControls(effectiveTenantId);
  const presets = detailQuery.data?.presets || listQuery.data?.presets || {};
  const selectedTenant = tenants.find((tenant) => tenant.id === effectiveTenantId);
  const draftMatches = !!(
    draft &&
    detailQuery.data &&
    draft.tenantId === detailQuery.data.tenant.id &&
    draft.configVersion === (detailQuery.data.config_version ?? null)
  );
  const originalModules = detailQuery.data?.active_modules;
  const draftModules = useMemo(() => {
    if (draftMatches && draft) {
      return draft.modules;
    }
    return originalModules ?? [];
  }, [draft, draftMatches, originalModules]);
  const draftBusinessType = draftMatches ? draft.businessType : detailQuery.data?.business_type;
  const updateDraft = (updater: (current: ToolDraft) => ToolDraft) => {
    const data = detailQuery.data;
    if (!data) return;
    setDraft((current) => {
      const configVersion = data.config_version ?? null;
      const base =
        current && current.tenantId === data.tenant.id && current.configVersion === configVersion
          ? current
          : {
              tenantId: data.tenant.id,
              configVersion,
              modules: data.active_modules,
              businessType: data.business_type,
            };
      return updater(base);
    });
  };
  const hasChanges = useMemo(() => {
    if (!detailQuery.data) return false;
    const original = [...detailQuery.data.active_modules].sort().join('|');
    const draftValue = [...draftModules].sort().join('|');
    return original !== draftValue || draftBusinessType !== detailQuery.data.business_type;
  }, [detailQuery.data, draftBusinessType, draftModules]);
  const toggleModule = (key: string, checked: boolean) => {
    updateDraft((current) => {
      const modules = checked
        ? current.modules.includes(key)
          ? current.modules
          : [...current.modules, key]
        : current.modules.filter((moduleKey) => moduleKey !== key);
      return { ...current, modules };
    });
  };
  const applyPreset = (presetKey: string) => {
    const preset = presets[presetKey];
    if (!preset || !detailQuery.data) return;
    const allowed = new Set(detailQuery.data.plan_policy.allowed_modules);
    const nextModules = preset.enabled_modules.filter((key) => allowed.has('*') || allowed.has(key));
    updateDraft((current) => ({ ...current, modules: nextModules, businessType: preset.business_type }));
  };
  const save = () => {
    if (!effectiveTenantId) return;
    updateTools.mutate({
      tenantId: effectiveTenantId,
      enabled_modules: draftModules,
      business_type: draftBusinessType,
    });
  };
  return (
    <div className="space-y-6">
      <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Tool Controls</h1>
          <p className="mt-1 text-muted-foreground">
            Configure which workflows each business can show to tenants and customers.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-[12px] text-muted-foreground">
          <Settings2 className="h-4 w-4" />
          Global presets, tenant-specific activation
        </div>
      </div>
      <div className="grid gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="text-[16px]">Businesses</CardTitle>
            <CardDescription>Select a business to control visible tools.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search business, plan, type..."
                className="pl-9"
              />
            </div>
            <div className="max-h-[620px] space-y-2 overflow-y-auto pr-1">
              {listQuery.isLoading ? (
                Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-[82px] w-full" />)
              ) : filteredTenants.length === 0 ? (
                <div className="rounded-md border border-dashed p-5 text-center text-sm text-muted-foreground">
                  No businesses found.
                </div>
              ) : (
                filteredTenants.map((tenant) => {
                  const selected = tenant.id === effectiveTenantId;
                  return (
                    <button
                      key={tenant.id}
                      type="button"
                      onClick={() => setSelectedTenantId(tenant.id)}
                      className={`w-full rounded-md border p-3 text-left transition-colors ${
                        selected ? 'border-primary bg-primary/5' : 'bg-card hover:bg-muted/50'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold">{tenant.name}</p>
                          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">{tenant.slug}</p>
                        </div>
                        <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
                          {tenant.plan_code}
                        </span>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-1">
                        {tenant.active_modules.length ? tenant.active_modules.slice(0, 4).map((moduleKey) => (
                          <span key={moduleKey} className="rounded bg-secondary px-1.5 py-0.5 text-[10px] capitalize text-muted-foreground">
                            {toolPillLabel(moduleKey)}
                          </span>
                        )) : (
                          <span className="text-[11px] text-muted-foreground">No tools enabled</span>
                        )}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </CardContent>
        </Card>
        <div className="space-y-5">
          <Card>
            <CardContent className="p-5">
              {!effectiveTenantId || detailQuery.isLoading ? (
                <div className="space-y-4">
                  <Skeleton className="h-6 w-48" />
                  <Skeleton className="h-20 w-full" />
                  <Skeleton className="h-56 w-full" />
                </div>
              ) : !detailQuery.data ? (
                <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
                  Select a business to configure tools.
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="flex flex-col justify-between gap-4 xl:flex-row xl:items-start">
                    <div className="flex items-start gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border bg-background">
                        <Building2 className="h-5 w-5 text-muted-foreground" />
                      </div>
                      <div>
                        <h2 className="text-[18px] font-semibold">{detailQuery.data.tenant.name}</h2>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {detailQuery.data.tenant.slug} - {detailQuery.data.plan_policy.plan_code} - config v{detailQuery.data.config_version || 'new'}
                        </p>
                      </div>
                    </div>
                    <Button onClick={save} disabled={!hasChanges || updateTools.isPending}>
                      <Save className="h-4 w-4" />
                      {updateTools.isPending ? 'Saving...' : 'Save Tool Controls'}
                    </Button>
                  </div>
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
                    <section className="rounded-lg border bg-card p-4">
                      <div className="flex items-start gap-3">
                        <Wand2 className="mt-0.5 h-4 w-4 text-muted-foreground" />
                        <div>
                          <h3 className="text-sm font-semibold">Business Preset</h3>
                          <p className="mt-1 text-xs text-muted-foreground">
                            Presets are global recommendations. Saving writes the selected tool set only to this business.
                          </p>
                        </div>
                      </div>
                      <div className="mt-4 grid gap-3 sm:grid-cols-[240px_minmax(0,1fr)]">
                        <Select onValueChange={applyPreset}>
                          <SelectTrigger>
                            <SelectValue placeholder="Apply preset..." />
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(presets).map(([key, preset]) => (
                              <SelectItem key={key} value={key}>{preset.label}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <div className="space-y-2">
                          <Label>Business Type</Label>
                          <Input
                            value={draftBusinessType || ''}
                            onChange={(event) => updateDraft((current) => ({ ...current, businessType: event.target.value }))}
                            placeholder="e.g. salon, clinic, restaurant"
                          />
                        </div>
                      </div>
                    </section>
                    <section className="rounded-lg border bg-card p-4">
                      <h3 className="text-sm font-semibold">Plan Guard</h3>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        Tools locked by plan cannot be enabled here. Upgrade the tenant plan or edit billing limits first.
                      </p>
                      <div className="mt-3 flex flex-wrap gap-1">
                        {detailQuery.data.plan_policy.allowed_modules.map((moduleKey) => (
                          <span key={moduleKey} className="rounded bg-secondary px-2 py-1 text-[11px] capitalize text-muted-foreground">
                            {moduleKey === '*' ? 'all tools' : toolPillLabel(moduleKey)}
                          </span>
                        ))}
                      </div>
                    </section>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    {detailQuery.data.tools.map((tool) => {
                      const checked = draftModules.includes(tool.key);
                      const disabled = !tool.plan_allowed || updateTools.isPending;
                      return (
                        <section key={tool.key} className="rounded-lg border bg-card p-4">
                          <div className="flex items-start justify-between gap-4">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <h3 className="text-sm font-semibold">{tool.label}</h3>
                                {checked && (
                                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-300">
                                    <CheckCircle2 className="h-3 w-3" />
                                    Visible
                                  </span>
                                )}
                                {!tool.plan_allowed && (
                                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-200">
                                    <Lock className="h-3 w-3" />
                                    Plan locked
                                  </span>
                                )}
                              </div>
                              <p className="mt-1 text-xs leading-5 text-muted-foreground">{tool.description}</p>
                              {tool.field_count > 0 && (
                                <p className="mt-2 text-[11px] text-muted-foreground">{tool.field_count} configured field{tool.field_count === 1 ? '' : 's'} retained</p>
                              )}
                            </div>
                            <Switch
                              checked={checked}
                              disabled={disabled}
                              onCheckedChange={(value) => toggleModule(tool.key, value)}
                              aria-label={`Toggle ${tool.label}`}
                            />
                          </div>
                        </section>
                      );
                    })}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
          {selectedTenant && (
            <div className="rounded-md border bg-muted/30 p-3 text-[12px] leading-5 text-muted-foreground">
              Customer-facing menus and tenant dashboard navigation are tenant-specific. For example, disabling Orders for {selectedTenant.name} hides order suggestions in AWA Chat and removes the tenant Orders page from navigation.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}