import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';
export type ConversationStatus = 'active' | 'closed' | 'escalated';
export interface Conversation {
  id: string;
  customer_id: string;
  customer_name?: string;
  customer_phone: string;
  customer_email?: string | null;
  status: ConversationStatus;
  workflow_state: string;
  ai_mode?: 'standard' | 'advanced' | 'concierge' | string | null;
  channel_type?: string | null;
  channel_account_id?: string | null;
  channel_session_id?: string | null;
  escalation_reason?: string | null;
  escalated_at?: string | null;
  resolved_at?: string | null;
  assigned_to?: string | null;
  resume_mode?: 'automatic' | 'customer' | 'tenant' | 'manual' | null;
  last_message_at: string;
  created_at: string;
}
export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  message_type?: 'text' | 'interactive' | 'template' | 'image' | 'video' | 'audio' | 'document' | 'location' | 'contacts' | 'unknown';
  raw_payload?: unknown;
  sent_at: string;
  delivery_status?: 'sent' | 'delivered' | 'read' | 'failed';
  channel_type?: string;
}
type ConversationsResponse = {
  items: Conversation[];
  meta: {
    next_cursor?: string;
  };
};
export interface EscalationReplyResult {
  conversation: Conversation;
  message_id: string;
  delivery_channel: 'awa_chat' | 'whatsapp' | 'email';
  delivery_status: string;
  knowledge_saved: boolean;
  knowledge_id?: string | null;
}
interface ConversationMessageRecord {
  id: string;
  direction: 'inbound' | 'outbound';
  message_type?: Message['message_type'];
  message_text: string;
  raw_payload?: unknown;
  delivery_status?: 'sent' | 'delivered' | 'read' | 'failed';
  processing_status?: string;
  channel_type?: string;
  created_at: string;
}
export function useConversationsList(searchTerm: string, statusFilter: string) {
  return useInfiniteQuery<ConversationsResponse, Error, InfiniteData<ConversationsResponse>, ['conversations', { search: string; status: string }], string | null>({
    queryKey: ['conversations', { search: searchTerm, status: statusFilter }],
    queryFn: async ({ pageParam = null }) => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (searchTerm) params.append('search', searchTerm);
      if (statusFilter && statusFilter !== 'all') params.append('status', statusFilter);
      const response = await api.get<Conversation[]>(`/api/v1/conversations?${params.toString()}`);
      return { items: response.data, meta: { next_cursor: response.meta?.next_cursor } };
    },
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor || undefined,
    initialPageParam: null,
    refetchInterval: 10000,
  });
}
export function useEscalationsList(searchTerm: string) {
  return useInfiniteQuery<ConversationsResponse, Error, InfiniteData<ConversationsResponse>, ['escalations', { search: string }], string | null>({
    queryKey: ['escalations', { search: searchTerm }],
    queryFn: async ({ pageParam = null }) => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (searchTerm) params.append('search', searchTerm);
      const response = await api.get<Conversation[]>(`/api/v1/escalations?${params.toString()}`);
      return { items: response.data, meta: { next_cursor: response.meta?.next_cursor } };
    },
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor || undefined,
    initialPageParam: null,
    refetchInterval: 10000,
  });
}
export function useConversation(conversationId: string | undefined) {
  return useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: async (): Promise<Conversation> => {
      const response = await api.get<Conversation>(`/api/v1/conversations/${conversationId}`);
      return response.data;
    },
    enabled: !!conversationId,
    refetchInterval: 10000,
  });
}
export function useConversationMessages(conversationId: string | undefined) {
  return useInfiniteQuery<{ items: Message[]; next_cursor: string | null }, Error, InfiniteData<{ items: Message[]; next_cursor: string | null }>, ['conversation_messages', string | undefined], string | null>({
    queryKey: ['conversation_messages', conversationId],
    queryFn: async ({ pageParam = null }) => {
      const resolvedConversationId = conversationId ?? '';
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      params.append('limit', '50');
      const response = await api.get<ConversationMessageRecord[]>(`/api/v1/conversations/${resolvedConversationId}/messages?${params.toString()}`);
      const mapped = response.data.map((message) => ({
        id: message.id,
        conversation_id: resolvedConversationId,
        role: (message.direction === 'inbound' ? 'user' : 'assistant') as 'user' | 'assistant',
        content: message.message_text,
        message_type: message.message_type,
        raw_payload: normalizeRawPayload(message.raw_payload),
        sent_at: message.created_at,
        delivery_status: message.delivery_status,
        channel_type: message.channel_type,
      }));
      return {
        items: mapped,
        next_cursor: response.meta?.next_cursor || null,
      };
    },
    enabled: !!conversationId,
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
    initialPageParam: null,
    refetchInterval: 5000,
  });
}
export function useEscalationAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      conversationId: string;
      action: 'escalate' | 'return-to-awa' | 'resolve';
      reason?: string;
    }): Promise<Conversation> => {
      const suffix = payload.action === 'escalate' ? '' : `/${payload.action}`;
      const response = await api.post<Conversation>(
        `/api/v1/conversations/${payload.conversationId}/escalation${suffix}`,
        payload.reason ? { reason: payload.reason } : {}
      );
      return response.data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['escalations'] });
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
      queryClient.invalidateQueries({ queryKey: ['conversation', variables.conversationId] });
      queryClient.invalidateQueries({ queryKey: ['conversation_messages', variables.conversationId] });
    },
  });
}
export function useEscalationReply() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      conversationId: string;
      messageText: string;
      saveToKnowledge?: boolean;
      closeAfterReply?: boolean;
    }): Promise<EscalationReplyResult> => {
      const response = await api.post<EscalationReplyResult>(
        `/api/v1/conversations/${payload.conversationId}/escalation/reply`,
        {
          message_text: payload.messageText,
          save_to_knowledge: payload.saveToKnowledge ?? false,
          close_after_reply: payload.closeAfterReply ?? false,
        }
      );
      return response.data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['escalations'] });
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
      queryClient.invalidateQueries({ queryKey: ['conversation', variables.conversationId] });
      queryClient.invalidateQueries({ queryKey: ['conversation_messages', variables.conversationId] });
    },
  });
}
function normalizeRawPayload(payload: unknown) {
  if (typeof payload !== 'string') return payload;
  try {
    return JSON.parse(payload);
  } catch {
    return payload;
  }
}
