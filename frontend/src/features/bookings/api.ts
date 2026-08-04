import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';
import { formatLocalDateOnly } from '@/lib/date-only';
export interface Booking {
  id: string;
  customer_id: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  service_type: string;
  service_name?: string;
  booking_date?: string;
  booking_time?: string | null;
  booking_datetime: string;
  status: 'confirmed' | 'cancelled';
  collected_fields: Record<string, unknown>;
  created_at: string;
}
interface RawBooking {
  id: string;
  customer_id: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  service_type?: string | null;
  service_name?: string | null;
  booking_date?: string | null;
  booking_time?: string | null;
  booking_datetime?: string | null;
  status: 'confirmed' | 'cancelled';
  collected_fields?: Record<string, unknown> | null;
  booking_data?: Record<string, unknown> | null;
  created_at: string;
}
interface BookingsResponse {
  items: Booking[];
  meta: {
    next_cursor?: string;
  };
}
type BookingsFilters = {
  search: string | undefined;
  status: string | undefined;
  dateFrom: Date | undefined;
  dateTo: Date | undefined;
};
type BookingsQueryKey = ['bookings', BookingsFilters];
function normalizeTimeForDateTime(time?: string | null): string {
  if (!time) {
    return '00:00:00';
  }
  return time.length === 5 ? `${time}:00` : time;
}
function normalizeBooking(raw: RawBooking): Booking {
  const bookingDate = raw.booking_date ?? raw.booking_datetime?.slice(0, 10) ?? '';
  return {
    id: raw.id,
    customer_id: raw.customer_id,
    customer_name: raw.customer_name ?? null,
    customer_phone: raw.customer_phone ?? null,
    service_type: raw.service_type ?? raw.service_name ?? 'General',
    service_name: raw.service_name ?? undefined,
    booking_date: bookingDate || undefined,
    booking_time: raw.booking_time ?? null,
    booking_datetime:
      raw.booking_datetime ??
      (bookingDate ? `${bookingDate}T${normalizeTimeForDateTime(raw.booking_time)}` : ''),
    status: raw.status,
    collected_fields: raw.collected_fields ?? raw.booking_data ?? {},
    created_at: raw.created_at,
  };
}
function toDateOnly(value: Date): string {
  return formatLocalDateOnly(value);
}
export function useBookingsList(
  search?: string,
  status?: string,
  dateFrom?: Date,
  dateTo?: Date,
  enabled = true
) {
  return useInfiniteQuery<BookingsResponse, Error, InfiniteData<BookingsResponse>, BookingsQueryKey, string | null>({
    queryKey: ['bookings', { search, status, dateFrom, dateTo }],
    queryFn: async ({ pageParam }): Promise<BookingsResponse> => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (search) params.append('search', search);
      if (status && status !== 'all') params.append('status', status);
      if (dateFrom) params.append('date_from', toDateOnly(dateFrom));
      if (dateTo) params.append('date_to', toDateOnly(dateTo));
      const response = await api.get<RawBooking[]>(`/api/v1/bookings?${params.toString()}`);
      return {
        items: response.data.map(normalizeBooking),
        meta: { next_cursor: response.meta?.next_cursor },
      };
    },
    getNextPageParam: (lastPage) => lastPage.meta?.next_cursor,
    initialPageParam: null,
    enabled,
  });
}
export function useUpdateBooking() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      updates
    }: {
      id: string;
      updates: { status?: string; booking_date?: string; booking_time?: string }
    }) => {
      const response = await api.patch<{ updated: boolean }>(`/api/v1/bookings/${id}`, updates);
      return response;
    },
    onSuccess: () => {
      // Refresh the bookings list on mutation success
      queryClient.invalidateQueries({ queryKey: ['bookings'] });
    },
  });
}
