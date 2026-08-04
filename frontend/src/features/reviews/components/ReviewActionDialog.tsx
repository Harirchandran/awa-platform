import type { PendingReviewAction } from '../types';
import { REVIEW_ACTION_COPY } from '../constants';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { PublicReviewLinkField } from './PublicReviewLinkField';

interface ReviewActionDialogProps {
  pendingAction: PendingReviewAction | null;
  onOpenChange: (open: boolean) => void;
  actionNote: string;
  onActionNoteChange: (value: string) => void;
  actionRating: string;
  onActionRatingChange: (value: string) => void;
  actionFeedback: string;
  onActionFeedbackChange: (value: string) => void;
  actionPublicUrl: string;
  onActionPublicUrlChange: (value: string) => void;
  isActionUrlInvalid: boolean;
  isPending: boolean;
  onConfirm: () => void;
}

export function ReviewActionDialog({
  pendingAction,
  onOpenChange,
  actionNote,
  onActionNoteChange,
  actionRating,
  onActionRatingChange,
  actionFeedback,
  onActionFeedbackChange,
  actionPublicUrl,
  onActionPublicUrlChange,
  isActionUrlInvalid,
  isPending,
  onConfirm,
}: ReviewActionDialogProps) {
  const copy = pendingAction ? REVIEW_ACTION_COPY[pendingAction.action] : null;
  const needsPublicUrl = ['submit_rating', 'request_public_review'].includes(pendingAction?.action || '');

  return (
    <ConfirmActionDialog
      open={!!pendingAction}
      onOpenChange={onOpenChange}
      title={copy?.title || 'Review action'}
      description={copy?.description || 'This updates the review request and records an event.'}
      confirmLabel={copy?.confirmLabel || 'Confirm'}
      variant={pendingAction?.tone === 'destructive' || copy?.isHighImpact ? 'warning' : 'default'}
      isLoading={isPending}
      confirmDisabled={needsPublicUrl && isActionUrlInvalid}
      onConfirm={onConfirm}
    >
        <div className="space-y-3">
          {pendingAction?.action === 'submit_rating' && (
            <>
              <div className="space-y-2">
                <Label htmlFor="review-action-rating">Rating</Label>
                <Input id="review-action-rating" type="number" min="1" max="5" value={actionRating} onChange={(event) => onActionRatingChange(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="review-action-feedback">Private feedback</Label>
                <Textarea id="review-action-feedback" rows={3} value={actionFeedback} onChange={(event) => onActionFeedbackChange(event.target.value)} />
              </div>
            </>
          )}
          {needsPublicUrl && (
            <PublicReviewLinkField
              id="review-action-url"
              value={actionPublicUrl}
              onChange={onActionPublicUrlChange}
              invalid={isActionUrlInvalid}
              helper="Leave blank or enter the public review link used for this request."
            />
          )}
          <div className="space-y-2">
            <Label htmlFor="review-action-note">Internal note</Label>
            <Textarea id="review-action-note" rows={3} value={actionNote} onChange={(event) => onActionNoteChange(event.target.value)} />
          </div>
        </div>
    </ConfirmActionDialog>
  );
}
