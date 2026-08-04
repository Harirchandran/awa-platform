import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';
export type PaymentStatus = 'draft' | 'sent' | 'viewed' | 'paid' | 'expired' | 'cancelled' | 'failed' | 'refunded';
export type PaymentType = 'booking_deposit' | 'booking_full_payment' | 'order_payment' | 'custom_payment' | 'balance_due';
export type PaymentProvider = 'manual' | 'external' | 'razorpay' | 'upi' | 'cash' | 'bank_transfer';
export type PaymentAction = 'send' | 'cancel' | 'mark_paid' | 'mark_failed' | 'mark_refunded' | 'expire';
export interface PaymentRequest {
  id: string;
  tenant_id: string;
  customer_id?: string | null;
  conversation_id?: string | null;
  booking_id?: string | null;
  order_id?: string | null;
  amount: number;
  currency: string;
  type: PaymentType;
  status: PaymentStatus;
  provider: PaymentProvider;
  provider_payment_link_id?: string | null;
  provider_order_id?: string | null;
  provider_payment_id?: string | null;
  payment_url?: string | null;
  expires_at?: string | null;
  sent_at?: string | null;
  paid_at?: string | null;
  cancelled_at?: string | null;
  created_by?: string | null;
  metadata?: Record<string, unknown>;
  customer_name?: string | null;
  customer_phone?: string | null;
  booking_service_name?: string | null;
  booking_date?: string | null;
  booking_time?: string | null;
  order_status?: string | null;
  order_payment_status?: string | null;
  created_at: string;
  updated_at?: string | null;
}
export interface PaymentEvent {
  id: string;
  tenant_id: string;
  payment_request_id: string;
  event_type: string;
  actor_type: string;
  actor_user_id?: string | null;
  provider_event_id?: string | null;
  provider_payload?: Record<string, unknown>;
  event_data?: Record<string, unknown>;
  created_at: string;
}
type PaymentFilters = {
  status: string | undefined;
  type: string | undefined;
  search: string | undefined;
};
type PaymentsQueryKey = ['payments', PaymentFilters];
interface PaymentsResponse {
  items: PaymentRequest[];
  meta: {
    next_cursor?: string;
  };
}
export type CreatePaymentPayload = {
  customer_id?: string | null;
  booking_id?: string | null;
  order_id?: string | null;
  amount?: number;
  currency: string;
  type: PaymentType;
  provider: PaymentProvider;
  payment_url?: string | null;
  expires_at?: string | null;
  idempotency_key?: string | null;
  metadata?: Record<string, unknown>;
};
function toNumber(value: number | string | null | undefined): number {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(parsed) ? Number(parsed) : 0;
}
function normalizePayment(raw: PaymentRequest): PaymentRequest {
  return {
    ...raw,
    amount: toNumber(raw.amount),
    currency: raw.currency || 'INR',
    metadata: raw.metadata || {},
  };
}
function normalizePaymentEvent(raw: PaymentEvent): PaymentEvent {
  return {
    ...raw,
    event_data: raw.event_data || {},
    provider_payload: raw.provider_payload || {},
  };
}
export function formatPaymentMoney(amount: number, currency = 'INR'): string {
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency,
      maximumFractionDigits: amount % 1 === 0 ? 0 : 2,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(amount % 1 === 0 ? 0 : 2)}`;
  }
}
export function usePaymentsList(status?: string, type?: string, search?: string) {
  return useInfiniteQuery<PaymentsResponse, Error, InfiniteData<PaymentsResponse>, PaymentsQueryKey, string | null>({
    queryKey: ['payments', { status, type, search }],
    queryFn: async ({ pageParam }): Promise<PaymentsResponse> => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (status && status !== 'all') params.append('status', status);
      if (type && type !== 'all') params.append('type', type);
      if (search) params.append('search', search);
      const response = await api.get<PaymentRequest[]>(`/api/v1/payments?${params.toString()}`);
      return {
        items: response.data.map(normalizePayment),
        meta: { next_cursor: response.meta?.next_cursor },
      };
    },
    getNextPageParam: (lastPage) => lastPage.meta?.next_cursor,
    initialPageParam: null,
  });
}
export function usePaymentDetail(paymentId?: string) {
  return useQuery({
    queryKey: ['payments', 'detail', paymentId],
    queryFn: async () => {
      const response = await api.get<PaymentRequest>(`/api/v1/payments/${paymentId}`);
      return normalizePayment(response.data);
    },
    enabled: !!paymentId,
  });
}
export function usePaymentEvents(paymentId?: string) {
  return useQuery({
    queryKey: ['payments', 'events', paymentId],
    queryFn: async () => {
      const response = await api.get<PaymentEvent[]>(`/api/v1/payments/${paymentId}/events`);
      return response.data.map(normalizePaymentEvent);
    },
    enabled: !!paymentId,
  });
}
export function useCreatePayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreatePaymentPayload) => {
      const response = await api.post<PaymentRequest>('/api/v1/payments', payload);
      return normalizePayment(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['payments'] });
      queryClient.invalidateQueries({ queryKey: ['orders'] });
    },
  });
}
export function useActionPayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      action,
      note,
      provider_payment_id,
      payment_url,
    }: {
      id: string;
      action: PaymentAction;
      note?: string;
      provider_payment_id?: string;
      payment_url?: string;
    }) => {
      const response = await api.post<PaymentRequest>(`/api/v1/payments/${id}/actions`, {
        action,
        note,
        provider_payment_id,
        payment_url,
      });
      return normalizePayment(response.data);
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['payments'] });
      queryClient.invalidateQueries({ queryKey: ['payments', 'detail', variables.id] });
      queryClient.invalidateQueries({ queryKey: ['payments', 'events', variables.id] });
      queryClient.invalidateQueries({ queryKey: ['orders'] });
    },
  });
}
export interface PaymentMetrics {
  pending: number;
  needsAttention: number;
  paidAmount: number;
}
export function usePaymentMetrics() {
  return useQuery<PaymentMetrics, Error>({
    queryKey: ['payments', 'metrics'],
    queryFn: async (): Promise<PaymentMetrics> => {
      const response = await api.get<{ pending: number; needs_attention: number; paid_amount: number }>('/api/v1/payments/metrics');
      return {
        pending: response.data.pending,
        needsAttention: response.data.needs_attention,
        paidAmount: response.data.paid_amount,
      };
    },
  });
}