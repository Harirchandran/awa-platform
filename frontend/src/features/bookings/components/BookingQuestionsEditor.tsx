import { useState } from 'react';
import { useFormContext } from 'react-hook-form';
import type { ModuleField, TenantConfig } from '@/types/config.types';
import { Button } from '@/components/ui/button';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Plus, Trash2 } from 'lucide-react';
const FIELD_TYPES: ModuleField['type'][] = ['string', 'number', 'date', 'time', 'email', 'phone', 'select'];

export function BookingQuestionsEditor() {
  const { watch, setValue } = useFormContext<TenantConfig>();
  const [pendingRemoveIndex, setPendingRemoveIndex] = useState<number | null>(null);
  const fields = watch('modules.booking.fields') || [];
  const updateField = (index: number, patch: Partial<ModuleField>) => {
    const nextFields = fields.map((field, fieldIndex) => (
      fieldIndex === index ? { ...field, ...patch } : field
    ));
    setValue('modules.booking.fields', nextFields, { shouldDirty: true });
  };
  const addField = () => {
    setValue('modules.booking.fields', [
      ...fields,
      { key: '', label: '', type: 'string', required: true },
    ], { shouldDirty: true });
  };
  const removeField = (index: number) => {
    setValue('modules.booking.fields', fields.filter((_, fieldIndex) => fieldIndex !== index), { shouldDirty: true });
  };
  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-medium">Questions AWA Should Ask</h3>
          <p className="text-sm text-muted-foreground">
            These are the details AWA collects on WhatsApp before creating a booking.
          </p>
        </div>
        <Button type="button" onClick={addField}>
          <Plus className="h-4 w-4" />
          Add Question
        </Button>
      </div>
      {fields.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-card/50 p-8 text-center">
          <p className="text-sm text-muted-foreground">No booking questions yet.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Add questions like customer name, preferred date, address, or special instructions.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {fields.map((field, index) => (
            <div key={`${field.key}-${index}`} className="rounded-lg border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">Question {index + 1}</p>
                  <p className="text-xs text-muted-foreground">Shown as a natural WhatsApp question by AWA.</p>
                </div>
                <Button type="button" variant="ghost" size="icon" onClick={() => setPendingRemoveIndex(index)} aria-label="Remove question">
                  <Trash2 className="h-4 w-4 text-muted-foreground" />
                </Button>
              </div>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Question Label</Label>
                  <Input
                    value={field.label || ''}
                    placeholder="e.g. Patient name"
                    onChange={(event) => {
                      const label = event.target.value;
                      updateField(index, {
                        label,
                        key: label.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, ''),
                      });
                    }}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Answer Type</Label>
                  <Select value={field.type} onValueChange={(value) => updateField(index, { type: value as ModuleField['type'] })}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {FIELD_TYPES.map((type) => (
                        <SelectItem key={type} value={type}>
                          {type === 'string' ? 'Text' : type.charAt(0).toUpperCase() + type.slice(1)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center justify-between rounded-md border px-3 py-2">
                  <div>
                    <Label>Required before booking</Label>
                    <p className="text-xs text-muted-foreground">AWA will not create the booking until this is answered.</p>
                  </div>
                  <Switch checked={field.required} onCheckedChange={(checked) => updateField(index, { required: checked })} />
                </div>
              </div>
              {field.type === 'select' && (
                <div className="mt-4 space-y-2">
                  <Label>Options</Label>
                  <Input
                    value={(field.options || []).join(', ')}
                    placeholder="e.g. New customer, Existing customer"
                    onChange={(event) => updateField(index, {
                      options: event.target.value.split(',').map((item) => item.trim()).filter(Boolean),
                    })}
                  />
                  <p className="text-xs text-muted-foreground">Separate options with commas.</p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <ConfirmActionDialog
        open={pendingRemoveIndex !== null}
        onOpenChange={(open) => !open && setPendingRemoveIndex(null)}
        title="Remove booking question?"
        description="This removes the question from the setup form. Save Booking Setup afterwards to stop AWA from asking it before bookings."
        confirmLabel="Remove Question"
        variant="destructive"
        onConfirm={() => {
          if (pendingRemoveIndex !== null) {
            removeField(pendingRemoveIndex);
          }
          setPendingRemoveIndex(null);
        }}
      />
    </div>
  );
}
