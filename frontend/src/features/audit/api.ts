import api from '@/lib/api';

export interface AuditLogEntry {
  id: string;
  actor_id: string;
  actor_email: string | null;
  tenant_name: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  details: Record<string, unknown>;
  ip_address: string | null;
  created_at: string;
}

export interface AuditLogResponse {
  items: AuditLogEntry[];
  next_cursor: string | null;
}

export interface AuditLogFilters {
  action_type?: string;
  date_from?: string;
  date_to?: string;
  tenant_id?: string;
  isGlobal?: boolean;
}

export async function fetchAuditLogs(
  filters: AuditLogFilters,
  cursor?: string | null,
): Promise<AuditLogResponse> {
  const params = new URLSearchParams();
  params.set('limit', '50');
  if (filters.action_type) params.set('action_type', filters.action_type);
  if (filters.date_from) params.set('date_from', filters.date_from);
  if (filters.date_to) params.set('date_to', filters.date_to);
  if (filters.tenant_id) params.set('tenant_id', filters.tenant_id);
  if (cursor) params.set('cursor', cursor);
  const url = filters.isGlobal ? `/api/v1/admin/audit-logs` : `/api/v1/audit-logs`;
  const response = await api.get<AuditLogEntry[]>(`${url}?${params.toString()}`);
  return { items: response.data || [], next_cursor: response.meta?.next_cursor || null };
}
