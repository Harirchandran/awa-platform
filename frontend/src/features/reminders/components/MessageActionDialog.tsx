import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import type { ScheduledMessage, ScheduledMessageAction } from '../api';

/* ── types ─────────────────────────────────────────────────── */

export type PendingAction = {
  message: ScheduledMessage;
  action: ScheduledMessageAction;
  label: string;
  tone?: 'default' | 'destructive';
};

interface MessageActionDialogProps {
  pendingAction: PendingAction | null;
  actionNote: string;
  onNoteChange: (note: string) => void;
  snoozeMinutes: string;
  onSnoozeChange: (minutes: string) => void;
  onConfirm: () => void;
  onClose: () => void;
  isPending: boolean;
}

/**
 * Confirmation dialog for message status actions: cancel, snooze, mark_sent,
 * mark_failed, mark_due, skip, retry.
 * Receives all state from orchestrator (RemindersPage) — no mutations here.
 */
export function MessageActionDialog({
  pendingAction,
  actionNote,
  onNoteChange,
  snoozeMinutes,
  onSnoozeChange,
  onConfirm,
  onClose,
  isPending,
}: MessageActionDialogProps) {
  return (
    <Dialog open={!!pendingAction} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent
        aria-labelledby="action-dialog-title"
        aria-describedby="action-dialog-desc"
        className={cn(isPending && "[&>button:last-child]:pointer-events-none [&>button:last-child]:opacity-0")}
        onPointerDownOutside={(e) => {
          if (isPending) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (isPending) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle id="action-dialog-title">
            {pendingAction?.label ?? 'Confirm action'}
          </DialogTitle>
          <DialogDescription id="action-dialog-desc">
            This updates the scheduled message status and records an event in the timeline.
            {pendingAction?.action === 'mark_sent' &&
              ' Only mark as sent after confirming the message was actually delivered by the worker or channel.'}
            {pendingAction?.action === 'cancel' &&
              ' Cancelling will prevent this message from being sent. This cannot be undone.'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Snooze duration — only for snooze action */}
          {pendingAction?.action === 'snooze' && (
            <div className="space-y-1.5">
              <Label htmlFor="snooze-minutes">Snooze duration (minutes)</Label>
              <Input
                id="snooze-minutes"
                type="number"
                min="1"
                value={snoozeMinutes}
                onChange={(e) => onSnoozeChange(e.target.value)}
                aria-describedby="snooze-helper"
              />
              <p id="snooze-helper" className="text-[11px] text-muted-foreground">
                The message will be rescheduled to{' '}
                {Number(snoozeMinutes) >= 60
                  ? `${Math.floor(Number(snoozeMinutes) / 60)}h ${Number(snoozeMinutes) % 60}m`
                  : `${snoozeMinutes} minutes`}{' '}
                from now.
              </p>
            </div>
          )}

          {/* Note */}
          <div className="space-y-1.5">
            <Label htmlFor="action-note">Note (optional)</Label>
            <Textarea
              id="action-note"
              rows={3}
              placeholder="Add an internal note about this action…"
              value={actionNote}
              onChange={(e) => onNoteChange(e.target.value)}
              aria-describedby="action-note-helper"
            />
            <p id="action-note-helper" className="text-[11px] text-muted-foreground">
              This note is saved in the message timeline for staff reference.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant={pendingAction?.tone ?? 'default'}
            onClick={onConfirm}
            disabled={isPending}
          >
            {isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            Confirm
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
