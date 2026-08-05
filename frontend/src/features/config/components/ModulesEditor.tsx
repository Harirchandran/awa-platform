import { useEffect, useState } from 'react';
import { Controller, useFormContext, useWatch, type Path } from 'react-hook-form';
import { useSearchParams } from 'react-router';
import { useTenantTools } from '@/features/tools/api';
import type { TenantConfig } from '@/types/config.types';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';

function labelize(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function ModulesEditor() {
  const { control } = useFormContext<TenantConfig>();
  const [searchParams] = useSearchParams();
  const { data: toolsData } = useTenantTools();
  const focusedTool = searchParams.get('tool');
  const modules = useWatch({ control, name: 'modules' }) || {};
  const fetchedToolKeys = toolsData?.tools.map((tool) => tool.key) || [];
  const toolByKey = new Map(toolsData?.tools.map((tool) => [tool.key, tool]) || []);
  const moduleKeys = [
    ...fetchedToolKeys,
    ...Object.keys(modules).filter((key) => !fetchedToolKeys.includes(key)),
  ];

  const [pendingDisable, setPendingDisable] = useState<{ key: string; onChange: (val: boolean) => void } | null>(null);

  useEffect(() => {
    if (!focusedTool) return;
    document.getElementById(`tool-module-${focusedTool}`)?.scrollIntoView({ block: 'center' });
  }, [focusedTool]);

  return (
    <div className="w-full max-w-4xl space-y-6">
      <div>
        <h3 className="text-lg font-medium">Tool Modules</h3>
        <p className="text-sm text-muted-foreground">
          Enable or disable customer workflows here, then configure the details each workflow needs before sharing AWA Chat.
        </p>
      </div>
      <div className="divide-y rounded-lg border bg-card">
        {moduleKeys.map((moduleKey) => {
          const moduleConfig = modules[moduleKey];
          const tool = toolByKey.get(moduleKey);
          const fieldCount = moduleConfig?.fields?.length || 0;
          return (
            <div
              key={moduleKey}
              id={`tool-module-${moduleKey}`}
              className={cn(
                'flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between',
                focusedTool === moduleKey && 'bg-primary/5 ring-1 ring-primary/30'
              )}
            >
              <div className="space-y-1">
                <Label className="text-sm font-medium">{tool?.label || labelize(moduleKey)}</Label>
                <p className="text-xs text-muted-foreground">{tool?.description || 'Custom workflow module.'}</p>
                {fieldCount > 0 && (
                  <p className="text-xs text-muted-foreground">{fieldCount} required field{fieldCount === 1 ? '' : 's'} configured</p>
                )}
              </div>
              <Controller
                control={control}
                name={`modules.${moduleKey}.enabled` as Path<TenantConfig>}
                render={({ field }) => {
                  const isCritical = ['booking', 'order', 'orders', 'payments', 'reminders'].includes(moduleKey);
                  return (
                    <Switch
                      checked={Boolean(field.value)}
                      onCheckedChange={(checked) => {
                        if (!checked && isCritical) {
                          setPendingDisable({ key: moduleKey, onChange: field.onChange });
                        } else {
                          field.onChange(checked);
                        }
                      }}
                      aria-label={`Toggle ${tool?.label || labelize(moduleKey)} workflow`}
                    />
                  );
                }}
              />
            </div>
          );
        })}
      </div>

      <ConfirmActionDialog
        open={!!pendingDisable}
        onOpenChange={(open) => !open && setPendingDisable(null)}
        title={`Disable ${pendingDisable ? labelize(pendingDisable.key) : ''} workflow?`}
        confirmLabel="Disable workflow"
        variant="destructive"
        onConfirm={() => {
          if (pendingDisable) {
            pendingDisable.onChange(false);
            setPendingDisable(null);
          }
        }}
        description={
          <div className="space-y-2 pt-2 text-sm text-muted-foreground">
            {pendingDisable?.key === 'booking' && (
              <span className="block">
                Customers will no longer be able to book appointments through AWA until this is turned back on.
              </span>
            )}
            {pendingDisable?.key === 'orders' && (
              <span className="block">
                Customers will no longer be able to browse catalogs or place orders through AWA until this is turned back on.
              </span>
            )}
            {pendingDisable?.key === 'payments' && (
              <span className="block">
                Customers will no longer be able to make payments or receive invoices through AWA until this is turned back on.
              </span>
            )}
            {pendingDisable?.key === 'reminders' && (
              <span className="block">
                AWA will stop sending automated campaigns and follow-up notifications to your customers until this is turned back on.
              </span>
            )}
            <span className="block text-[11px] text-muted-foreground mt-2 font-medium">
              Note: You must click "Review & Sync" at the bottom of the page to apply this change.
            </span>
          </div>
        }
      />
    </div>
  );
}
