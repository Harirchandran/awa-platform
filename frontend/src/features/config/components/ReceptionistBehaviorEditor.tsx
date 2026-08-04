import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Loader2, RotateCcw, Save } from 'lucide-react';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getApiErrorMessage } from '@/lib/api-errors';
import { cn } from '@/lib/utils';
import { useReceptionistBehavior, useResetReceptionistBehavior, useUpdateReceptionistBehavior } from '../api';
import type {
  BehaviorField,
  BehaviorOverrides,
  ChannelBehaviorOverrides,
  ChannelKey,
  ReceptionistBehaviorResponse,
} from '@/types/config.types';

const INHERIT = '__inherit__';

const FIELD_LABELS: Record<string, string> = {
  ask_need_first: 'Ask what the customer needs',
  menu_first: 'Show available options',
  hybrid: 'Hybrid',
  friendly: 'Friendly',
  formal: 'Formal',
  neutral: 'Neutral',
  concise: 'Concise',
  premium: 'Premium',
  one_question_at_a_time: 'One question at a time',
  compact: 'Compact',
  detailed: 'Detailed',
  single: 'Single',
  grouped: 'Grouped',
  follow_customer: 'Follow customer language',
  tenant_default: 'Use business default',
  local_language_first: 'Local language first',
  show_when_available: 'Show when available',
  when_asked: 'Show when asked',
  hide_unless_required: 'Hide unless required',
  brief: 'Brief',
  explain_when_asked: 'Explain when asked',
  explain_before_booking: 'Explain before booking',
  never_proactive: 'Never proactive',
  when_stuck: 'When stuck',
  early_offer: 'Offer early',
  during_off_hours: 'During off hours',
  faq_first: 'FAQ first',
  clarify_first: 'Clarify first',
  handoff_if_available: 'Handoff if available',
  show_available_workflows: 'Show available workflows',
  hide_menu_unless_asked: 'Hide unless asked',
};

const SELECT_FIELDS: Array<{ key: Exclude<BehaviorField, 'journey_priority' | 'channel_overrides'>; label: string; values: string[] }> = [
  { key: 'greeting_mode', label: 'Starting experience', values: ['ask_need_first', 'menu_first', 'hybrid'] },
  { key: 'tone', label: 'Tone', values: ['friendly', 'formal', 'neutral', 'concise', 'premium'] },
  { key: 'clarification_style', label: 'Clarification style', values: ['one_question_at_a_time', 'compact', 'detailed'] },
  { key: 'question_batching', label: 'Question batching', values: ['single', 'grouped'] },
  { key: 'language_policy', label: 'Language', values: ['follow_customer', 'tenant_default', 'local_language_first'] },
  { key: 'price_visibility', label: 'Prices', values: ['show_when_available', 'when_asked', 'hide_unless_required'] },
  { key: 'service_detail_policy', label: 'Service details', values: ['brief', 'explain_when_asked', 'explain_before_booking'] },
  { key: 'fallback_policy', label: 'Fallback', values: ['clarify_first', 'faq_first', 'handoff_if_available'] },
  { key: 'handoff_policy', label: 'Handoff', values: ['when_stuck', 'early_offer', 'during_off_hours', 'never_proactive'] },
  { key: 'menu_policy', label: 'Menu', values: ['show_available_workflows', 'hide_menu_unless_asked'] },
];

function label(value: string) {
  return FIELD_LABELS[value] || value.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
}

function fieldValue(data: ReceptionistBehaviorResponse, overrides: BehaviorOverrides, key: string): string {
  const value = overrides[key as keyof BehaviorOverrides];
  if (typeof value === 'string') return value;
  const inherited = data.inherited_profile[key as keyof typeof data.inherited_profile];
  return typeof inherited === 'string' ? inherited : '';
}

function activeJourney(data: ReceptionistBehaviorResponse, overrides: BehaviorOverrides): string[] {
  const override = overrides.journey_priority;
  if (Array.isArray(override)) return override;
  return data.effective_profile.journey_priority || data.inherited_profile.journey_priority || [];
}

function updateChannel(
  current: ChannelBehaviorOverrides,
  channel: ChannelKey,
  field: string,
  value: string,
): ChannelBehaviorOverrides {
  const next = { ...current, [channel]: { ...(current[channel] || {}) } };
  if (value === INHERIT) {
    delete next[channel]?.[field as keyof NonNullable<ChannelBehaviorOverrides[ChannelKey]>];
  } else {
    next[channel] = { ...(next[channel] || {}), [field]: value };
  }
  if (Object.keys(next[channel] || {}).length === 0) {
    delete next[channel];
  }
  return next;
}

function compactOverrides(overrides: BehaviorOverrides): BehaviorOverrides {
  return Object.fromEntries(
    Object.entries(overrides).filter(([, value]) => Array.isArray(value) ? value.length > 0 : Boolean(value)),
  ) as BehaviorOverrides;
}

export function ReceptionistBehaviorEditor() {
  const behaviorQuery = useReceptionistBehavior();
  const data = behaviorQuery.data;

  if (behaviorQuery.isLoading) {
    return <div className="text-[13px] text-muted-foreground">Loading receptionist behavior...</div>;
  }
  if (behaviorQuery.isError || !data) {
    return (
      <div className="space-y-3">
        <p className="text-[13px] text-destructive">Receptionist behavior settings are unavailable right now.</p>
        <Button type="button" variant="outline" onClick={() => behaviorQuery.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <ReceptionistBehaviorForm
      key={`${data.config_version}:${JSON.stringify(data.overrides)}:${JSON.stringify(data.channel_overrides)}`}
      data={data}
    />
  );
}

function ReceptionistBehaviorForm({ data }: { data: ReceptionistBehaviorResponse }) {
  const updateBehavior = useUpdateReceptionistBehavior();
  const resetBehavior = useResetReceptionistBehavior();
  const [overrides, setOverrides] = useState<BehaviorOverrides>(data.overrides || {});
  const [channelOverrides, setChannelOverrides] = useState<ChannelBehaviorOverrides>(data.channel_overrides || {});
  const editable = useMemo(() => new Set(data.editable_fields || []), [data.editable_fields]);
  const journey = activeJourney(data, overrides);
  const selectableWorkflows = data.workflows.filter(workflow => workflow.selectable_in_journey_priority);
  const unavailableWorkflows = data.workflows.filter(workflow => !workflow.selectable_in_journey_priority);
  const canSave = Boolean(data.preset_available);

  const setField = (key: BehaviorField, value: string) => {
    setOverrides(current => ({ ...current, [key]: value }));
  };
  const moveJourney = (index: number, direction: -1 | 1) => {
    const next = [...journey];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setOverrides(current => ({ ...current, journey_priority: next }));
  };
  const removeJourney = (key: string) => {
    setOverrides(current => ({ ...current, journey_priority: journey.filter(item => item !== key) }));
  };
  const addJourney = (key: string) => {
    if (!key || journey.includes(key)) return;
    setOverrides(current => ({ ...current, journey_priority: [...journey, key] }));
  };
  const save = () => {
    updateBehavior.mutate({
      expected_config_version: data.config_version,
      overrides: compactOverrides(overrides),
      channel_overrides: channelOverrides,
    }, {
      onSuccess: () => toast.success('Receptionist behavior saved'),
      onError: (error) => toast.error(getApiErrorMessage(error, 'Could not save receptionist behavior')),
    });
  };
  const reset = () => {
    resetBehavior.mutate(data.config_version, {
      onSuccess: () => toast.success('Receptionist behavior reset'),
      onError: (error) => toast.error(getApiErrorMessage(error, 'Could not reset receptionist behavior')),
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-lg font-medium">AI Receptionist Behavior</h3>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Configure how the receptionist opens, prioritizes, clarifies, and hands off inside preset boundaries.
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            {data.preset ? `${data.preset.label} preset v${data.preset.version}` : 'No active preset available'}
          </p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={reset} disabled={resetBehavior.isPending}>
            {resetBehavior.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
            Reset
          </Button>
          <Button type="button" onClick={save} disabled={!canSave || updateBehavior.isPending}>
            {updateBehavior.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save
          </Button>
        </div>
      </div>

      {!data.preset_available && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900">
          Behavior customization is unavailable until an active super-admin preset exists for this business type.
        </div>
      )}

      {data.warnings.length > 0 && (
        <div className="rounded-md border border-border bg-muted/40 p-3 text-[13px] text-muted-foreground">
          {data.warnings.map(warning => label(warning)).join(', ')}
        </div>
      )}

      <section className="space-y-3">
        <div>
          <h4 className="text-[15px] font-semibold">Workflow priority</h4>
          <p className="text-[13px] text-muted-foreground">Only workflows available to this tenant can be selected.</p>
        </div>
        <div className="space-y-2">
          {journey.map((module, index) => {
            const workflow = data.workflows.find(item => item.key === module);
            return (
              <div key={module} className="flex items-center justify-between rounded-md border bg-background px-3 py-2">
                <span className="text-[13px] font-medium">{workflow?.label || label(module)}</span>
                <div className="flex gap-1">
                  <Button type="button" variant="ghost" size="icon" onClick={() => moveJourney(index, -1)} disabled={!editable.has('journey_priority') || index === 0}>
                    <ArrowUp className="h-4 w-4" />
                  </Button>
                  <Button type="button" variant="ghost" size="icon" onClick={() => moveJourney(index, 1)} disabled={!editable.has('journey_priority') || index === journey.length - 1}>
                    <ArrowDown className="h-4 w-4" />
                  </Button>
                  <Button type="button" variant="ghost" onClick={() => removeJourney(module)} disabled={!editable.has('journey_priority')}>
                    Remove
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
        <Select onValueChange={addJourney} disabled={!editable.has('journey_priority') || !canSave}>
          <SelectTrigger className="max-w-sm">
            <SelectValue placeholder="Add available workflow" />
          </SelectTrigger>
          <SelectContent>
            {selectableWorkflows.filter(workflow => !journey.includes(workflow.key)).map(workflow => (
              <SelectItem key={workflow.key} value={workflow.key}>{workflow.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {unavailableWorkflows.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {unavailableWorkflows.map(workflow => (
              <span key={workflow.key} className="rounded-md border bg-muted px-2 py-1 text-xs text-muted-foreground">
                {workflow.label}: {workflow.allowed_by_plan ? 'Enable in Tools' : 'Unavailable'}
              </span>
            ))}
          </div>
        )}
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        {SELECT_FIELDS.map(field => {
          const locked = !editable.has(field.key);
          return (
            <label key={field.key} className={cn('space-y-2', locked && 'opacity-60')}>
              <span className="text-[13px] font-medium">{field.label}</span>
              <Select
                value={fieldValue(data, overrides, field.key)}
                onValueChange={(value) => setField(field.key, value)}
                disabled={locked || !canSave}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {field.values.map(value => (
                    <SelectItem key={value} value={value}>{label(value)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {locked && <span className="block text-xs text-muted-foreground">Locked by preset</span>}
            </label>
          );
        })}
      </section>

      <section className="space-y-4 border-t pt-4">
        <div>
          <h4 className="text-[15px] font-semibold">Channel overrides</h4>
          <p className="text-[13px] text-muted-foreground">Keep channel-specific differences narrow and presentation-only.</p>
        </div>
        {(['public_chat', 'whatsapp'] as ChannelKey[]).map(channel => (
          <div key={channel} className="grid gap-3 rounded-md border p-3 sm:grid-cols-2">
            <div className="sm:col-span-2 text-[13px] font-medium">{channel === 'public_chat' ? 'Public Chat' : 'WhatsApp'}</div>
            {(['greeting_mode', 'question_batching'] as const).map(field => (
              <label key={field} className="space-y-2">
                <span className="text-[13px] text-muted-foreground">{label(field)}</span>
                <Select
                  value={channelOverrides[channel]?.[field] || INHERIT}
                  onValueChange={(value) => setChannelOverrides(current => updateChannel(current, channel, field, value))}
                  disabled={!editable.has('channel_overrides') || !canSave}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={INHERIT}>Use default</SelectItem>
                    {(field === 'greeting_mode' ? ['ask_need_first', 'menu_first', 'hybrid'] : ['single', 'grouped']).map(value => (
                      <SelectItem key={value} value={value}>{label(value)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
            ))}
          </div>
        ))}
      </section>

      <section className="border-t pt-4">
        <h4 className="text-[15px] font-semibold">Effective preview</h4>
        <div className="mt-3 grid gap-2 text-[13px] sm:grid-cols-2">
          <div>Starting experience: <span className="font-medium">{label(data.effective_profile.greeting_mode)}</span></div>
          <div>Tone: <span className="font-medium">{label(data.effective_profile.tone)}</span></div>
          <div>Language: <span className="font-medium">{label(data.effective_profile.language_policy)}</span></div>
          <div>Fallback: <span className="font-medium">{label(data.effective_profile.fallback_policy)}</span></div>
        </div>
      </section>
    </div>
  );
}
