import { format } from 'date-fns';
import { formatLocalDateOnly, parseDateOnlyAsLocal } from '@/lib/date-only';
import type { Booking } from './api';
import { BOOKING_STATUS_LABELS } from './constants';

export function parseBookingDate(booking: Booking): Date | null {
  const rawDate = booking.booking_date || booking.booking_datetime;
  if (!rawDate) return null;
  return parseDateOnlyAsLocal(rawDate);
}

export function formatBookingDate(booking: Booking, pattern: string): string {
  const parsed = parseBookingDate(booking);
  return parsed ? format(parsed, pattern) : 'Date not set';
}

export function formatBookingTime(booking: Booking, pattern = 'h:mm a'): string {
  if (!booking.booking_time) {
    return 'Time not set';
  }
  const parsed = new Date(`1970-01-01T${booking.booking_time}`);
  return Number.isNaN(parsed.getTime()) ? 'Time not set' : format(parsed, pattern);
}

export function formatBookingStatus(status: string): string {
  return BOOKING_STATUS_LABELS[status] ?? status.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

export function getBookingMetrics(bookings: Booking[]) {
  const todayKey = formatLocalDateOnly(new Date());
  return {
    loaded: bookings.length,
    confirmed: bookings.filter((booking) => booking.status === 'confirmed').length,
    cancelled: bookings.filter((booking) => booking.status === 'cancelled').length,
    today: bookings.filter((booking) => (booking.booking_date || booking.booking_datetime.slice(0, 10)) === todayKey).length,
  };
}

export function formatCollectedValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return 'Not provided';
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return 'Saved value';
  }
}
