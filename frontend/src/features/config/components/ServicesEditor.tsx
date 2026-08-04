import { useState } from 'react';
import { useFormContext, useFieldArray, useWatch } from 'react-hook-form';
import type { TenantConfig } from '@/types/config.types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { Trash2, Plus } from 'lucide-react';
import { v4 as uuidv4 } from 'uuid';
export function ServicesEditor() {
  const { register, control, setValue } = useFormContext<TenantConfig>();
  const [pendingRemoveIndex, setPendingRemoveIndex] = useState<number | null>(null);
  const services = useWatch({ control, name: 'services' }) || [];
  const { fields, append, remove } = useFieldArray({
    control,
    name: "services",
    keyName: "fieldId"
  });
  return (
    <div className="w-full max-w-4xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-medium">Services Customers Can Book</h3>
          <p className="text-sm text-muted-foreground">Add the appointments or services AWA can offer through AWA Chat or WhatsApp.</p>
        </div>
        <Button
          type="button"
          variant="default"
          onClick={() => append({ id: uuidv4(), name: '', duration_mins: 30, required_fields: [] })}
        >
          <Plus className="w-4 h-4 mr-2" /> Add Service
        </Button>
      </div>
      {fields.length === 0 ? (
        <div className="p-8 border border-dashed rounded-lg text-center bg-card/50">
           <p className="text-sm text-muted-foreground">You currently have no services defined.</p>
           <p className="text-xs text-muted-foreground mt-1">Add at least one service before customers can book through AWA Chat or WhatsApp.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {fields.map((field, index) => (
            <div key={field.fieldId} className="p-4 border rounded-lg bg-card relative group">
              <input type="hidden" {...register(`services.${index}.id` as const)} />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute top-2 right-2 text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={() => setPendingRemoveIndex(index)}
                aria-label={`Remove ${field.name || 'service'}`}
              >
                <Trash2 className="w-4 h-4" />
              </Button>
              <div className="grid grid-cols-1 gap-4 pr-10 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor={`service-name-${index}`}>Service Name</Label>
                  <Input
                    id={`service-name-${index}`}
                    {...register(`services.${index}.name`)}
                    placeholder="e.g. Initial Consultation"
                    aria-invalid={Boolean(getServiceNameError(services[index]?.name))}
                    aria-describedby={getServiceNameError(services[index]?.name) ? `service-name-${index}-error` : undefined}
                  />
                  {getServiceNameError(services[index]?.name) && (
                    <p id={`service-name-${index}-error`} className="text-xs text-destructive">
                      {getServiceNameError(services[index]?.name)}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`service-duration-${index}`}>Duration (Minutes)</Label>
                  <Input
                    id={`service-duration-${index}`}
                    type="number"
                    min="5"
                    step="5"
                    {...register(`services.${index}.duration_mins`, {
                      valueAsNumber: true,
                    })}
                    aria-invalid={Boolean(getServiceDurationError(services[index]?.duration_mins))}
                    aria-describedby={getServiceDurationError(services[index]?.duration_mins) ? `service-duration-${index}-error` : undefined}
                  />
                  {getServiceDurationError(services[index]?.duration_mins) && (
                    <p id={`service-duration-${index}-error`} className="text-xs text-destructive">
                      {getServiceDurationError(services[index]?.duration_mins)}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`service-price-${index}`}>Price</Label>
                  <Input
                    id={`service-price-${index}`}
                    type="number"
                    min="0"
                    step="0.01"
                    {...register(`services.${index}.price`, {
                      valueAsNumber: true,
                    })}
                    aria-invalid={Boolean(getServicePriceError(services[index]?.price))}
                    aria-describedby={getServicePriceError(services[index]?.price) ? `service-price-${index}-error` : undefined}
                  />
                  {getServicePriceError(services[index]?.price) && (
                    <p id={`service-price-${index}-error`} className="text-xs text-destructive">
                      {getServicePriceError(services[index]?.price)}
                    </p>
                  )}
                </div>
              </div>
              <div className="mt-4 space-y-2">
                <Label>Questions AWA Should Ask For This Service</Label>
                <Input
                  placeholder="e.g. address, pain level, car model"
                  value={(services[index]?.required_fields || []).join(', ')}
                  onChange={(event) => {
                    const values = event.target.value
                      .split(',')
                      .map((item) => item.trim())
                      .filter(Boolean);
                    setValue(`services.${index}.required_fields` as const, values, { shouldDirty: true });
                  }}
                />
                <p className="text-xs text-muted-foreground">AWA asks these questions before finalizing this specific booking.</p>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConfirmActionDialog
        open={pendingRemoveIndex !== null}
        onOpenChange={(open) => !open && setPendingRemoveIndex(null)}
        title="Remove service?"
        description="This removes the service from the setup form. Save Business Setup afterwards to apply the change to customer booking options."
        confirmLabel="Remove Service"
        variant="destructive"
        onConfirm={() => {
          if (pendingRemoveIndex !== null) {
            remove(pendingRemoveIndex);
          }
          setPendingRemoveIndex(null);
        }}
      />
    </div>
  );
}
function getServiceNameError(value?: string) {
  if (!value?.trim()) return 'Service name is required.';
  if (value.trim().length > 120) return 'Service name must be 120 characters or fewer.';
  return null;
}
function getServiceDurationError(value?: number) {
  if (!Number.isFinite(value)) return 'Duration is required.';
  if (Number(value) < 5) return 'Duration must be at least 5 minutes.';
  if (Number(value) > 1440) return 'Duration must be 1440 minutes or less.';
  return null;
}
function getServicePriceError(value?: number) {
  if (value === undefined || value === null) return null;
  if (!Number.isFinite(value)) return 'Price must be a valid number.';
  if (Number(value) < 0) return 'Price cannot be negative.';
  return null;
}
