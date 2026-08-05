import { Link } from 'react-router';
import { useState } from 'react';
import { ArrowRight, Lock, Settings2 } from 'lucide-react';
import { safeDashboardHref, toolIcon } from '@/features/tools/catalog';
import { useTenantTools, type TenantToolState } from '@/features/tools/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ConfigSkeleton } from '@/components/ui/loading-states';
import { cn } from '@/lib/utils';
import { Switch } from '@/components/ui/switch';
import { useTenantConfig, useUpdateConfig } from '@/features/config/api';
import { toast } from 'react-hot-toast';
import { getApiErrorMessage } from '@/lib/api-errors';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';

const CRITICAL_TOOL_KEYS = new Set(['booking', 'order', 'orders', 'payments', 'reminders']);

type PendingToolToggle = {
  key: string;
  label: string;
  checked: boolean;
} | null;

function toolActions(tool: TenantToolState) {
  const configuredActions = (tool.actions || [])
    .map((action) => ({ ...action, href: safeDashboardHref(action.href) }))
    .filter((action): action is { label: string; href: string } => Boolean(action.href));
  const primaryHref = safeDashboardHref(tool.route_path);
  if (configuredActions.length > 0) {
    return configuredActions;
  }
  return primaryHref ? [{ label: 'Open', href: primaryHref }] : [];
}
function configSummary(tool: TenantToolState) {
  const planAllowed = tool.allowed_by_plan ?? tool.plan_allowed ?? true;
  const lockedByPlan = tool.locked_by_plan ?? !planAllowed;
  if (lockedByPlan || !planAllowed) return tool.locked_reason || 'Plan does not include this tool.';
  if (!tool.runtime_available) return 'Available, currently off.';
  if (tool.field_count > 0) return `${tool.field_count} configured field${tool.field_count === 1 ? '' : 's'}.`;
  return 'Enabled with default settings.';
}
export function ToolsPage() {
  const { data: toolsData, isLoading: isToolsLoading, isError, refetch, isFetching } = useTenantTools();
  const { data: configData, isLoading: isConfigLoading } = useTenantConfig();
  const updateConfig = useUpdateConfig();
  const [pendingToolToggle, setPendingToolToggle] = useState<PendingToolToggle>(null);

  const applyToolToggle = async (toolKey: string, checked: boolean) => {
    if (!configData) return;
    const updatedConfig = {
      ...configData,
      modules: {
        ...configData.modules,
        [toolKey]: {
          ...(configData.modules?.[toolKey] || { fields: [] }),
          enabled: checked,
        }
      }
    };
    await toast.promise(
      updateConfig.mutateAsync(updatedConfig),
      {
        loading: `${checked ? 'Enabling' : 'Disabling'} ${toolKey} workflow...`,
        success: `${checked ? 'Enabled' : 'Disabled'} ${toolKey} workflow!`,
        error: (err) => getApiErrorMessage(err, `Failed to update ${toolKey} workflow.`)
      }
    );
  };

  const requestToolToggle = (tool: TenantToolState, checked: boolean) => {
    if (!checked && CRITICAL_TOOL_KEYS.has(tool.key)) {
      setPendingToolToggle({ key: tool.key, label: tool.label, checked });
      return;
    }
    void applyToolToggle(tool.key, checked);
  };

  const isLoading = isToolsLoading || isConfigLoading;

  if (isLoading) {
    return (
      <ConfigSkeleton
        label="Loading workflow tools"
        description="This may take a few seconds in local demo mode while plan and configuration rules are checked."
        rows={5}
      />
    );
  }
  if (isError || !toolsData) {
    return (
      <div className="space-y-3 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-[13px] text-destructive">
        <div>
          <p className="font-medium">Workflow tools could not be loaded.</p>
          <p className="mt-1 text-[12px] leading-5 text-destructive/80">
            Refresh this panel or return to Overview. If this continues, ask support or your admin to check the tenant tools API.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? 'Retrying...' : 'Retry'}
          </Button>
          <Button asChild size="sm" variant="ghost">
            <Link to="/dashboard">Back to Overview</Link>
          </Button>
        </div>
      </div>
    );
  }
  const enabledTools = toolsData.tools.filter((tool) => tool.runtime_available);
  const availableOffTools = toolsData.tools.filter((tool) => {
    const planAllowed = tool.allowed_by_plan ?? tool.plan_allowed ?? true;
    return !tool.runtime_available && planAllowed;
  });
  const planLockedTools = toolsData.tools.filter((tool) => {
    const planAllowed = tool.allowed_by_plan ?? tool.plan_allowed ?? true;
    const lockedByPlan = tool.locked_by_plan ?? !planAllowed;
    return lockedByPlan || !planAllowed;
  });
  const businessType = toolsData.business_type?.replace(/_/g, ' ') || 'this business';
  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-[16px] font-semibold text-foreground">Workflow Tools</h2>
          <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">
            Use this page to review which workflows AWA can use for {businessType}. Use workflow pages to view bookings, orders, payments, reminders, and reviews created by customers.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-[12px] text-muted-foreground w-full shrink-0 sm:w-auto">
          <Settings2 className="h-4 w-4" />
          Workflow controls active
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <Card>
          <CardContent className="p-4">
            <p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">Enabled</p>
            <p className="mt-2 text-[24px] font-semibold leading-none">{enabledTools.length}</p>
            <p className="mt-1 text-[12px] text-muted-foreground">Ready for this business</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">Available</p>
            <p className="mt-2 text-[24px] font-semibold leading-none">{availableOffTools.length}</p>
            <p className="mt-1 text-[12px] text-muted-foreground">Can be enabled from config</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">Plan locked</p>
            <p className="mt-2 text-[24px] font-semibold leading-none">{planLockedTools.length}</p>
            <p className="mt-1 text-[12px] text-muted-foreground">Need billing access</p>
          </CardContent>
        </Card>
      </div>
      {enabledTools.length === 0 && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-[13px] leading-relaxed text-amber-900 dark:text-amber-100">
          No workflow tools are enabled yet. Available tools remain listed below.
        </div>
      )}
      <section className="space-y-3">
        <div>
          <h3 className="text-[14px] font-semibold text-foreground">Available tools</h3>
          <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
            This list comes from the saved tenant tool catalog and configuration. Disabled tools stay visible, but AWA will not use them with customers until they are enabled.
          </p>
        </div>
      </section>
      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {toolsData.tools.map((tool) => {
          const enabled = tool.enabled_by_tenant ?? tool.enabled;
          const planAllowed = tool.allowed_by_plan ?? tool.plan_allowed ?? true;
          const canEnable = tool.can_enable_module ?? planAllowed;
          const canConfigure = tool.can_configure ?? planAllowed;
          const statusLabel = !planAllowed ? 'Plan locked' : enabled ? 'Enabled' : 'Off';
          const Icon = toolIcon(tool.icon_key, tool.key);
          const actions = toolActions(tool);
          const primaryHref = safeDashboardHref(tool.route_path);
          return (
            <Card
              key={tool.key}
              className={cn(
                'min-w-0 overflow-hidden transition-all duration-200 hover:-translate-y-[2px] hover:shadow-md hover:border-primary/30',
                enabled && planAllowed ? 'border-primary/30' : 'bg-muted/20',
                !planAllowed && 'border-dashed'
              )}
            >
              <CardContent className="flex h-full flex-col gap-4 p-4">
                <div className="flex items-start gap-3">
                  <div className={cn(
                    'flex h-10 w-10 shrink-0 items-center justify-center rounded-md border bg-background',
                    enabled && planAllowed && 'border-primary/30 bg-primary/10 text-primary'
                  )}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-[14px] font-semibold text-foreground">{tool.label}</p>
                        <p className="mt-0.5 text-[12px] text-muted-foreground">{tool.category}</p>
                      </div>
                      {planAllowed ? (
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] text-muted-foreground">{enabled ? 'Active' : 'Off'}</span>
                          <Switch
                            checked={Boolean(enabled)}
                            disabled={updateConfig.isPending || !canEnable}
                            onCheckedChange={(checked) => requestToolToggle(tool, checked)}
                            aria-label={`Toggle ${tool.label} workflow`}
                          />
                        </div>
                      ) : (
                        <span className="shrink-0 rounded border px-2 py-1 text-[11px] font-medium border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300">
                          {statusLabel}
                        </span>
                      )}
                    </div>
                    <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">{tool.description}</p>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      <span className="rounded border border-border bg-background px-2 py-1 text-[11px] text-muted-foreground">
                        {configSummary(tool)}
                      </span>
                      <span className="rounded border border-border bg-background px-2 py-1 text-[11px] text-muted-foreground">
                        {planAllowed ? 'Plan available' : 'Plan upgrade required'}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-3 w-full">
                  {canConfigure && primaryHref && (
                    <Button asChild size="sm" variant={enabled ? 'default' : 'outline'} className="h-8 transition-all">
                      <Link to={primaryHref}>
                        {enabled ? 'Manage & Configure' : 'Configure Setup'}
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Link>
                    </Button>
                  )}
                  {canConfigure && enabled && !primaryHref && actions.map((action) => (
                    <Button key={action.href} asChild size="sm" variant={action.label === 'Open' ? 'default' : 'outline'} className="h-8">
                      <Link to={action.href}>
                        {action.label}
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Link>
                    </Button>
                  ))}
                  {canConfigure && !enabled && !primaryHref && (
                    <p className="basis-full text-[12px] leading-relaxed text-muted-foreground">
                      Enable this tool using the switch above to configure setup and view records.
                    </p>
                  )}
                  {!planAllowed && (
                    <>
                      <p className="basis-full text-[12px] leading-relaxed text-muted-foreground">
                        {tool.locked_reason || 'Upgrade this business plan before enabling or configuring this tool.'}
                      </p>
                      <Button asChild size="sm" variant="link" className="h-8 px-0 text-[12px]">
                        <Link to="/dashboard/billing">
                          View plan
                          <Lock className="h-3.5 w-3.5" />
                        </Link>
                      </Button>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <ConfirmActionDialog
        open={!!pendingToolToggle}
        onOpenChange={(open) => {
          if (!open && !updateConfig.isPending) {
            setPendingToolToggle(null);
          }
        }}
        title={`Disable ${pendingToolToggle?.label || 'workflow'}?`}
        description={`${pendingToolToggle?.label || 'This workflow'} will stop being available to AWA after this change is saved. Existing records remain visible in the dashboard.`}
        confirmLabel="Disable workflow"
        variant="destructive"
        isLoading={updateConfig.isPending}
        onConfirm={() => {
          if (!pendingToolToggle) return;
          void applyToolToggle(pendingToolToggle.key, pendingToolToggle.checked)
            .then(() => setPendingToolToggle(null))
            .catch(() => {});
        }}
      />
    </div>
  );
}
