import { useEffect, useState, useMemo, type SyntheticEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useForm, FormProvider, useWatch } from 'react-hook-form';
import { useQueryClient } from '@tanstack/react-query';
import { useTenantStore } from '@/stores/tenant.store';
import type { TenantConfig } from '@/types/config.types';
import { useTenantConfig, useUpdateConfig } from '../api';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';

import { ConfigSkeleton } from '@/components/ui/loading-states';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { AlertTriangle, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { getApiErrorMessage } from '@/lib/api-errors';
import { cn } from '@/lib/utils';
import { validateTenantConfig } from '@/lib/tenant-config-validation';
import { normalizeAiMode, normalizeAllowedAiModes } from '@/lib/ai-mode';
import { WorkingHoursEditor } from './WorkingHoursEditor';
import { GeneralConfigEditor } from './GeneralConfigEditor';
import { ReceptionistBehaviorEditor } from './ReceptionistBehaviorEditor';
import { StickyBar } from '@/components/shared/StickyFormActions';

const VALID_CONFIG_TABS = new Set(['general', 'hours', 'receptionist', 'persona', 'json']);

const TAB_FIELDS_MAPPING: Record<string, string[]> = {
  general: [
    'business_name',
    'business_type',
    'ai_mode',
    'timezone',
    'default_language',
    'ai_tone',
    'greeting_message',
    'fallback_message',
    'escalation_message',
    'custom_instructions',
    'escalation_phone',
    'escalation_email',
    'security',
    'ignored_numbers',
  ],
  hours: ['working_hours'],
  receptionist: [],
  persona: [],
  json: [],
};

function getErrorsForTab(tab: string, errorsList: string[]): string[] {
  if (errorsList.length === 0) return [];
  return errorsList.filter((err) => {
    const lower = err.toLowerCase();
    switch (tab) {
      case 'general':
        return (
          lower.includes('business name') ||
          lower.includes('business type') ||
          lower.includes('operating timezone') ||
          lower.includes('primary language') ||
          lower.includes('ai tone') ||
          lower.includes('greeting message') ||
          lower.includes('fallback message') ||
          lower.includes('escalation message') ||
          lower.includes('business instructions') ||
          lower.includes('escalation email') ||
          lower.includes('escalation phone') ||
          lower.includes('do-not-auto-reply') ||
          lower.includes('ai mode') ||
          lower.includes('ai capability')
        );
      case 'hours':
        return lower.includes('hours') || lower.includes('timezone') || lower.includes('booking') || lower.includes('cutoff') || lower.includes('limit');
      default:
        return false;
    }
  });
}
type TenantConfigWithPolicy = TenantConfig & {
  __planPolicy?: {
    plan_code: string;
    allowed_ai_modes?: string[];
    ai_capability_profiles?: Array<{
      code: string;
      label: string;
      runtime_engine: string;
      summary: string;
      ui_description: string;
      allowed: boolean;
      locked_reason?: string | null;
    }>;
    ai_capability_policy?: {
      requested_mode: string;
      effective_mode: string;
      runtime_engine: string;
      label: string;
      summary: string;
      ui_description: string;
      downgraded: boolean;
      locked_reason?: string | null;
    };
  };
};
export function ConfigTabs() {
  const queryClient = useQueryClient();
  const activeTenantId = useTenantStore(state => state.activeTenantId);
  const { data: configData, isLoading: isFetching } = useTenantConfig();
  const updateConfig = useUpdateConfig();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab') || 'general';
  const [hasUserEdited, setHasUserEdited] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const activeTab = VALID_CONFIG_TABS.has(requestedTab) ? requestedTab : 'general';
  const methods = useForm<TenantConfig>({
    defaultValues: configData || {}
  });
  const { reset, handleSubmit, control, setValue, formState: { isDirty, isSubmitting, dirtyFields } } = methods;
  const activeTenantMode = useWatch({ control, name: 'ai_mode' });
  const liveConfig = useWatch({ control });
  const planPolicy = (configData as TenantConfigWithPolicy | undefined)?.__planPolicy;
  const allowedAiModes = normalizeAllowedAiModes(planPolicy?.allowed_ai_modes || ['standard']);
  const advancedModeAvailable = normalizeAiMode(activeTenantMode) === 'advanced' && allowedAiModes.includes('advanced');
  const visibleActiveTab = activeTab === 'persona' && !advancedModeAvailable ? 'general' : activeTab;

  const liveValidationErrors = useMemo(() => {
    if (!hasUserEdited || !configData) return [];
    try {
      const completeData: TenantConfig = {
        ...configData,
        ...liveConfig,
        ai_mode: normalizeAiMode(liveConfig.ai_mode || configData.ai_mode),
        timezone: liveConfig.timezone || configData.timezone || 'UTC',
      } as TenantConfig;
      return validateTenantConfig(completeData);
    } catch {
      return [];
    }
  }, [liveConfig, configData, hasUserEdited]);

  const tabTriggerClass = 'w-auto lg:w-full shrink-0 justify-start data-[state=active]:bg-primary/10 data-[state=active]:text-primary';
  const renderTabTrigger = (value: string, label: string) => {
    const fields = TAB_FIELDS_MAPPING[value] || [];
    const isTabDirty = fields.some((field) => dirtyFields[field as keyof TenantConfig]);
    const tabErrors = getErrorsForTab(value, liveValidationErrors);
    const hasErrors = tabErrors.length > 0;

    return (
      <TabsTrigger value={value} className={cn(tabTriggerClass, "relative pr-6")}>
        <span>{label}</span>
        {hasErrors ? (
          <span className="absolute right-2 top-1/2 -translate-y-1/2 flex h-2.5 w-2.5" title={`${tabErrors.length} error(s)`}>
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500"></span>
          </span>
        ) : isTabDirty ? (
          <span className="absolute right-2 top-1/2 -translate-y-1/2 flex h-2 w-2" title="Unsaved changes">
            <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
          </span>
        ) : null}
      </TabsTrigger>
    );
  };

  const markUserEdited = (event: SyntheticEvent<HTMLFormElement>) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.closest('[role="tab"], a[href]')) return;
    if (target.closest('input, textarea, select, button, [role="switch"], [role="checkbox"], [role="combobox"]')) {
      setHasUserEdited(true);
    }
  };
  useEffect(() => {
    if (configData) {
      reset(configData);
      setValue('ai_mode', normalizeAiMode(configData.ai_mode) as TenantConfig['ai_mode'], { shouldDirty: false, shouldTouch: false, shouldValidate: false });
      setValue('timezone', configData.timezone || 'UTC', { shouldDirty: false, shouldTouch: false, shouldValidate: false });
    }
  }, [configData, reset, setValue]);

  const blocker = {
    state: 'unblocked' as 'unblocked' | 'blocked',
    reset: () => {},
    proceed: () => {},
  };

  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty && hasUserEdited) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty, hasUserEdited]);
  if (isFetching || !configData) {
    return (
      <ConfigSkeleton
        label="Loading business configuration"
        description="This may take a few seconds in local demo mode while the current setup version and plan rules are loaded."
      />
    );
  }
  const saveConfig = (data: TenantConfig) => {
    updateConfig.mutate(data, {
      onSuccess: () => {
        toast.success("Business Setup saved successfully!");
        reset(data); // Resets the 'dirty' state tracking
        setHasUserEdited(false);
        setValidationErrors([]);
      },
      onError: (error: unknown) => {
        const status = (error as { response?: { status?: number } })?.response?.status;
        if (status === 409) {
          toast((t) => (
            <div className="flex flex-col gap-2 p-1">
              <span className="font-semibold text-sm text-amber-600 dark:text-amber-500">Version Conflict</span>
              <span className="text-xs text-muted-foreground">Configuration was updated in another session. Reload to pull the latest version.</span>
              <div className="flex gap-2 justify-end mt-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={() => toast.dismiss(t.id)}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  className="h-7 text-xs bg-amber-600 hover:bg-amber-700 text-white"
                  onClick={() => {
                    queryClient.invalidateQueries({ queryKey: ['config', activeTenantId] });
                    toast.dismiss(t.id);
                  }}
                >
                  Reload
                </Button>
              </div>
            </div>
          ), { duration: 10000 });
        } else {
          toast.error(getApiErrorMessage(error, "Failed to save configuration."));
        }
      }
    });
  };
  const onSubmit = (data: TenantConfig) => {
    const completeData: TenantConfig = {
      ...configData,
      ...data,
      ai_mode: normalizeAiMode(data.ai_mode || configData.ai_mode) as TenantConfig['ai_mode'],
      timezone: data.timezone || configData.timezone || 'UTC',
    };
    const errors = validateTenantConfig(completeData);
    if (!allowedAiModes.includes(normalizeAiMode(completeData.ai_mode))) {
      errors.push(
        `${completeData.ai_mode} AI mode is not available on the current ${planPolicy?.plan_code || 'business'} plan.`
      );
    }
    setValidationErrors(errors);
    if (errors.length > 0) {
      toast.error('Fix the highlighted configuration issues before syncing.');
      return;
    }
    saveConfig(completeData);
  };
  const discardChanges = () => {
    if (!configData) return;
    reset(configData);
    setHasUserEdited(false);
    setValidationErrors([]);
  };
  const handleTabChange = (value: string) => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('tab', value);
    if (value !== 'tools') {
      nextParams.delete('tool');
    }
    setSearchParams(nextParams, { replace: true });
  };
  return (
    <FormProvider {...methods}>
      <form
        onPointerDownCapture={markUserEdited}
        onKeyDownCapture={markUserEdited}
        onSubmit={handleSubmit(onSubmit)}
        className="space-y-6 pb-24 relative min-h-full flex flex-col"
      >
        {validationErrors.length > 0 && (
          <div className="rounded-md border border-destructive/20 bg-destructive/5 p-4 text-[13px] text-destructive">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-medium">Review these configuration issues before syncing</p>
                <ul className="mt-2 list-disc space-y-1 pl-4">
                  {validationErrors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        )}

        <Tabs value={visibleActiveTab} onValueChange={handleTabChange} className="grid w-full items-start gap-4 lg:grid-cols-[12rem_minmax(0,1fr)] lg:gap-6">
          <TabsList className="flex h-auto w-full flex-row overflow-x-auto bg-transparent p-0 gap-1 border-b pb-2 lg:sticky lg:top-0 lg:flex-col lg:items-stretch lg:space-y-1 lg:border-b-0 lg:pb-0 shrink-0 scrollbar-none">
             <div className="hidden lg:block px-2 text-xs font-semibold uppercase text-muted-foreground">
               Business Setup
             </div>
             {renderTabTrigger('general', 'General')}
             {renderTabTrigger('hours', 'Hours')}
             {renderTabTrigger('receptionist', 'Receptionist')}
             {advancedModeAvailable && (
               <>
                  <div className="hidden lg:block px-2 text-xs font-semibold uppercase text-muted-foreground lg:mt-6">Advanced AI</div>
                  {renderTabTrigger('persona', 'Persona')}
               </>
             )}
             <div className="hidden lg:block px-2 text-xs font-semibold uppercase text-muted-foreground lg:mt-6">Advanced</div>
             {renderTabTrigger('json', 'Advanced JSON')}
          </TabsList>
          <div className="min-h-[640px] w-full min-w-0 rounded-lg border border-border bg-card p-4 shadow-sm sm:p-6">
            <TabsContent value="general" className="m-0 border-0 p-0">
               <GeneralConfigEditor
                 allowedAiModes={allowedAiModes}
                 planCode={planPolicy?.plan_code}
                 capabilityProfiles={planPolicy?.ai_capability_profiles}
                 activeCapabilityPolicy={planPolicy?.ai_capability_policy}
               />
            </TabsContent>
            <TabsContent value="hours" className="m-0 border-0 p-0"><WorkingHoursEditor /></TabsContent>
            <TabsContent value="receptionist" className="m-0 border-0 p-0"><ReceptionistBehaviorEditor /></TabsContent>
            {advancedModeAvailable && (
              <>
                 <TabsContent value="persona" className="m-0 border-0 p-0">
                   <div className="space-y-4">
                      <h3 className="text-lg font-medium">Advanced AI Guidance</h3>
                      <p className="text-sm text-muted-foreground">Persona settings shape the unified AWA agent. Backend plan and tool gates still decide what actions can commit.</p>
                      {/* Will build Persona component next */}
                   </div>
                 </TabsContent>
              </>
            )}
            <TabsContent value="json" className="m-0 border-0 p-0">
               <div className="w-full max-w-4xl">
                 <h3 className="text-lg font-medium mb-2">Advanced Setup JSON</h3>
                 <p className="mb-4 text-sm text-muted-foreground">
                   This is an advanced preview for support and debugging. Use the standard sections to edit safely.
                 </p>
                 <div className="overflow-x-auto rounded bg-muted p-4 font-mono text-xs text-muted-foreground">
                   <pre className="whitespace-pre-wrap break-words">
                    {JSON.stringify(liveConfig, null, 2)}
                   </pre>
                 </div>
                 <p className="text-xs text-muted-foreground mt-2">Edit natively inside the standard tabs to validate schema safely.</p>
               </div>
            </TabsContent>
          </div>
        </Tabs>
        <StickyBar.Root visible={isDirty && hasUserEdited}>
          <StickyBar.Message>
            You have unsaved changes in your configuration
          </StickyBar.Message>
          <StickyBar.Actions>
            <Button
              type="button"
              variant="ghost"
              onClick={discardChanges}
              disabled={updateConfig.isPending}
              className="text-[13px] h-9 outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              Discard
            </Button>
            <Button
              type="submit"
              disabled={updateConfig.isPending || isSubmitting}
              className="text-[13px] h-9 outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              {updateConfig.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />}
              Review & Sync
            </Button>
          </StickyBar.Actions>
        </StickyBar.Root>
      </form>
      <Dialog open={blocker.state === 'blocked'} onOpenChange={(open) => { if (!open) blocker.reset?.(); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Unsaved changes</DialogTitle>
            <DialogDescription>
              You have unsaved changes in your business setup. Navigating away will discard these changes.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => blocker.reset?.()}>
              Stay on Page
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => blocker.proceed?.()}
            >
              Discard and Leave
            </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
    </FormProvider>
  );
}
