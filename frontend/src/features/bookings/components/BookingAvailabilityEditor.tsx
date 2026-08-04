import { WorkingHoursEditor } from '@/features/config/components/WorkingHoursEditor';
export function BookingAvailabilityEditor() {
  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-card p-4">
        <h3 className="text-lg font-medium">When Customers Can Book</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Start with weekly business hours. Later this area can add holidays, breaks, staff, and capacity rules.
        </p>
      </div>
      <WorkingHoursEditor />
    </div>
  );
}