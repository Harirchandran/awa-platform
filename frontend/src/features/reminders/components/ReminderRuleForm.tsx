import { useState, useRef } from 'react';
import { Loader2, Plus } from 'lucide-react';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Textarea } from '@/components/ui/textarea';
import { getApiErrorMessage } from '@/lib/api-errors';
import {
  useCreateReminderRule,
  type CreateReminderRulePayload,
  type ReminderChannel,
  type ReminderTrigger,
} from '../api';
import { CHANNEL_COPY, TRIGGER_COPY } from '../constants';

/* ── types ─────────────────────────────────────────────────── */

type RuleFormState = {
  name: string;
  trigger_type: ReminderTrigger;
  offset_minutes: string; // string of computed minutes (value * factor)
  channel: ReminderChannel;
  template_text: string;
  quiet_hours_start: string;
  quiet_hours_end: string;
};

type FieldErrors = {
  name?: string;
  template_text?: string;
  offset?: string;
};

/* ── helpers ────────────────────────────────────────────────── */

const emptyRuleForm: RuleFormState = {
  name: '',
  trigger_type: 'manual',
  offset_minutes: '0',
  channel: 'whatsapp',
  template_text: '',
  quiet_hours_start: '',
  quiet_hours_end: '',
};

function computeOffsetMinutes(value: string, unit: 'minutes' | 'hours' | 'days'): string {
  const val = parseInt(value) || 0;
  const factor = unit === 'hours' ? 60 : unit === 'days' ? 1440 : 1;
  return String(val * factor);
}

function getHelperText(offsetValue: string, offsetUnit: 'minutes' | 'hours' | 'days'): string {
  const val = parseInt(offsetValue) || 0;
  const absVal = Math.abs(val);
  const relation = val < 0 ? 'before the event' : 'after the event';

  if (offsetUnit === 'hours') {
    return `${absVal} hour${absVal !== 1 ? 's' : ''} ${relation} = ${val * 60} minutes. AWA will schedule the follow-up using this converted value.`;
  }
  if (offsetUnit === 'days') {
    return `${absVal} day${absVal !== 1 ? 's' : ''} ${relation} = ${val * 1440} minutes. AWA will schedule the follow-up using this converted value.`;
  }
  return `${absVal} minute${absVal !== 1 ? 's' : ''} ${relation}. AWA will schedule the follow-up using this value.`;
}

function validateForm(form: RuleFormState, offsetValue: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!form.name.trim()) {
    errors.name = 'Rule name is required.';
  }
  if (!form.template_text.trim()) {
    errors.template_text = 'Message template is required.';
  }
  const parsedOffset = parseInt(offsetValue);
  if (Number.isNaN(parsedOffset)) {
    errors.offset = 'Timing offset must be a valid number.';
  }
  return errors;
}

/* ── component ──────────────────────────────────────────────── */

/**
 * Self-contained Sheet panel for creating a new reminder rule.
 * Renders its own "New Rule" trigger button.
 * Form state is preserved while sheet is open; resets only on success or explicit Cancel.
 * Timing conversion: minutes×1, hours×60, days×1440 — value sent to backend as offset_minutes.
 */
export function ReminderRuleForm() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<RuleFormState>(emptyRuleForm);
  const [offsetValue, setOffsetValue] = useState('0');
  const [offsetUnit, setOffsetUnit] = useState<'minutes' | 'hours' | 'days'>('minutes');
  const [errors, setErrors] = useState<FieldErrors>({});

  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const insertVariable = (variable: string) => {
    const textarea = textareaRef.current;
    if (!textarea) {
      setForm((prev) => ({ ...prev, template_text: prev.template_text + variable }));
      return;
    }
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const text = form.template_text;
    const before = text.substring(0, start);
    const after = text.substring(end);
    const newText = before + variable + after;

    setForm((prev) => ({ ...prev, template_text: newText }));

    setTimeout(() => {
      textarea.focus();
      textarea.setSelectionRange(start + variable.length, start + variable.length);
    }, 0);
  };

  const createRule = useCreateReminderRule();

  /* ── field helpers ── */

  const updateName = (value: string) => {
    setForm((prev) => ({ ...prev, name: value }));
    if (errors.name) setErrors((prev) => ({ ...prev, name: undefined }));
  };

  const updateTrigger = (value: ReminderTrigger) => {
    setForm((prev) => ({ ...prev, trigger_type: value }));
  };

  const updateChannel = (value: ReminderChannel) => {
    setForm((prev) => ({ ...prev, channel: value }));
  };

  const updateOffset = (value: string, unit: 'minutes' | 'hours' | 'days') => {
    const computed = computeOffsetMinutes(value, unit);
    setForm((prev) => ({ ...prev, offset_minutes: computed }));
    if (errors.offset) setErrors((prev) => ({ ...prev, offset: undefined }));
  };

  const updateTemplate = (value: string) => {
    setForm((prev) => ({ ...prev, template_text: value }));
    if (errors.template_text) setErrors((prev) => ({ ...prev, template_text: undefined }));
  };

  /* ── submission ── */

  const handleSave = () => {
    const validationErrors = validateForm(form, offsetValue);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    setErrors({});

    const payload: CreateReminderRulePayload = {
      name: form.name.trim(),
      trigger_type: form.trigger_type,
      offset_minutes: parseInt(form.offset_minutes) || 0,
      channel: form.channel,
      template_text: form.template_text.trim(),
      is_active: true,
      quiet_hours_start: form.quiet_hours_start ? form.quiet_hours_start : null,
      quiet_hours_end: form.quiet_hours_end ? form.quiet_hours_end : null,
    };

    createRule.mutate(payload, {
      onSuccess: () => {
        toast.success('Follow-up rule added');
        resetForm();
        setOpen(false);
      },
      onError: (error) =>
        toast.error(getApiErrorMessage(error, 'Could not add follow-up rule')),
    });
  };

  const resetForm = () => {
    setForm(emptyRuleForm);
    setOffsetValue('0');
    setOffsetUnit('minutes');
    setErrors({});
  };

  /* Explicit cancel clears form; incidental close (Escape/backdrop) preserves it */
  const handleCancel = () => {
    resetForm();
    setOpen(false);
  };

  /* Backdrop/Escape only closes — preserves typing */
  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen && !createRule.isPending) setOpen(false);
  };

  const helperText = getHelperText(offsetValue, offsetUnit);

  /* ── render ── */

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-8 text-[13px]"
        onClick={() => setOpen(true)}
        id="new-rule-trigger"
      >
        <Plus className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
        New Rule
      </Button>

      <Sheet open={open} onOpenChange={handleOpenChange}>
        <SheetContent
          className="flex flex-col overflow-y-auto sm:max-w-md"
          aria-labelledby="rule-sheet-title"
          aria-describedby="rule-sheet-desc"
        >
          <SheetHeader className="shrink-0">
            <SheetTitle id="rule-sheet-title">New follow-up rule</SheetTitle>
            <SheetDescription id="rule-sheet-desc">
              Set up an automated follow-up message that AWA will schedule when a trigger event occurs.
            </SheetDescription>
          </SheetHeader>

          <div className="mt-6 flex-1 space-y-5">
            {/* Name */}
            <div className="space-y-1.5">
              <Label htmlFor="rule-name">
                Rule name{' '}
                <span aria-hidden="true" className="text-destructive">
                  *
                </span>
              </Label>
              <Input
                id="rule-name"
                value={form.name}
                onChange={(e) => updateName(e.target.value)}
                placeholder="e.g. Booking reminder, Post-payment follow-up"
                aria-describedby={errors.name ? 'rule-name-error' : undefined}
                aria-invalid={!!errors.name}
                aria-required="true"
              />
              {errors.name && (
                <p id="rule-name-error" role="alert" className="text-[12px] text-destructive">
                  {errors.name}
                </p>
              )}
            </div>

            {/* Trigger */}
            <div className="space-y-1.5">
              <Label htmlFor="rule-trigger">
                Trigger{' '}
                <span aria-hidden="true" className="text-destructive">
                  *
                </span>
              </Label>
              <Select
                value={form.trigger_type}
                onValueChange={(v) => updateTrigger(v as ReminderTrigger)}
              >
                <SelectTrigger id="rule-trigger" aria-required="true">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(TRIGGER_COPY).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                When this event occurs, AWA will schedule the follow-up message.
              </p>
            </div>

            {/* Channel */}
            <div className="space-y-1.5">
              <Label htmlFor="rule-channel">
                Channel{' '}
                <span aria-hidden="true" className="text-destructive">
                  *
                </span>
              </Label>
              <Select
                value={form.channel}
                onValueChange={(v) => updateChannel(v as ReminderChannel)}
              >
                <SelectTrigger id="rule-channel" aria-required="true">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(CHANNEL_COPY).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Timing */}
            <div className="space-y-1.5">
              <Label htmlFor="rule-offset-value">
                Send offset{' '}
                <span aria-hidden="true" className="text-destructive">
                  *
                </span>
              </Label>
              <div className="flex gap-2">
                <Input
                  id="rule-offset-value"
                  type="number"
                  className="flex-1"
                  value={offsetValue}
                  onChange={(e) => {
                    const val = e.target.value;
                    setOffsetValue(val);
                    updateOffset(val, offsetUnit);
                  }}
                  aria-describedby={errors.offset ? 'rule-offset-error' : 'rule-offset-helper'}
                  aria-invalid={!!errors.offset}
                  aria-required="true"
                />
                <Select
                  value={offsetUnit}
                  onValueChange={(v: 'minutes' | 'hours' | 'days') => {
                    setOffsetUnit(v);
                    updateOffset(offsetValue, v);
                  }}
                >
                  <SelectTrigger className="w-[110px]" aria-label="Time unit">
                    <SelectValue placeholder="Unit" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="minutes">Minutes</SelectItem>
                    <SelectItem value="hours">Hours</SelectItem>
                    <SelectItem value="days">Days</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {errors.offset ? (
                <p id="rule-offset-error" role="alert" className="text-[12px] text-destructive">
                  {errors.offset}
                </p>
              ) : (
                <p id="rule-offset-helper" className="text-[11px] text-muted-foreground">
                  {helperText}
                </p>
              )}
            </div>

            {/* Quiet Hours */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="quiet-hours-start">Quiet hours start</Label>
                <Input
                  id="quiet-hours-start"
                  type="time"
                  value={form.quiet_hours_start}
                  onChange={(e) => setForm((prev) => ({ ...prev, quiet_hours_start: e.target.value }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="quiet-hours-end">Quiet hours end</Label>
                <Input
                  id="quiet-hours-end"
                  type="time"
                  value={form.quiet_hours_end}
                  onChange={(e) => setForm((prev) => ({ ...prev, quiet_hours_end: e.target.value }))}
                />
              </div>
            </div>

            {/* Template */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="rule-template">
                  Message template{' '}
                  <span aria-hidden="true" className="text-destructive">
                    *
                  </span>
                </Label>
              </div>

              <div className="flex flex-wrap gap-1.5 pb-1">
                {['{{customer_name}}', '{{booking_date}}', '{{booking_time}}', '{{total_amount}}'].map((variable) => (
                  <button
                    key={variable}
                    type="button"
                    onClick={() => insertVariable(variable)}
                    className="inline-flex items-center rounded-md bg-secondary px-2 py-1 text-xs font-medium text-secondary-foreground hover:bg-secondary/80 transition-colors"
                  >
                    {variable}
                  </button>
                ))}
              </div>

              <Textarea
                ref={textareaRef}
                id="rule-template"
                rows={5}
                value={form.template_text}
                onChange={(e) => updateTemplate(e.target.value)}
                placeholder="Hi {{customer_name}}, this is a reminder about your upcoming booking on {{booking_date}}…"
                aria-describedby={
                  errors.template_text ? 'rule-template-error' : 'rule-template-helper'
                }
                aria-invalid={!!errors.template_text}
                aria-required="true"
              />
              {errors.template_text ? (
                <p id="rule-template-error" role="alert" className="text-[12px] text-destructive">
                  {errors.template_text}
                </p>
              ) : (
                <p id="rule-template-helper" className="text-[11px] text-muted-foreground">
                  This message will be sent to customers when the rule trigger fires.
                </p>
              )}
            </div>
          </div>

          {/* Footer actions */}
          <div className="mt-6 flex shrink-0 gap-2 border-t pt-4">
            <Button
              type="button"
              variant="ghost"
              className="flex-1"
              onClick={handleCancel}
              disabled={createRule.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              className="flex-1"
              onClick={handleSave}
              disabled={createRule.isPending}
            >
              {createRule.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
              )}
              Add Rule
            </Button>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
