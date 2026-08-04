export const BOOKING_STATUS_LABELS: Record<string, string> = {
  confirmed: 'Confirmed',
  cancelled: 'Cancelled',
  pending: 'Pending',
  awaiting_confirmation: 'Awaiting confirmation',
  completed: 'Completed',
  failed: 'Failed',
  rescheduled: 'Rescheduled',
  no_show: 'No show',
  draft: 'Draft',
  proposed: 'Proposed',
};

export const BOOKING_SECTIONS = [
  {
    value: 'operations',
    label: 'Bookings',
    description: 'Review appointment records and update booking status.',
  },
  {
    value: 'services',
    label: 'Services',
    description: 'Manage bookable services, duration, price, and required details.',
  },
  {
    value: 'availability',
    label: 'Availability',
    description: 'Set the weekly working hours AWA uses for booking conversations.',
  },
  {
    value: 'questions',
    label: 'Questions',
    description: 'Choose the details AWA asks before creating a booking.',
  },
  {
    value: 'rules',
    label: 'Rules',
    description: 'Control confirmation, notice, slot interval, and cancellation behavior.',
  },
  {
    value: 'advanced',
    label: 'Advanced',
    description: 'Fine-tune buffers, reminders, approval cases, and reset options.',
  },
] as const;

export type BookingSection = typeof BOOKING_SECTIONS[number]['value'];
