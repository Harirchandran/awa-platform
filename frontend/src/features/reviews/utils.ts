import type { ReviewRequest, ReviewSettings } from './api';
import type { CreateReviewForm, ReviewSettingsForm } from './types';

export const emptyCreateReviewForm: CreateReviewForm = {
  customer_id: '',
  conversation_id: '',
  booking_id: '',
  order_id: '',
  source: 'manual',
  public_review_url: '',
  note: '',
};

export const emptyReviewSettingsForm: ReviewSettingsForm = {
  is_active: true,
  public_review_url: '',
  ask_after_booking_completed: true,
  ask_after_order_completed: true,
  ask_delay_minutes: '1440',
  positive_rating_threshold: '4',
  negative_feedback_escalation_enabled: true,
  request_template: 'How was your experience with us? Please reply with a rating from 1 to 5.',
  positive_template: 'Thank you. Would you mind leaving us a public review?',
  negative_template: 'I am sorry about that. I have shared this with the team so they can help.',
};

export function toOptionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

export function toNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function isValidHttpUrl(value: string): boolean {
  if (!value.trim()) return true;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

export function settingsToForm(settings: ReviewSettings): ReviewSettingsForm {
  return {
    is_active: settings.is_active,
    public_review_url: settings.public_review_url || '',
    ask_after_booking_completed: settings.ask_after_booking_completed,
    ask_after_order_completed: settings.ask_after_order_completed,
    ask_delay_minutes: String(settings.ask_delay_minutes),
    positive_rating_threshold: String(settings.positive_rating_threshold),
    negative_feedback_escalation_enabled: settings.negative_feedback_escalation_enabled,
    request_template: settings.request_template,
    positive_template: settings.positive_template,
    negative_template: settings.negative_template,
  };
}

export function settingsFormPayload(form: ReviewSettingsForm) {
  return {
    is_active: form.is_active,
    public_review_url: form.public_review_url.trim() || null,
    ask_after_booking_completed: form.ask_after_booking_completed,
    ask_after_order_completed: form.ask_after_order_completed,
    ask_delay_minutes: toNumber(form.ask_delay_minutes, 1440),
    positive_rating_threshold: toNumber(form.positive_rating_threshold, 4),
    negative_feedback_escalation_enabled: form.negative_feedback_escalation_enabled,
    request_template: form.request_template.trim(),
    positive_template: form.positive_template.trim(),
    negative_template: form.negative_template.trim(),
  };
}

export function settingsSignature(form: ReviewSettingsForm) {
  return JSON.stringify(settingsFormPayload(form));
}

export async function copyTextToClipboard(value: string) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Fall back for local/test browsers where the Clipboard API is present but blocked.
    }
  }
  const textArea = document.createElement('textarea');
  textArea.value = value;
  textArea.setAttribute('readonly', '');
  textArea.style.position = 'fixed';
  textArea.style.left = '-9999px';
  document.body.appendChild(textArea);
  textArea.select();
  const copied = document.execCommand('copy');
  document.body.removeChild(textArea);
  if (!copied) {
    throw new Error('Clipboard unavailable');
  }
}

export function formatReviewDateTime(value?: string | null): string {
  if (!value) return 'Not set';
  const normalized = typeof value === 'string' ? value.replace(' ', 'T') : value;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? 'Not set' : parsed.toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export function getReviewMetrics(reviews: ReviewRequest[]) {
  const rated = reviews.filter((review) => review.rating);
  return {
    loaded: reviews.length,
    pending: reviews.filter((review) => ['draft', 'scheduled', 'sent'].includes(review.status)).length,
    average: rated.length
      ? (rated.reduce((total, review) => total + (review.rating || 0), 0) / rated.length).toFixed(1)
      : '0.0',
    escalated: reviews.filter((review) => review.status === 'negative_feedback_escalated').length,
    lowRatings: reviews.filter((review) => review.rating && review.rating <= 2).length,
    positive: reviews.filter((review) => review.rating && review.rating >= 4).length,
  };
}
