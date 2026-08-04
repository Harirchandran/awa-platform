import { useState } from 'react';
import { useFormContext } from 'react-hook-form';
import type { BookingSettings, ModuleField, TenantConfig, WorkingHours } from '@/types/config.types';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RotateCcw, Settings2 } from 'lucide-react';
const RECOMMENDED_QUESTIONS: ModuleField[] = [
  { key: 'customer_name', label: 'Customer name', type: 'string', required: true },
  { key: 'service_id', label: 'Service needed', type: 'select', required: true, options_from: 'services' },
  { key: 'preferred_date', label: 'Preferred date', type: 'date', required: true },
  { key: 'preferred_time', label: 'Preferred time', type: 'time', required: true },
  { key: 'notes', label: 'Anything else we should know?', type: 'string', required: false },
];
const DEFAULT_BOOKING_SETTINGS: BookingSettings = {
  confirmation_mode: 'auto_confirm',
  allow_customer_reschedule: true,
  allow_customer_cancel: true,
  minimum_notice_minutes: 120,
  advance_booking_days: 30,
  slot_interval_minutes: 30,
  buffer_before_mins: 0,
  buffer_after_mins: 0,
  max_bookings_per_day: undefined,
  cancellation_cutoff_minutes: 120,
  reminder_offsets_minutes: [1440, 60],
  approval_required_for: [],
};
const DEFAULT_WORKING_HOURS: WorkingHours = {
  monday: { is_open: true, open_time: '09:00', close_time: '18:00' },
  tuesday: { is_open: true, open_time: '09:00', close_time: '18:00' },
  wednesday: { is_open: true, open_time: '09:00', close_time: '18:00' },
  thursday: { is_open: true, open_time: '09:00', close_time: '18:00' },
  friday: { is_open: true, open_time: '09:00', close_time: '18:00' },
  saturday: { is_open: false, open_time: '09:00', close_time: '18:00' },
  sunday: { is_open: false, open_time: '09:00', close_time: '18:00' },
};
type ResetAction = {
  title: string;
  description: string;
  confirmLabel: string;
  run: () => void;
};
function parseNumberList(value: string): number[] {
  return value
    .split(',')
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isFinite(item) && item >= 0);
}
function parseStringList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}
export function BookingAdvancedEditor() {
  const { register, watch, setValue } = useFormContext<TenantConfig>();
  const [pendingReset, setPendingReset] = useState<ResetAction | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const reminderOffsets = watch('booking_settings.reminder_offsets_minutes') || [];
  const approvalRequiredFor = watch('booking_settings.approval_required_for') || [];
  const resetActions: ResetAction[] = [
    {
      title: 'Restore recommended questions',
      description: 'Replace the booking questions with a simple recommended set: name, service, date, time, and notes.',
      confirmLabel: 'Restore Questions',
      run: () => setValue('modules.booking.fields', RECOMMENDED_QUESTIONS, { shouldDirty: true }),
    },
    {
      title: 'Reset booking rules',
      description: 'Restore confirmation, reminder, cancellation, and advanced timing rules to recommended defaults.',
      confirmLabel: 'Reset Rules',
      run: () => setValue('booking_settings', DEFAULT_BOOKING_SETTINGS, { shouldDirty: true }),
    },
    {
      title: 'Reset booking setup',
      description: 'Restore recommended questions, rules, and weekly availability. Existing bookings and services are not deleted.',
      confirmLabel: 'Reset Setup',
      run: () => {
        setValue('modules.booking.fields', RECOMMENDED_QUESTIONS, { shouldDirty: true });
        setValue('booking_settings', DEFAULT_BOOKING_SETTINGS, { shouldDirty: true });
        setValue('working_hours', DEFAULT_WORKING_HOURS, { shouldDirty: true });
      },
    },
  ];
  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-medium">Advanced Customization</h3>
          <p className="text-sm text-muted-foreground">
            Fine-tune booking behavior when the normal setup is not enough. These settings can stay unchanged for most local businesses.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="w-full shrink-0 sm:w-auto"
        >
          {showAdvanced ? 'Hide Advanced Settings' : 'Show Advanced Settings'}
        </Button>
      </div>
      {showAdvanced && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardContent className="space-y-4 p-4 sm:p-5">
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border bg-background">
                    <Settings2 className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold">Slot and capacity controls</h4>
                    <p className="mt-1 text-xs text-muted-foreground">Use these when bookings need spacing, limits, or stricter cancellation windows.</p>
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Buffer before booking</Label>
                    <Input type="number" min="0" step="5" placeholder="0" {...register('booking_settings.buffer_before_mins', { valueAsNumber: true })} />
                    <p className="text-xs text-muted-foreground">Minutes blocked before each booking.</p>
                  </div>
                  <div className="space-y-2">
                    <Label>Buffer after booking</Label>
                    <Input type="number" min="0" step="5" placeholder="0" {...register('booking_settings.buffer_after_mins', { valueAsNumber: true })} />
                    <p className="text-xs text-muted-foreground">Minutes blocked after each booking.</p>
                  </div>
                  <div className="space-y-2">
                    <Label>Maximum bookings per day</Label>
                    <Input type="number" min="1" step="1" placeholder="No limit" {...register('booking_settings.max_bookings_per_day', { valueAsNumber: true })} />
                    <p className="text-xs text-muted-foreground">Leave empty if there is no daily limit.</p>
                  </div>
                  <div className="space-y-2">
                    <Label>Cancellation cutoff</Label>
                    <Input type="number" min="0" step="15" placeholder="120" {...register('booking_settings.cancellation_cutoff_minutes', { valueAsNumber: true })} />
                    <p className="text-xs text-muted-foreground">Minutes before appointment when cancellation closes.</p>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="space-y-4 p-4 sm:p-5">
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border bg-background">
                    <Settings2 className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold">Automation details</h4>
                    <p className="mt-1 text-xs text-muted-foreground">Control reminders and special approval cases for the booking assistant.</p>
                  </div>
                </div>
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label>Reminder timing</Label>
                    <Input
                      value={reminderOffsets.join(', ')}
                      placeholder="1440, 60"
                      onChange={(event) => setValue('booking_settings.reminder_offsets_minutes', parseNumberList(event.target.value), { shouldDirty: true })}
                    />
                    <p className="text-xs text-muted-foreground">Minutes before booking. Example: 1440, 60 means 24 hours and 1 hour before.</p>
                  </div>
                  <div className="space-y-2">
                    <Label>Always ask staff approval for</Label>
                    <Input
                      value={approvalRequiredFor.join(', ')}
                      placeholder="e.g. high value service, outside hours"
                      onChange={(event) => setValue('booking_settings.approval_required_for', parseStringList(event.target.value), { shouldDirty: true })}
                    />
                    <p className="text-xs text-muted-foreground">Optional comma-separated conditions or service names for manual approval.</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
          <div className="space-y-3">
            <div>
              <h4 className="text-sm font-semibold">Reset Options</h4>
              <p className="mt-1 text-xs text-muted-foreground">Reset changes only affect setup values. Existing bookings are never deleted.</p>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              {resetActions.map((action) => (
                <Card key={action.title}>
                  <CardContent className="flex h-full flex-col gap-4 p-4">
                    <div>
                      <p className="text-sm font-semibold">{action.title}</p>
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{action.description}</p>
                    </div>
                    <Button type="button" variant="outline" className="mt-auto justify-start" onClick={() => setPendingReset(action)}>
                      <RotateCcw className="h-4 w-4" />
                      {action.confirmLabel}
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        </>
      )}
      <ConfirmActionDialog
        open={!!pendingReset}
        onOpenChange={(open) => !open && setPendingReset(null)}
        title={pendingReset?.title || 'Reset booking setup?'}
        description={`${pendingReset?.description || ''} This will update the setup form only. Press Save Booking Setup afterwards to apply it.`}
        confirmLabel={pendingReset?.confirmLabel || 'Reset'}
        variant="warning"
        onConfirm={() => {
          pendingReset?.run();
          setPendingReset(null);
        }}
      />
    </div>
  );
}
