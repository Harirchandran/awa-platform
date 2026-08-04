import { useInfiniteQuery, useQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';
export interface Customer {
  id: string;
  phone_number: string;
  name: string | null;
  email: string | null;
  consent_status?: 'pending' | 'granted' | 'withdrawn';
  metadata: Record<string, unknown>;
  first_seen: string;
  last_interaction: string;
  conversation_count: number;
  booking_count: number;
  order_count: number;
  total_order_value: number;
  tags: CustomerTag[];
}
interface RawCustomer {
  id: string;
  phone_number: string;
  name?: string | null;
  email?: string | null;
  consent_status?: 'pending' | 'granted' | 'withdrawn';
  metadata?: Record<string, unknown> | null;
  first_seen?: string | null;
  last_interaction?: string | null;
  created_at?: string | null;
  conversation_count?: number | null;
  booking_count?: number | null;
  order_count?: number | null;
  total_order_value?: number | null;
  tags?: CustomerTag[] | null;
}
export interface CustomerUpdateRequest {
  name?: string;
  email?: string;
  consent_status?: 'pending' | 'granted' | 'withdrawn';
  metadata?: Record<string, unknown>;
}
type CustomersResponse = {
  items: Customer[];
  meta: {
    next_cursor?: string;
  };
};
export interface CustomerTag {
  id: string;
  name: string;
  color: string;
  description?: string | null;
  created_at?: string;
  updated_at?: string;
}
export interface CustomerNote {
  id: string;
  customer_id: string;
  author_user_id?: string | null;
  author_email?: string | null;
  note: string;
  visibility: 'internal';
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
export interface CustomerTimelineItem {
  occurred_at: string;
  event_type: 'note' | 'conversation' | 'booking' | 'order';
  ref_id: string;
  title: string;
  subtitle?: string | null;
  data: Record<string, unknown>;
}
function normalizeCustomer(raw: RawCustomer): Customer {
  const firstSeen = raw.first_seen ?? raw.created_at ?? '';
  return {
    id: raw.id,
    phone_number: raw.phone_number,
    name: raw.name ?? null,
    email: raw.email ?? null,
    consent_status: raw.consent_status ?? 'pending',
    metadata: raw.metadata ?? {},
    first_seen: firstSeen,
    last_interaction: raw.last_interaction ?? firstSeen,
    conversation_count: raw.conversation_count ?? 0,
    booking_count: raw.booking_count ?? 0,
    order_count: raw.order_count ?? 0,
    total_order_value: raw.total_order_value ?? 0,
    tags: raw.tags ?? [],
  };
}
export function useCustomersList(searchTerm: string) {
  return useInfiniteQuery<CustomersResponse, Error, InfiniteData<CustomersResponse>, ['customers', { search: string }], string | null>({
    queryKey: ['customers', { search: searchTerm }],
    queryFn: async ({ pageParam = null }) => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (searchTerm) params.append('search', searchTerm);
      const response = await api.get<RawCustomer[]>(`/api/v1/customers?${params.toString()}`);
      return { items: response.data.map(normalizeCustomer), meta: { next_cursor: response.meta?.next_cursor } };
    },
    getNextPageParam: (lastPage) => lastPage.meta?.next_cursor,
    initialPageParam: null,
  });
}
export function useCustomer(id: string | null) {
  return useQuery({
    queryKey: ['customer', id],
    queryFn: async (): Promise<Customer> => {
      const response = await api.get<RawCustomer>(`/api/v1/customers/${id}`);
      return normalizeCustomer(response.data);
    },
    enabled: !!id,
  });
}
export function useUpdateCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: CustomerUpdateRequest }) => {
      const response = await api.patch<{ updated: boolean }>(`/api/v1/customers/${id}`, data);
      return response;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['customers'] });
      queryClient.invalidateQueries({ queryKey: ['customer', variables.id] });
    },
  });
}
export function useCustomerTags() {
  return useQuery({
    queryKey: ['customer-tags'],
    queryFn: async (): Promise<CustomerTag[]> => {
      const response = await api.get<CustomerTag[]>('/api/v1/customer-tags');
      return response.data;
    },
  });
}
export function useCreateCustomerTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: { name: string; color: string; description?: string }) => {
      const response = await api.post<CustomerTag>('/api/v1/customer-tags', data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['customer-tags'] });
    },
  });
}
export function useAddCustomerTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ customerId, tagId }: { customerId: string; tagId: string }) => {
      const response = await api.post<{ attached: boolean }>(`/api/v1/customers/${customerId}/tags`, { tag_id: tagId });
      return response.data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['customers'] });
      queryClient.invalidateQueries({ queryKey: ['customer', variables.customerId] });
    },
  });
}
export function useRemoveCustomerTag() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ customerId, tagId }: { customerId: string; tagId: string }) => {
      const response = await api.delete<{ removed: boolean }>(`/api/v1/customers/${customerId}/tags/${tagId}`);
      return response.data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['customers'] });
      queryClient.invalidateQueries({ queryKey: ['customer', variables.customerId] });
    },
  });
}
export function useCustomerNotes(customerId: string | null) {
  return useQuery({
    queryKey: ['customer-notes', customerId],
    queryFn: async (): Promise<CustomerNote[]> => {
      const response = await api.get<CustomerNote[]>(`/api/v1/customers/${customerId}/notes`);
      return response.data;
    },
    enabled: !!customerId,
  });
}
export function useCreateCustomerNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ customerId, note }: { customerId: string; note: string }) => {
      const response = await api.post<CustomerNote>(`/api/v1/customers/${customerId}/notes`, { note, visibility: 'internal' });
      return response.data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['customer-notes', variables.customerId] });
      queryClient.invalidateQueries({ queryKey: ['customer-timeline', variables.customerId] });
    },
  });
}
export function useDeleteCustomerNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ customerId, noteId }: { customerId: string; noteId: string }) => {
      const response = await api.delete<{ deleted: boolean }>(`/api/v1/customers/${customerId}/notes/${noteId}`);
      return response.data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['customer-notes', variables.customerId] });
      queryClient.invalidateQueries({ queryKey: ['customer-timeline', variables.customerId] });
    },
  });
}
export function useCustomerTimeline(customerId: string | null) {
  return useQuery({
    queryKey: ['customer-timeline', customerId],
    queryFn: async (): Promise<CustomerTimelineItem[]> => {
      const response = await api.get<CustomerTimelineItem[]>(`/api/v1/customers/${customerId}/timeline`);
      return response.data;
    },
    enabled: !!customerId,
  });
}
export function useMergeCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ primaryId, secondaryId }: { primaryId: string; secondaryId: string }) => {
      const response = await api.post<{ merged: boolean; primary_id: string; secondary_id: string }>(
        `/api/v1/customers/${primaryId}/merge`,
        { secondary_customer_id: secondaryId }
      );
      return response.data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['customers'] });
      queryClient.invalidateQueries({ queryKey: ['customer', variables.primaryId] });
    },
  });
}