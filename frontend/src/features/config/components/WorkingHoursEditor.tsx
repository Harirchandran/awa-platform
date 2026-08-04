import { useFormContext, useWatch } from 'react-hook-form';
import type { TenantConfig } from '@/types/config.types';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const;
export function WorkingHoursEditor() {
  const { register, control, setValue } = useFormContext<TenantConfig>();
  const workingHours = useWatch({ control, name: 'working_hours' }) || {};
  return (
    <div className="w-full max-w-4xl space-y-6">
      <div>
        <h3 className="text-lg font-medium">Business Schedule</h3>
        <p className="text-sm text-muted-foreground">
          AWA uses this schedule when answering customers, checking booking times, and explaining when the team is available.
        </p>
      </div>
      <div className="space-y-4 border rounded-lg p-4 bg-card">
        {DAYS.map((day) => {
          const isOpenPath = `working_hours.${day}.is_open` as const;
          const openTimePath = `working_hours.${day}.open_time` as const;
          const closeTimePath = `working_hours.${day}.close_time` as const;
          const isOpen = workingHours[day]?.is_open;
          return (
            <div key={day} className="flex flex-col gap-3 p-3 rounded border border-transparent transition-colors hover:border-border hover:bg-muted/50 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-center gap-3 sm:w-40">
                <Switch
                  checked={!!isOpen}
                  onCheckedChange={(checked) => setValue(isOpenPath, checked, { shouldDirty: true })}
                />
                <Label className="min-w-0 cursor-pointer truncate capitalize font-medium">{day}</Label>
              </div>
              <div className="grid flex-1 grid-cols-[1fr_auto_1fr] items-center gap-2 sm:flex sm:justify-end">
                {isOpen ? (
                  <>
                    <Input
                      type="time"
                      className="min-w-0 bg-background sm:w-32"
                      {...register(openTimePath)}
                    />
                    <span className="text-muted-foreground text-sm font-medium">to</span>
                    <Input
                      type="time"
                      className="min-w-0 bg-background sm:w-32"
                      {...register(closeTimePath)}
                    />
                  </>
                ) : (
                  <span className="col-span-3 text-sm italic text-muted-foreground sm:w-32 sm:text-right">Closed</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}