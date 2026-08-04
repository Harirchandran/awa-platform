import { AlertTriangle } from 'lucide-react';
import type { ReminderRule } from '../api';
import { ReminderRuleCard } from './ReminderRuleCard';

interface ReminderRulesListProps {
  rules: ReminderRule[];
  isLoading: boolean;
  errorMessage: string | null;
}

/**
 * Renders the list of reminder rules with loading, error, and empty states.
 * Each card manages its own mutations internally.
 */
export function ReminderRulesList({ rules, isLoading, errorMessage }: ReminderRulesListProps) {
  if (isLoading) {
    return (
      <div className="space-y-2 p-3" aria-live="polite" aria-label="Loading rules">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-[72px] animate-pulse rounded-md border bg-muted/40"
            aria-hidden="true"
          />
        ))}
      </div>
    );
  }

  if (errorMessage) {
    return (
      <div role="alert" className="m-3 rounded-md border border-destructive/40 bg-destructive/5 p-4 text-[13px] text-destructive">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load follow-up rules</p>
            <p className="mt-1 leading-relaxed">{errorMessage}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!rules.length) {
    return (
      <p className="m-3 rounded-md border border-dashed p-5 text-center text-[13px] leading-5 text-muted-foreground">
        No follow-up rules yet. Create your first rule to automatically remind customers about bookings, payments, or reviews.
      </p>
    );
  }

  return (
    <div className="space-y-2 p-3">
      {rules.map((rule) => (
        <ReminderRuleCard key={rule.id} rule={rule} />
      ))}
    </div>
  );
}
