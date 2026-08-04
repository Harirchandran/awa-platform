import { useEffect } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { Loader2, Star, Clock, Bell, Link2, MessageSquare } from 'lucide-react';
import toast from 'react-hot-toast';
import { useReviewSettings, useUpdateReviewSettings, type UpdateReviewSettingsPayload } from '@/features/reviews/api';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';

// ReviewSettingsEditor is intentionally separate from the tenant-config form.
// It calls PUT /api/v1/reviews/settings, a distinct endpoint and table from
// tenant_configs. It manages its own useForm instance and save lifecycle.

interface ReviewSettingsFormValues {
  is_active: boolean;
  public_review_url: string;
  ask_after_booking_completed: boolean;
  ask_after_order_completed: boolean;
  ask_delay_minutes: number;
  positive_rating_threshold: number;
  negative_feedback_escalation_enabled: boolean;
  request_template: string;
  positive_template: string;
  negative_template: string;
}

export function ReviewSettingsEditor() {
  const { data: settings, isLoading, isError } = useReviewSettings();
  const updateSettings = useUpdateReviewSettings();

  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { isDirty, isSubmitting },
  } = useForm<ReviewSettingsFormValues>({
    defaultValues: {
      is_active: false,
      public_review_url: '',
      ask_after_booking_completed: true,
      ask_after_order_completed: false,
      ask_delay_minutes: 60,
      positive_rating_threshold: 4,
      negative_feedback_escalation_enabled: false,
      request_template: 'Hi {{customer_name}}, thank you for choosing us! How would you rate your experience today? Reply 1-5.',
      positive_template: 'We\'re thrilled you had a great experience! Would you mind sharing it here? {{review_url}}',
      negative_template: 'We\'re sorry to hear that. Our team will reach out to make it right.',
    },
  });

  const isActive = useWatch({ control, name: 'is_active' });
  const askAfterBooking = useWatch({ control, name: 'ask_after_booking_completed' });
  const askAfterOrder = useWatch({ control, name: 'ask_after_order_completed' });
  const escalationEnabled = useWatch({ control, name: 'negative_feedback_escalation_enabled' });

  // Populate form once settings are loaded
  useEffect(() => {
    if (settings) {
      reset({
        is_active: settings.is_active,
        public_review_url: settings.public_review_url ?? '',
        ask_after_booking_completed: settings.ask_after_booking_completed,
        ask_after_order_completed: settings.ask_after_order_completed,
        ask_delay_minutes: settings.ask_delay_minutes,
        positive_rating_threshold: settings.positive_rating_threshold,
        negative_feedback_escalation_enabled: settings.negative_feedback_escalation_enabled,
        request_template: settings.request_template,
        positive_template: settings.positive_template,
        negative_template: settings.negative_template,
      });
    }
  }, [settings, reset]);

  const onSubmit = (data: ReviewSettingsFormValues) => {
    const payload: UpdateReviewSettingsPayload = {
      ...data,
      public_review_url: data.public_review_url.trim() || null,
      ask_delay_minutes: Number(data.ask_delay_minutes),
      positive_rating_threshold: Number(data.positive_rating_threshold),
    };
    updateSettings.mutate(payload, {
      onSuccess: () => {
        toast.success('Review settings saved successfully!');
        reset(data); // Clear dirty state
      },
      onError: () => {
        toast.error('Failed to save review settings. Please try again.');
      },
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading review settings…
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rounded-md border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
        Failed to load review settings. Please refresh the page and try again.
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-8">
      {/* Header */}
      <div>
        <h3 className="text-lg font-semibold">Review Settings</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Configure how and when AWA automatically requests customer reviews after completed bookings or orders.
        </p>
      </div>

      {/* Enable / disable */}
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="space-y-0.5">
          <Label className="text-base font-medium">Enable Review Automation</Label>
          <p className="text-sm text-muted-foreground">
            When enabled, AWA will send review request messages based on the triggers below.
          </p>
        </div>
        <Switch
          id="review-is-active"
          checked={isActive}
          onCheckedChange={(checked) => setValue('is_active', checked, { shouldDirty: true })}
        />
      </div>

      {/* Public Review URL */}
      <div className="space-y-2">
        <Label htmlFor="review-public-url" className="flex items-center gap-1.5">
          <Link2 className="h-3.5 w-3.5 text-muted-foreground" />
          Public Review URL
        </Label>
        <Input
          id="review-public-url"
          type="url"
          placeholder="https://g.page/your-business/review"
          {...register('public_review_url')}
        />
        <p className="text-xs text-muted-foreground">
          Google, Yelp, or any public review link shown to satisfied customers in the positive template.
        </p>
      </div>

      {/* Trigger rules */}
      <div className="space-y-4">
        <h4 className="flex items-center gap-1.5 text-sm font-semibold">
          <Bell className="h-4 w-4 text-muted-foreground" />
          Automation Triggers
        </h4>

        <div className="rounded-lg border divide-y">
          {/* Ask after booking */}
          <div className="flex items-center justify-between px-4 py-3">
            <div>
              <p className="text-sm font-medium">Ask after booking completed</p>
              <p className="text-xs text-muted-foreground">Sends a review request when a booking is marked completed.</p>
            </div>
            <Switch
              id="review-ask-booking"
              checked={askAfterBooking}
              onCheckedChange={(checked) => setValue('ask_after_booking_completed', checked, { shouldDirty: true })}
            />
          </div>

          {/* Ask after order */}
          <div className="flex items-center justify-between px-4 py-3">
            <div>
              <p className="text-sm font-medium">Ask after order completed</p>
              <p className="text-xs text-muted-foreground">Sends a review request when an order is fulfilled.</p>
            </div>
            <Switch
              id="review-ask-order"
              checked={askAfterOrder}
              onCheckedChange={(checked) => setValue('ask_after_order_completed', checked, { shouldDirty: true })}
            />
          </div>
        </div>
      </div>

      {/* Delay + threshold */}
      <div className="grid gap-6 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="review-delay" className="flex items-center gap-1.5">
            <Clock className="h-3.5 w-3.5 text-muted-foreground" />
            Delay after trigger (minutes)
          </Label>
          <Input
            id="review-delay"
            type="number"
            min={0}
            max={10080}
            {...register('ask_delay_minutes', { valueAsNumber: true })}
          />
          <p className="text-xs text-muted-foreground">
            How long after the trigger event to send the request. 60 = 1 hour.
          </p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="review-threshold" className="flex items-center gap-1.5">
            <Star className="h-3.5 w-3.5 text-muted-foreground" />
            Positive rating threshold
          </Label>
          <Input
            id="review-threshold"
            type="number"
            min={1}
            max={5}
            step={1}
            {...register('positive_rating_threshold', { valueAsNumber: true })}
          />
          <p className="text-xs text-muted-foreground">
            Ratings at or above this score receive the public review link (1–5).
          </p>
        </div>
      </div>

      {/* Negative escalation */}
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="space-y-0.5">
          <Label className="text-base font-medium">Escalate negative feedback</Label>
          <p className="text-sm text-muted-foreground">
            Automatically escalate low-rating responses to the human support queue.
          </p>
        </div>
        <Switch
          id="review-escalation"
          checked={escalationEnabled}
          onCheckedChange={(checked) =>
            setValue('negative_feedback_escalation_enabled', checked, { shouldDirty: true })
          }
        />
      </div>

      {/* Message templates */}
      <div className="space-y-4">
        <h4 className="flex items-center gap-1.5 text-sm font-semibold">
          <MessageSquare className="h-4 w-4 text-muted-foreground" />
          Message Templates
        </h4>
        <p className="text-xs text-muted-foreground">
          Use{' '}
          <code className="rounded bg-muted px-1 py-0.5">{'{{customer_name}}'}</code> and{' '}
          <code className="rounded bg-muted px-1 py-0.5">{'{{review_url}}'}</code> as placeholders.
        </p>

        <div className="space-y-2">
          <Label htmlFor="review-request-tmpl">Review request message</Label>
          <Textarea
            id="review-request-tmpl"
            rows={3}
            placeholder="Hi {{customer_name}}, how would you rate your experience? Reply 1-5."
            {...register('request_template')}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="review-positive-tmpl">Positive response message</Label>
          <Textarea
            id="review-positive-tmpl"
            rows={3}
            placeholder="We're so glad you loved it! Share your experience: {{review_url}}"
            {...register('positive_template')}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="review-negative-tmpl">Negative response message</Label>
          <Textarea
            id="review-negative-tmpl"
            rows={3}
            placeholder="We're sorry to hear that. Our team will reach out to make it right."
            {...register('negative_template')}
          />
        </div>
      </div>

      {/* Save bar */}
      {isDirty && (
        <div className="sticky bottom-0 z-10 -mx-4 -mb-4 flex items-center justify-between border-t bg-background/95 px-6 py-3 backdrop-blur sm:-mx-6">
          <p className="text-sm text-muted-foreground">You have unsaved review setting changes.</p>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => reset()}
              disabled={updateSettings.isPending || isSubmitting}
            >
              Discard
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={updateSettings.isPending || isSubmitting}
            >
              {updateSettings.isPending && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
              Save Review Settings
            </Button>
          </div>
        </div>
      )}
    </form>
  );
}
