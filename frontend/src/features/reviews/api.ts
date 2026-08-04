import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';
export type ReviewStatus =
  | 'draft'
  | 'scheduled'
  | 'sent'
  | 'responded'
  | 'public_review_requested'
  | 'negative_feedback_escalated'
  | 'skipped'
  | 'expired';
export type ReviewSource = 'manual' | 'booking' | 'order' | 'reminder' | 'automation';
export type ReviewAction = 'send' | 'submit_rating' | 'request_public_review' | 'escalate_negative' | 'skip' | 'expire' | 'reopen';
export interface ReviewSettings {
  id: string;
  tenant_id: string;
  is_active: boolean;
  public_review_url?: string | null;
  ask_after_booking_completed: boolean;
  ask_after_order_completed: boolean;
  ask_delay_minutes: number;
  positive_rating_threshold: number;
  negative_feedback_escalation_enabled: boolean;
  request_template: string;
  positive_template: string;
  negative_template: string;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at?: string | null;
}
export interface ReviewRequest {
  id: string;
  tenant_id: string;
  customer_id?: string | null;
  conversation_id?: string | null;
  booking_id?: string | null;
  order_id?: string | null;
  status: ReviewStatus;
  source: ReviewSource;
  rating?: number | null;
  feedback_text?: string | null;
  public_review_url?: string | null;
  sent_at?: string | null;
  responded_at?: string | null;
  public_requested_at?: string | null;
  escalated_at?: string | null;
  skipped_at?: string | null;
  created_by?: string | null;
  idempotency_key?: string | null;
  metadata?: Record<string, unknown>;
  customer_name?: string | null;
  customer_phone?: string | null;
  booking_service_name?: string | null;
  booking_date?: string | null;
  booking_time?: string | null;
  order_status?: string | null;
  order_total_amount?: number | null;
  created_at: string;
  updated_at?: string | null;
}
export interface ReviewEvent {
  id: string;
  tenant_id: string;
  review_request_id: string;
  event_type: string;
  actor_type: string;
  actor_user_id?: string | null;
  event_data?: Record<string, unknown>;
  created_at: string;
}
export type CreateReviewPayload = {
  customer_id?: string | null;
  conversation_id?: string | null;
  booking_id?: string | null;
  order_id?: string | null;
  status?: ReviewStatus;
  source: ReviewSource;
  public_review_url?: string | null;
  metadata?: Record<string, unknown>;
};
export type UpdateReviewSettingsPayload = Partial<Omit<ReviewSettings, 'id' | 'tenant_id' | 'created_at' | 'updated_at'>>;
type ReviewFilters = {
  status: string | undefined;
  source: string | undefined;
  rating: string | undefined;
  search: string | undefined;
};
type ReviewsQueryKey = ['reviews', ReviewFilters];
interface ReviewsResponse {
  items: ReviewRequest[];
  meta: {
    next_cursor?: string;
  };
}
function toNumber(value: number | string | null | undefined): number {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(parsed) ? Number(parsed) : 0;
}
function normalizeSettings(raw: ReviewSettings): ReviewSettings {
  return {
    ...raw,
    ask_delay_minutes: toNumber(raw.ask_delay_minutes),
    positive_rating_threshold: toNumber(raw.positive_rating_threshold),
    metadata: raw.metadata || {},
  };
}
function normalizeReview(raw: ReviewRequest): ReviewRequest {
  return {
    ...raw,
    rating: raw.rating == null ? null : toNumber(raw.rating),
    order_total_amount: raw.order_total_amount == null ? null : toNumber(raw.order_total_amount),
    metadata: raw.metadata || {},
  };
}
function normalizeEvent(raw: ReviewEvent): ReviewEvent {
  return {
    ...raw,
    event_data: raw.event_data || {},
  };
}
export interface ReviewMetrics {
  status: ReviewStatus;
  count: number;
  rated_count: number;
  average_rating: number | null;
  low_ratings_count: number;
}

export function useReviewMetrics() {
  return useQuery<ReviewMetrics[]>({
    queryKey: ['reviews', 'metrics'],
    queryFn: async () => {
      const response = await api.get<ReviewMetrics[]>('/api/v1/reviews/metrics');
      return response.data;
    },
  });
}

export function useReviewSettings() {
  return useQuery({
    queryKey: ['reviews', 'settings'],
    queryFn: async () => {
      const response = await api.get<ReviewSettings>('/api/v1/reviews/settings');
      return normalizeSettings(response.data);
    },
  });
}
export function useUpdateReviewSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: UpdateReviewSettingsPayload) => {
      const response = await api.put<ReviewSettings>('/api/v1/reviews/settings', payload);
      return normalizeSettings(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reviews', 'settings'] });
    },
  });
}
export function useReviewsList(status?: string, source?: string, rating?: string, search?: string) {
  return useInfiniteQuery<ReviewsResponse, Error, InfiniteData<ReviewsResponse>, ReviewsQueryKey, string | null>({
    queryKey: ['reviews', { status, source, rating, search }],
    queryFn: async ({ pageParam }): Promise<ReviewsResponse> => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (status && status !== 'all') params.append('status', status);
      if (source && source !== 'all') params.append('source', source);
      if (rating && rating !== 'all') params.append('rating', rating);
      if (search) params.append('search', search);
      const response = await api.get<ReviewRequest[]>(`/api/v1/reviews?${params.toString()}`);
      return {
        items: response.data.map(normalizeReview),
        meta: { next_cursor: response.meta?.next_cursor },
      };
    },
    getNextPageParam: (lastPage) => lastPage.meta?.next_cursor,
    initialPageParam: null,
  });
}
export function useReviewDetail(reviewId?: string) {
  return useQuery({
    queryKey: ['reviews', 'detail', reviewId],
    queryFn: async () => {
      const response = await api.get<ReviewRequest>(`/api/v1/reviews/${reviewId}`);
      return normalizeReview(response.data);
    },
    enabled: !!reviewId,
  });
}
export function useReviewEvents(reviewId?: string) {
  return useQuery({
    queryKey: ['reviews', 'events', reviewId],
    queryFn: async () => {
      const response = await api.get<ReviewEvent[]>(`/api/v1/reviews/${reviewId}/events`);
      return response.data.map(normalizeEvent);
    },
    enabled: !!reviewId,
  });
}
export function useCreateReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateReviewPayload) => {
      const response = await api.post<ReviewRequest>('/api/v1/reviews', payload);
      return normalizeReview(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reviews'] });
    },
  });
}
export function useActionReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      action,
      rating,
      feedback_text,
      public_review_url,
      note,
    }: {
      id: string;
      action: ReviewAction;
      rating?: number;
      feedback_text?: string;
      public_review_url?: string;
      note?: string;
    }) => {
      const response = await api.post<ReviewRequest>(`/api/v1/reviews/${id}/actions`, {
        action,
        rating,
        feedback_text,
        public_review_url,
        note,
      });
      return normalizeReview(response.data);
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['reviews'] });
      queryClient.invalidateQueries({ queryKey: ['reviews', 'detail', variables.id] });
      queryClient.invalidateQueries({ queryKey: ['reviews', 'events', variables.id] });
    },
  });
}

export function useDeleteReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const response = await api.delete<{ success: boolean }>(`/api/v1/reviews/${id}`);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reviews'] });
    },
  });
}