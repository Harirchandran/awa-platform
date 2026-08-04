import { useQuery } from '@tanstack/react-query';
import api from '@/lib/api';
export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'unhealthy';
  services: {
    redis: string;
    database: string;
    worker?: string;
    api: string;
  };
  queue_depth: number;
  processing_depth?: number;
  queue_alert: string;
  worker_last_seen_at?: string | null;
  worker_lag_seconds?: number | null;
  dlq_depth?: number;
  workers?: Record<string, {
    status: string;
    last_seen_at: string | null;
    lag_seconds: number | null;
    ttl_seconds: number;
  }>;
  events?: {
    status: string;
    reason?: string;
    counts: {
      pending: number;
      processing: number;
      processed: number;
      failed: number;
      skipped: number;
    };
    retryable_failed_count: number;
    retrying_count: number;
    oldest_pending_event_age_seconds: number;
    oldest_failed_event_age_seconds: number;
    dispatcher: {
      status: string;
      last_seen_at: string | null;
      lag_seconds: number | null;
      ttl_seconds: number;
      last_success_at: string | null;
      last_error_at: string | null;
    };
    recent_failed_events: Array<{
      event_id: string | null;
      tenant_id: string | null;
      event_type: string;
      source_domain: string;
      aggregate_type: string;
      aggregate_id: string | null;
      status: string;
      attempts: number;
      max_attempts: number;
      reason_code: string | null;
      available_at: string | null;
      updated_at: string | null;
    }>;
    recent_skipped_events: Array<Record<string, unknown>>;
  };
  providers?: Record<string, ProviderHealth>;
  checked_at?: string | null;
  version: string;
}

export interface ProviderHealth {
  mode?: string;
  status: string;
  checks?: Record<string, boolean | number>;
  routing_provider?: string;
  agent_provider?: string;
}

export interface DLQItem {
  index: number;
  raw: string;
  payload: Record<string, unknown>;
}

export interface DLQResponse {
  total: number;
  items: DLQItem[];
}

export function useSystemHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: async (): Promise<HealthStatus> => {
      const response = await api.get<HealthStatus>('/api/v1/health');
      return response.data;
    },
    refetchInterval: 30000,
    refetchOnMount: true,
  });
}
export function useOperationalHealth() {
  return useQuery({
    queryKey: ['health', 'ops'],
    queryFn: async (): Promise<HealthStatus> => {
      const response = await api.get<HealthStatus>('/api/v1/health/ops');
      return response.data;
    },
    refetchInterval: 30000,
    refetchOnMount: true,
  });
}

/** Fetch the top 50 items in the DLQ. */
export async function fetchDLQItems(): Promise<DLQResponse> {
  const response = await api.get<DLQResponse>('/api/v1/health/queues/dlq');
  return response.data;
}

/** Retry all DLQ items (or a single item by passing its raw string). */
export async function retryDLQ(target: string = 'all'): Promise<{ retried: number }> {
  const response = await api.post<{ retried: number }>('/api/v1/health/queues/dlq/retry', { target });
  return response.data;
}

/** Permanently delete all items in the DLQ. */
export async function purgeDLQ(): Promise<{ purged: number }> {
  const response = await api.delete<{ purged: number }>('/api/v1/health/queues/dlq/purge');
  return response.data;
}

/** Send a pause or resume signal to all background workers. */
export async function signalWorkers(action: 'pause' | 'resume'): Promise<{ paused: boolean }> {
  const response = await api.post<{ paused: boolean }>('/api/v1/health/workers/signal', { action });
  return response.data;
}
