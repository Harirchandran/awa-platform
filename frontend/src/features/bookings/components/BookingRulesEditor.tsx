import { useFormContext } from 'react-hook-form';
import type { TenantConfig } from '@/types/config.types';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
export function BookingRulesEditor() {
  const { register, watch, setValue } = useFormContext<TenantConfig>();
  const settings = watch('booking_settings') || {};
  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h3 className="text-lg font-medium">Booking Rules</h3>
        <p className="text-sm text-muted-foreground">Choose how AWA should behave when customers request bookings on WhatsApp.</p>
      </div>
      <div className="space-y-4 rounded-lg border bg-card p-4">
        <div className="space-y-2">
          <Label>When a customer chooses a valid time</Label>
          <Select
            value={settings.confirmation_mode || 'auto_confirm'}
            onValueChange={(value) => setValue('booking_settings.confirmation_mode', value as 'auto_confirm' | 'approval_required', { shouldDirty: true })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto_confirm">AWA confirms automatically</SelectItem>
              <SelectItem value="approval_required">Ask my team to approve first</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">Approval mode is useful when staff must verify every appointment manually.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Minimum notice before booking</Label>
            <Input
              type="number"
              min="0"
              step="15"
              placeholder="120"
              {...register('booking_settings.minimum_notice_minutes', { valueAsNumber: true })}
            />
            <p className="text-xs text-muted-foreground">Minutes before the appointment. Example: 120 means two hours.</p>
          </div>
          <div className="space-y-2">
            <Label>How far ahead customers can book</Label>
            <Input
              type="number"
              min="1"
              step="1"
              placeholder="30"
              {...register('booking_settings.advance_booking_days', { valueAsNumber: true })}
            />
            <p className="text-xs text-muted-foreground">Days into the future AWA can offer slots.</p>
          </div>
          <div className="space-y-2">
            <Label>Slot interval</Label>
            <Input
              type="number"
              min="5"
              step="5"
              placeholder="30"
              {...register('booking_settings.slot_interval_minutes', { valueAsNumber: true })}
            />
            <p className="text-xs text-muted-foreground">Controls suggested times such as 10:00, 10:30, 11:00.</p>
          </div>
        </div>
        <div className="divide-y rounded-md border">
          <div className="flex items-center justify-between gap-4 p-4">
            <div>
              <Label>Let customers reschedule through WhatsApp</Label>
              <p className="text-xs text-muted-foreground">AWA can help customers move an existing appointment when allowed.</p>
            </div>
            <Switch
              checked={settings.allow_customer_reschedule ?? true}
              onCheckedChange={(checked) => setValue('booking_settings.allow_customer_reschedule', checked, { shouldDirty: true })}
            />
          </div>
          <div className="flex items-center justify-between gap-4 p-4">
            <div>
              <Label>Let customers cancel through WhatsApp</Label>
              <p className="text-xs text-muted-foreground">AWA can cancel a booking and notify your team when allowed.</p>
            </div>
            <Switch
              checked={settings.allow_customer_cancel ?? true}
              onCheckedChange={(checked) => setValue('booking_settings.allow_customer_cancel', checked, { shouldDirty: true })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}