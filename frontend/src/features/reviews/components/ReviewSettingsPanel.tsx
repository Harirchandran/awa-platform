import { Loader2, Settings } from 'lucide-react';
import type { ReviewSettingsForm } from '../types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { PublicReviewLinkField } from './PublicReviewLinkField';
import { ReviewErrorState } from './ReviewErrorState';

interface ReviewSettingsPanelProps {
  form: ReviewSettingsForm;
  onChange: (patch: Partial<ReviewSettingsForm>) => void;
  isLoading: boolean;
  isError?: boolean;
  onRetry?: () => void;
  isDirty: boolean;
  isSaving: boolean;
  isUrlInvalid: boolean;
  onSave: () => void;
}

export function ReviewSettingsPanel({
  form,
  onChange,
  isLoading,
  isError = false,
  onRetry,
  isDirty,
  isSaving,
  isUrlInvalid,
  onSave,
}: ReviewSettingsPanelProps) {
  return (
    <div className="rounded-md border bg-card p-4">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border bg-background">
          <Settings className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <div>
          <h2 className="text-[15px] font-semibold">Review settings</h2>
          <p className="mt-1 text-[12px] leading-normal text-muted-foreground">
            Configure when AWA asks for private ratings and where happy customers can leave a public review.
          </p>
        </div>
      </div>

      {isLoading ? (
        <p className="text-[13px] text-muted-foreground">Loading settings...</p>
      ) : isError ? (
        <ReviewErrorState title="Could not load review settings" message="Review automation settings are unavailable right now." onRetry={onRetry} />
      ) : (
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
            <div>
              <Label htmlFor="reviews-active">Review automation active</Label>
              <p className="mt-1 text-xs text-muted-foreground">AWA can create and process review requests when enabled.</p>
            </div>
            <Switch id="reviews-active" checked={form.is_active} onCheckedChange={(checked) => onChange({ is_active: checked })} />
          </div>

          <PublicReviewLinkField
            id="reviews-url"
            value={form.public_review_url}
            onChange={(value) => onChange({ public_review_url: value })}
            invalid={isUrlInvalid}
          />

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="reviews-delay">Delay minutes</Label>
              <Input id="reviews-delay" type="number" min="0" value={form.ask_delay_minutes} onChange={(event) => onChange({ ask_delay_minutes: event.target.value })} />
              <p className="text-xs text-muted-foreground">Minutes after completion before asking.</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="reviews-threshold">Positive at</Label>
              <Input id="reviews-threshold" type="number" min="1" max="5" value={form.positive_rating_threshold} onChange={(event) => onChange({ positive_rating_threshold: event.target.value })} />
              <p className="text-xs text-muted-foreground">Rating that can lead to public link.</p>
            </div>
          </div>

          <div className="divide-y rounded-md border">
            <div className="flex items-center justify-between gap-3 p-3">
              <Label htmlFor="reviews-bookings">Ask after bookings</Label>
              <Switch id="reviews-bookings" checked={form.ask_after_booking_completed} onCheckedChange={(checked) => onChange({ ask_after_booking_completed: checked })} />
            </div>
            <div className="flex items-center justify-between gap-3 p-3">
              <Label htmlFor="reviews-orders">Ask after orders</Label>
              <Switch id="reviews-orders" checked={form.ask_after_order_completed} onCheckedChange={(checked) => onChange({ ask_after_order_completed: checked })} />
            </div>
            <div className="flex items-center justify-between gap-3 p-3">
              <div>
                <Label htmlFor="reviews-negative">Escalate negatives</Label>
                <p className="mt-1 text-xs text-muted-foreground">Low ratings can be routed for private team follow-up.</p>
              </div>
              <Switch id="reviews-negative" checked={form.negative_feedback_escalation_enabled} onCheckedChange={(checked) => onChange({ negative_feedback_escalation_enabled: checked })} />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="reviews-request-template">Rating ask</Label>
            <Textarea id="reviews-request-template" rows={3} value={form.request_template} onChange={(event) => onChange({ request_template: event.target.value })} />
            <p className="text-xs text-muted-foreground">Message AWA uses to ask for private rating feedback.</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="reviews-positive-template">Positive response template</Label>
            <Textarea id="reviews-positive-template" rows={3} value={form.positive_template} onChange={(event) => onChange({ positive_template: event.target.value })} />
            <p className="text-xs text-muted-foreground">Message shown to satisfied customers with the public review link.</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="reviews-negative-template">Negative response template</Label>
            <Textarea id="reviews-negative-template" rows={3} value={form.negative_template} onChange={(event) => onChange({ negative_template: event.target.value })} />
            <p className="text-xs text-muted-foreground">Message shown to customers who leave a negative rating.</p>
          </div>

          <Button
            type="button"
            className="w-full"
            onClick={onSave}
            disabled={isSaving || !isDirty || isUrlInvalid}
            title={isDirty ? 'Save review setting changes' : 'No review setting changes to save'}
          >
            {isSaving && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {isSaving ? 'Saving Settings' : isDirty ? 'Save Settings' : 'Settings Saved'}
          </Button>
        </div>
      )}
    </div>
  );
}
