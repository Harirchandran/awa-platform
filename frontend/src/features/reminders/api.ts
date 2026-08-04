import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';
export type ReminderTrigger =
  | 'manual'
  | 'before_booking'
  | 'after_booking'
  | 'before_payment_expiry'
  | 'after_payment_failed'
  | 'after_order_completed'
  | 'inactive_conversation'
  | 'custom';
export type ReminderChannel = 'whatsapp' | 'email' | 'manual';
export type ScheduledMessageStatus = 'scheduled' | 'due' | 'snoozed' | 'sent' | 'failed' | 'cancelled' | 'skipped';
export type ScheduledMessageAction = 'cancel' | 'snooze' | 'mark_sent' | 'mark_failed' | 'skip' | 'mark_due' | 'retry';
export interface ReminderRule {
  id: string;
  tenant_id: string;
  name: string;
  trigger_type: ReminderTrigger;
  offset_minutes: number;
  channel: ReminderChannel;
  template_text: string;
  is_active: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at?: string | null;
}
export interface ScheduledMessage {
  id: string;
  tenant_id: string;
  rule_id?: string | null;
  rule_name?: string | null;
  customer_id?: string | null;
  conversation_id?: string | null;
  booking_id?: string | null;
  order_id?: string | null;
  payment_request_id?: string | null;
  review_request_id?: string | null;
  trigger_type: ReminderTrigger;
  channel: ReminderChannel;
  message_text: string;
  status: ScheduledMessageStatus;
  scheduled_for: string;
  locked_at?: string | null;
  sent_at?: string | null;
  cancelled_at?: string | null;
  attempt_count: number;
  last_error?: string | null;
  created_by?: string | null;
  metadata?: Record<string, unknown>;
  customer_name?: string | null;
  customer_phone?: string | null;
  booking_service_name?: string | null;
  booking_date?: string | null;
  booking_time?: string | null;
  order_status?: string | null;
  payment_status?: string | null;
  created_at: string;
  updated_at?: string | null;
}
export interface ScheduledMessageEvent {
  id: string;
  tenant_id: string;
  scheduled_message_id: string;
  event_type: string;
  actor_type: string;
  actor_user_id?: string | null;
  event_data?: Record<string, unknown>;
  created_at: string;
}
export type CreateReminderRulePayload = {
  name: string;
  trigger_type: ReminderTrigger;
  offset_minutes: number;
  channel: ReminderChannel;
  template_text: string;
  is_active?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  metadata?: Record<string, unknown>;
};
export type CreateScheduledMessagePayload = {
  rule_id?: string | null;
  customer_id?: string | null;
  conversation_id?: string | null;
  booking_id?: string | null;
  order_id?: string | null;
  payment_request_id?: string | null;
  review_request_id?: string | null;
  trigger_type: ReminderTrigger;
  channel: ReminderChannel;
  message_text: string;
  scheduled_for: string;
  metadata?: Record<string, unknown>;
};
type ReminderFilters = {
  status: string | undefined;
  triggerType: string | undefined;
  channel: string | undefined;
  search: string | undefined;
};
type ScheduledMessagesQueryKey = ['scheduled-messages', ReminderFilters];
interface ScheduledMessagesResponse {
  items: ScheduledMessage[];
  meta: {
    next_cursor?: string;
  };
}
function toNumber(value: number | string | null | undefined): number {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(parsed) ? Number(parsed) : 0;
}
function normalizeRule(raw: ReminderRule): ReminderRule {
  return {
    ...raw,
    offset_minutes: toNumber(raw.offset_minutes),
    metadata: raw.metadata || {},
  };
}
function normalizeMessage(raw: ScheduledMessage): ScheduledMessage {
  return {
    ...raw,
    attempt_count: toNumber(raw.attempt_count),
    metadata: raw.metadata || {},
  };
}
function normalizeEvent(raw: ScheduledMessageEvent): ScheduledMessageEvent {
  return {
    ...raw,
    event_data: raw.event_data || {},
  };
}
export function useReminderRules(activeOnly = false) {
  return useQuery({
    queryKey: ['reminder-rules', { activeOnly }],
    queryFn: async () => {
      const response = await api.get<ReminderRule[]>(`/api/v1/reminder-rules?active_only=${activeOnly}`);
      return response.data.map(normalizeRule);
    },
  });
}
export function useScheduledMessages(status?: string, triggerType?: string, channel?: string, search?: string) {
  return useInfiniteQuery<ScheduledMessagesResponse, Error, InfiniteData<ScheduledMessagesResponse>, ScheduledMessagesQueryKey, string | null>({
    queryKey: ['scheduled-messages', { status, triggerType, channel, search }],
    queryFn: async ({ pageParam }): Promise<ScheduledMessagesResponse> => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (status && status !== 'all') params.append('status', status);
      if (triggerType && triggerType !== 'all') params.append('trigger_type', triggerType);
      if (channel && channel !== 'all') params.append('channel', channel);
      if (search) params.append('search', search);
      const response = await api.get<ScheduledMessage[]>(`/api/v1/scheduled-messages?${params.toString()}`);
      return {
        items: response.data.map(normalizeMessage),
        meta: { next_cursor: response.meta?.next_cursor },
      };
    },
    getNextPageParam: (lastPage) => lastPage.meta?.next_cursor,
    initialPageParam: null,
  });
}
export type ScheduledMessageMetrics = {
  scheduled: number;
  due: number;
  failed: number;
  sent: number;
  skipped: number;
};
export function useScheduledMessageMetrics() {
  return useQuery({
    queryKey: ['scheduled-messages', 'metrics'],
    queryFn: async () => {
      const response = await api.get<ScheduledMessageMetrics>('/api/v1/scheduled-messages/metrics');
      return response.data;
    },
  });
}
export function useScheduledMessageDetail(messageId?: string) {
  return useQuery({
    queryKey: ['scheduled-messages', 'detail', messageId],
    queryFn: async () => {
      const response = await api.get<ScheduledMessage>(`/api/v1/scheduled-messages/${messageId}`);
      return normalizeMessage(response.data);
    },
    enabled: !!messageId,
  });
}
export function useScheduledMessageEvents(messageId?: string) {
  return useQuery({
    queryKey: ['scheduled-messages', 'events', messageId],
    queryFn: async () => {
      const response = await api.get<ScheduledMessageEvent[]>(`/api/v1/scheduled-messages/${messageId}/events`);
      return response.data.map(normalizeEvent);
    },
    enabled: !!messageId,
  });
}
export function useCreateReminderRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateReminderRulePayload) => {
      const response = await api.post<ReminderRule>('/api/v1/reminder-rules', payload);
      return normalizeRule(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reminder-rules'] });
    },
  });
}
export function useUpdateReminderRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, updates }: { id: string; updates: Partial<CreateReminderRulePayload> }) => {
      const response = await api.patch<ReminderRule>(`/api/v1/reminder-rules/${id}`, updates);
      return normalizeRule(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reminder-rules'] });
    },
  });
}
export function useDeleteReminderRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const response = await api.delete<{ deleted: boolean }>(`/api/v1/reminder-rules/${id}`);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reminder-rules'] });
    },
  });
}
export function useCreateScheduledMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateScheduledMessagePayload) => {
      const response = await api.post<ScheduledMessage>('/api/v1/scheduled-messages', payload);
      return normalizeMessage(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scheduled-messages'] });
    },
  });
}
export function useActionScheduledMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      action,
      scheduled_for,
      snooze_minutes,
      note,
      last_error,
    }: {
      id: string;
      action: ScheduledMessageAction;
      scheduled_for?: string;
      snooze_minutes?: number;
      note?: string;
      last_error?: string;
    }) => {
      const response = await api.post<ScheduledMessage>(`/api/v1/scheduled-messages/${id}/actions`, {
        action,
        scheduled_for,
        snooze_minutes,
        note,
        last_error,
      });
      return normalizeMessage(response.data);
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['scheduled-messages'] });
      queryClient.invalidateQueries({ queryKey: ['scheduled-messages', 'detail', variables.id] });
      queryClient.invalidateQueries({ queryKey: ['scheduled-messages', 'events', variables.id] });
    },
  });
}