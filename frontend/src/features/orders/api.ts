import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';
export type OrderStatus = 'new' | 'accepted' | 'preparing' | 'ready' | 'completed' | 'cancelled';
export type PaymentStatus = 'not_collected' | 'pending' | 'paid' | 'failed' | 'refunded';
export interface OrderItem {
  id: string;
  item_id?: string | null;
  item_name: string;
  quantity: number;
  unit_price: number;
  line_total: number;
  item_snapshot?: Record<string, unknown> | null;
}
export interface OrderEvent {
  id: string;
  event_type: string;
  actor_type: string;
  event_data?: Record<string, unknown> | null;
  created_at: string;
}
export interface Order {
  id: string;
  customer_id?: string | null;
  conversation_id?: string | null;
  channel_type: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  status: OrderStatus;
  currency: string;
  subtotal_amount: number;
  total_amount: number;
  fulfillment_type: string;
  payment_method?: string | null;
  payment_status: PaymentStatus;
  delivery_address?: string | null;
  customer_snapshot?: Record<string, unknown> | null;
  order_data?: Record<string, unknown> | null;
  item_count?: number;
  items?: OrderItem[];
  events?: OrderEvent[];
  created_at: string;
  updated_at?: string | null;
}
interface RawOrder extends Omit<Order, 'subtotal_amount' | 'total_amount' | 'items' | 'events'> {
  subtotal_amount?: number | string | null;
  total_amount?: number | string | null;
  items?: RawOrderItem[];
  events?: RawOrderEvent[];
}
interface RawOrderItem extends Omit<OrderItem, 'quantity' | 'unit_price' | 'line_total'> {
  quantity?: number | string | null;
  unit_price?: number | string | null;
  line_total?: number | string | null;
}
type RawOrderEvent = OrderEvent;
interface OrdersResponse {
  items: Order[];
  meta: {
    next_cursor?: string;
  };
}
type OrdersFilters = {
  status: string | undefined;
  fulfillment: string | undefined;
  search: string | undefined;
  dateFrom: Date | undefined;
  dateTo: Date | undefined;
};
type OrdersQueryKey = ['orders', OrdersFilters];
function toNumber(value: number | string | null | undefined): number {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(parsed) ? Number(parsed) : 0;
}
function toDateOnly(value: Date): string {
  return value.toISOString().slice(0, 10);
}
function parseRecord(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value !== 'string') return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}
function normalizeOrderItem(raw: RawOrderItem): OrderItem {
  return {
    ...raw,
    item_name: raw.item_name || 'Item',
    quantity: toNumber(raw.quantity),
    unit_price: toNumber(raw.unit_price),
    line_total: toNumber(raw.line_total),
  };
}
function normalizeOrder(raw: RawOrder): Order {
  return {
    ...raw,
    channel_type: raw.channel_type || 'awa_chat',
    status: raw.status || 'new',
    currency: raw.currency || 'INR',
    subtotal_amount: toNumber(raw.subtotal_amount),
    total_amount: toNumber(raw.total_amount),
    fulfillment_type: raw.fulfillment_type || 'pickup',
    payment_status: raw.payment_status || 'not_collected',
    customer_snapshot: parseRecord(raw.customer_snapshot),
    order_data: parseRecord(raw.order_data),
    item_count: raw.item_count ?? raw.items?.length ?? 0,
    items: raw.items?.map(normalizeOrderItem),
    events: raw.events,
  };
}
export function formatMoney(amount: number, currency = 'INR'): string {
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
export function useOrdersList(
  status?: string,
  fulfillment?: string,
  search?: string,
  dateFrom?: Date,
  dateTo?: Date,
  enabled = true
) {
  return useInfiniteQuery<OrdersResponse, Error, InfiniteData<OrdersResponse>, OrdersQueryKey, string | null>({
    queryKey: ['orders', { status, fulfillment, search, dateFrom, dateTo }],
    queryFn: async ({ pageParam }): Promise<OrdersResponse> => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (status && status !== 'all') params.append('status', status);
      if (fulfillment && fulfillment !== 'all') params.append('fulfillment_type', fulfillment);
      if (search) params.append('search', search);
      if (dateFrom) params.append('date_from', toDateOnly(dateFrom));
      if (dateTo) params.append('date_to', toDateOnly(dateTo));
      const response = await api.get<RawOrder[]>(`/api/v1/orders?${params.toString()}`);
      return {
        items: response.data.map(normalizeOrder),
        meta: { next_cursor: response.meta?.next_cursor },
      };
    },
    getNextPageParam: (lastPage) => lastPage.meta?.next_cursor,
    initialPageParam: null,
    enabled,
  });
}
export function useOrderDetail(orderId?: string) {
  return useQuery({
    queryKey: ['orders', 'detail', orderId],
    queryFn: async () => {
      const response = await api.get<RawOrder>(`/api/v1/orders/${orderId}`);
      return normalizeOrder(response.data);
    },
    enabled: !!orderId,
  });
}
export function useUpdateOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      updates,
    }: {
      id: string;
      updates: {
        status?: OrderStatus;
        payment_status?: PaymentStatus;
        payment_method?: string;
        delivery_address?: string;
        internal_note?: string;
      };
    }) => {
      const response = await api.patch<{ updated: boolean }>(`/api/v1/orders/${id}`, updates);
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['orders'] });
    },
  });
}
