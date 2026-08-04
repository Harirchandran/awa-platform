import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import { useTenantStore } from '@/stores/tenant.store';
export interface AwaChatLink {
  id: string;
  tenant_id: string;
  slug: string;
  display_name: string;
  status: 'active' | 'paused' | 'disabled';
  public_status?: 'active' | 'paused' | 'disabled' | 'needs_rotation' | 'unknown';
  is_publicly_accessible?: boolean;
  status_reason?: string | null;
  scan_count: number;
  public_url: string;
  created_at: string;
  updated_at?: string;
  disabled_at?: string | null;
  last_opened_at?: string | null;
}
export interface PublicChatLinkInfo {
  slug: string;
  display_name: string;
  tenant_name: string;
  status: string;
  privacy_note: string;
  suggested_replies: string[];
  captcha_required?: boolean;
  captcha_mode?: 'disabled' | 'mock' | 'turnstile';
  customer_otp_required?: boolean;
}
export interface PublicChatSession {
  id: string;
  tenant_id: string;
  link_id: string;
  customer_id: string;
  conversation_id: string;
  visitor_name?: string | null;
  status: string;
  session_token: string;
  tenant_name: string;
  display_name: string;
}
export interface PublicChatMessage {
  id: string;
  direction: 'inbound' | 'outbound';
  message_type?: string;
  message_text: string;
  raw_payload?: unknown;
  cards?: PublicChatCard[];
  quick_replies?: string[];
  quick_replies_stale?: boolean;
  quick_replies_state?: 'none' | 'active' | 'expired';
  processing_status?: string;
  delivery_status?: string;
  created_at: string;
  channel_type?: string;
  conversation_status?: string;
}

export interface PublicChatProcessingTransition {
  kind: 'completed' | 'recoverable_failure' | 'terminal_failure';
  createdAt: string;
  closeVerification: true;
  clearProcessingLock: true;
  allowFreshConfirmation: boolean;
}

export function resolvePublicChatProcessingTransition(
  message: PublicChatMessage | undefined,
  lastHandledAt?: string | null,
): PublicChatProcessingTransition | null {
  if (!message || message.direction !== 'outbound') return null;
  const payload = message.raw_payload as Record<string, unknown> | undefined;
  const status = String(payload?.processing_status || '').toLowerCase();
  let kind: PublicChatProcessingTransition['kind'] | null = null;
  if (['completed', 'succeeded', 'processed'].includes(status)) kind = 'completed';
  else if (status === 'failed' && payload?.recoverable === true) kind = 'recoverable_failure';
  else if (['terminal_failed', 'rejected', 'expired'].includes(status)) kind = 'terminal_failure';
  if (!kind) return null;

  const createdAt = message.created_at || '';
  const currentTime = Date.parse(createdAt);
  const handledTime = Date.parse(lastHandledAt || '');
  if (Number.isFinite(currentTime) && Number.isFinite(handledTime) && currentTime < handledTime) return null;
  return {
    kind,
    createdAt,
    closeVerification: true,
    clearProcessingLock: true,
    allowFreshConfirmation: kind === 'recoverable_failure',
  };
}
export interface PublicChatCard {
  type: 'catalog_item' | 'service' | 'category';
  id?: string;
  title: string;
  subtitle?: string;
  description?: string;
  image_url?: string;
  thumbnail_url?: string;
  image_alt?: string;
  actions?: string[];
}
export function resolvePublicMediaUrl(url?: string | null): string | undefined {
  if (!url) return undefined;
  if (/^https?:\/\//i.test(url)) return url;
  const base = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
  return `${base}${url.startsWith('/') ? url : `/${url}`}`;
}
export function useAwaChatLinks() {
  const tenantId = useTenantStore((state) => state.activeTenantId);
  return useQuery({
    queryKey: ['awa-chat-links', tenantId],
    queryFn: async (): Promise<AwaChatLink[]> => {
      const response = await api.get<AwaChatLink[]>('/api/v1/awa-chat/links');
      return response.data;
    },
    enabled: !!tenantId,
    refetchInterval: 30000,
  });
}
export function useCreateAwaChatLink() {
  const queryClient = useQueryClient();
  const tenantId = useTenantStore((state) => state.activeTenantId);
  return useMutation({
    mutationFn: async (displayName?: string): Promise<AwaChatLink> => {
      const response = await api.post<AwaChatLink>('/api/v1/awa-chat/links', {
        display_name: displayName,
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['awa-chat-links', tenantId] });
    },
  });
}
export function useUpdateAwaChatLink() {
  const queryClient = useQueryClient();
  const tenantId = useTenantStore((state) => state.activeTenantId);
  return useMutation({
    mutationFn: async (payload: { id: string; display_name?: string; status?: AwaChatLink['status'] }): Promise<AwaChatLink> => {
      const response = await api.patch<AwaChatLink>(`/api/v1/awa-chat/links/${payload.id}`, {
        display_name: payload.display_name,
        status: payload.status,
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['awa-chat-links', tenantId] });
    },
  });
}
export function useRotateAwaChatLink() {
  const queryClient = useQueryClient();
  const tenantId = useTenantStore((state) => state.activeTenantId);
  return useMutation({
    mutationFn: async (id: string): Promise<AwaChatLink> => {
      const response = await api.post<AwaChatLink>(`/api/v1/awa-chat/links/${id}/rotate`);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['awa-chat-links', tenantId] });
    },
  });
}
export async function getPublicChatLink(slug: string): Promise<PublicChatLinkInfo> {
  const response = await api.get<PublicChatLinkInfo>(`/api/v1/public-chat/links/${slug}`);
  return response.data;
}
export async function createPublicChatSession(slug: string, visitorName?: string): Promise<PublicChatSession> {
  return createPublicChatSessionWithVerification({ slug, visitorName });
}
export async function createPublicChatSessionWithVerification(payload: {
  slug: string;
  visitorName?: string;
  captchaToken?: string;
}): Promise<PublicChatSession> {
  const response = await api.post<PublicChatSession>(`/api/v1/public-chat/links/${payload.slug}/sessions`, {
    visitor_name: payload.visitorName || undefined,
    captcha_token: payload.captchaToken || undefined,
  });
  return response.data;
}
export async function sendPublicChatMessage(payload: {
  sessionId: string;
  sessionToken: string;
  messageText: string;
  clientMessageId: string;
}) {
  const response = await api.post<{
    message_id: string;
    conversation_id: string;
    status: 'queued' | 'received' | 'duplicate';
    processing_status?: 'queued' | 'delayed' | 'ignored';
    processing_code?: 'queued' | 'worker_degraded' | string;
    duplicate?: boolean;
    safe_error?: {
      code?: string;
      safe_message?: string;
    } | null;
    processing_message?: string;
  }>(
    `/api/v1/public-chat/sessions/${payload.sessionId}/messages`,
    {
      session_token: payload.sessionToken,
      message_text: payload.messageText,
      client_message_id: payload.clientMessageId,
    }
  );
  return response.data;
}
export async function savePublicChatContact(payload: {
  sessionId: string;
  sessionToken: string;
  email?: string;
  phone?: string;
  name?: string;
  whatsappOptIn?: boolean;
}) {
  const response = await api.post<{ saved: boolean; email_saved: boolean; phone_saved: boolean }>(
    `/api/v1/public-chat/sessions/${payload.sessionId}/contact`,
    {
      session_token: payload.sessionToken,
      email: payload.email || undefined,
      phone: payload.phone || undefined,
      name: payload.name || undefined,
      whatsapp_opted_in: payload.whatsappOptIn ?? false,
    }
  );
  return response.data;
}
export async function sendPublicChatOtp(payload: {
  sessionId: string;
  sessionToken: string;
  phone: string;
  action: string;
}) {
  const response = await api.post<{
    sent: boolean;
    expires_in_seconds: number;
    mode: string;
    test_code?: string;
  }>(`/api/v1/public-chat/sessions/${payload.sessionId}/otp/send`, {
    session_token: payload.sessionToken,
    phone: payload.phone,
    action: payload.action,
  });
  return response.data;
}
export async function verifyPublicChatOtp(payload: {
  sessionId: string;
  sessionToken: string;
  phone: string;
  action: string;
  code: string;
}) {
  const response = await api.post<{
    verified: boolean;
    action: string;
    processing_status: 'queued';
    expires_in_seconds: number;
  }>(`/api/v1/public-chat/sessions/${payload.sessionId}/otp/verify`, {
    session_token: payload.sessionToken,
    phone: payload.phone,
    action: payload.action,
    code: payload.code,
  });
  return response.data;
}
export async function endPublicChatSession(payload: {
  sessionId: string;
  sessionToken: string;
}) {
  const response = await api.post<PublicChatSession>(
    `/api/v1/public-chat/sessions/${payload.sessionId}/end`,
    {
      session_token: payload.sessionToken,
    }
  );
  return response.data;
}
export async function getPublicChatMessages(sessionId: string, sessionToken: string): Promise<PublicChatMessage[]> {
  const response = await api.post<PublicChatMessage[]>(`/api/v1/public-chat/sessions/${sessionId}/messages/list`, {
    session_token: sessionToken,
  });
  return response.data;
}
