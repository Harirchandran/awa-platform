import type { ReviewAction, ReviewRequest, ReviewSource } from './api';

export type CreateReviewForm = {
  customer_id: string;
  conversation_id: string;
  booking_id: string;
  order_id: string;
  source: ReviewSource;
  public_review_url: string;
  note: string;
};

export type ReviewSettingsForm = {
  is_active: boolean;
  public_review_url: string;
  ask_after_booking_completed: boolean;
  ask_after_order_completed: boolean;
  ask_delay_minutes: string;
  positive_rating_threshold: string;
  negative_feedback_escalation_enabled: boolean;
  request_template: string;
  positive_template: string;
  negative_template: string;
};

export type PendingReviewAction = {
  review: ReviewRequest;
  action: ReviewAction;
  label: string;
  tone?: 'default' | 'destructive';
};
