import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { cn } from '@/lib/utils';
import { getApiErrorMessage } from '@/lib/api-errors';
import {
  useUpdateReminderRule,
  useDeleteReminderRule,
  type ReminderRule,
} from '../api';
import { TRIGGER_COPY, CHANNEL_COPY } from '../constants';
import { RuleStatusBadge } from './ReminderStatusBadge';
import { ReminderTimingDisplay } from './ReminderTimingDisplay';

interface ReminderRuleCardProps {
  rule: ReminderRule;
}

/**
 * Displays a single reminder rule card with toggle enable/disable and delete with confirmation.
 * Manages its own update and delete mutations — no prop-drilling needed.
 */
export function ReminderRuleCard({ rule }: ReminderRuleCardProps) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  const updateRule = useUpdateReminderRule();
  const deleteRule = useDeleteReminderRule();

  const handleToggle = (checked: boolean) => {
    updateRule.mutate(
      { id: rule.id, updates: { is_active: checked } },
      { onError: (error) => toast.error(getApiErrorMessage(error, 'Could not update rule')) }
    );
  };

  const handleDelete = () => {
    deleteRule.mutate(rule.id, {
      onSuccess: () => {
        toast.success('Rule removed');
        setDeleteOpen(false);
      },
      onError: (error) => {
        toast.error(getApiErrorMessage(error, 'Could not remove rule'));
      },
    });
  };

  return (
    <>
      <div
        className={cn(
          'rounded-md border p-3 transition-colors',
          rule.is_active ? 'border-border bg-background' : 'border-muted bg-muted/40'
        )}
      >
        {/* Header row */}
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <p
                className={cn(
                  'truncate text-[13px] font-medium',
                  rule.is_active ? 'text-foreground' : 'text-muted-foreground'
                )}
              >
                {rule.name}
              </p>
              <RuleStatusBadge isActive={rule.is_active} />
            </div>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {TRIGGER_COPY[rule.trigger_type]}
              {' · '}
              {CHANNEL_COPY[rule.channel]}
              {' · '}
              <ReminderTimingDisplay offsetMinutes={rule.offset_minutes} />
            </p>
          </div>

          {/* Actions */}
          <div className="flex shrink-0 items-center gap-1">
            <Switch
              checked={rule.is_active}
              onCheckedChange={handleToggle}
              disabled={updateRule.isPending}
              aria-label={`${rule.is_active ? 'Disable' : 'Enable'} rule: ${rule.name}`}
            />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-8 w-8 text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => setDeleteOpen(true)}
              disabled={deleteRule.isPending}
              aria-label={`Delete rule: ${rule.name}`}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>

        {/* Template preview */}
        {rule.template_text && (
          <p className="mt-2 line-clamp-2 text-[12px] leading-relaxed text-muted-foreground">
            {rule.template_text}
          </p>
        )}
      </div>

      {/* Delete confirmation dialog */}
      <ConfirmActionDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={`Delete "${rule.name}"?`}
        description="This follow-up rule will be removed from active application configuration. Automation using this rule will stop scheduling new messages. Existing scheduled messages are not affected."
        confirmLabel="Delete Rule"
        variant="destructive"
        isLoading={deleteRule.isPending}
        onConfirm={handleDelete}
      />
    </>
  );
}
