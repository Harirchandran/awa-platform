import type { ReviewAction, ReviewSource, ReviewStatus } from './api';

export const REVIEW_STATUS_COPY: Record<ReviewStatus, string> = {
  draft: 'Draft',
  scheduled: 'Scheduled',
  sent: 'Sent',
  responded: 'Responded',
  public_review_requested: 'Public review requested',
  negative_feedback_escalated: 'Needs follow-up',
  skipped: 'Skipped',
  expired: 'Expired',
};

export const REVIEW_SOURCE_COPY: Record<ReviewSource, string> = {
  manual: 'Manual',
  booking: 'Booking',
  order: 'Order',
  reminder: 'Reminder',
  automation: 'Automation',
};

export const REVIEW_ACTION_COPY: Record<ReviewAction, {
  title: string;
  confirmLabel: string;
  description: string;
  isHighImpact?: boolean;
}> = {
  send: {
    title: 'Send review request',
    confirmLabel: 'Mark Sent',
    description: 'This marks the review request as sent and records an event for this request.',
  },
  submit_rating: {
    title: 'Submit private rating',
    confirmLabel: 'Submit Rating',
    description: 'This records the customer rating and optional private feedback on this review request.',
  },
  request_public_review: {
    title: 'Request public review',
    confirmLabel: 'Request Public Review',
    description: 'This records or shares the public review link for this request. It does not post on the customer\'s behalf.',
  },
  escalate_negative: {
    title: 'Flag low rating for reply',
    confirmLabel: 'Flag Negative Feedback',
    description: 'This marks the review request for private team follow-up. It does not create a public review.',
    isHighImpact: true,
  },
  skip: {
    title: 'Skip review request',
    confirmLabel: 'Skip Request',
    description: 'This stops this review request from continuing and records the skip event.',
    isHighImpact: true,
  },
  expire: {
    title: 'Expire review request',
    confirmLabel: 'Expire Request',
    description: 'This expires the review request and records the event.',
    isHighImpact: true,
  },
  reopen: {
    title: 'Reopen review request',
    confirmLabel: 'Reopen Request',
    description: 'This reopens the review request so it can continue through the review workflow.',
  },
};
