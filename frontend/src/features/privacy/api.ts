import { useMutation } from '@tanstack/react-query';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import toast from 'react-hot-toast';

export interface ScheduleErasureResult {
  action: string;
  customer_id: string;
  erasure_scheduled_at: string;
  compliance: string[];
  note?: string;
}

export interface CancelErasureResult {
  action: string;
  customer_id: string;
  status: string;
  compliance: string[];
}

export interface ExportResult {
  action: string;
  compliance: string[];
  export: Record<string, unknown>;
}

export interface CustomerStatusResult {
  id: string;
  status: string;
  erasure_scheduled_at: string | null;
}

/** Fetch a customer's current status (to detect pending_erasure). */
export async function fetchCustomerStatus(customerId: string): Promise<CustomerStatusResult> {
  const response = await api.get<CustomerStatusResult>(`/api/v1/privacy/status/${customerId}`);
  return response.data;
}

/** Schedule a 72-hour tombstone erasure (replaces hard-delete). */
export function useScheduleErasure() {
  return useMutation({
    mutationFn: async (customerId: string): Promise<ScheduleErasureResult> => {
      const response = await api.delete<ScheduleErasureResult>(`/api/v1/privacy/erase/${customerId}`);
      return response.data;
    },
    onSuccess: (data) => {
      const at = data.erasure_scheduled_at
        ? new Date(data.erasure_scheduled_at).toLocaleString()
        : 'unknown time';
      toast.success(`Erasure scheduled. Hard-delete will execute at ${at}.`);
    },
    onError: (error: unknown) => {
      toast.error(`Erasure scheduling failed: ${getApiErrorMessage(error)}`);
    },
  });
}

/** Cancel a pending erasure tombstone. */
export function useCancelErasure() {
  return useMutation({
    mutationFn: async (customerId: string): Promise<CancelErasureResult> => {
      const response = await api.post<CancelErasureResult>(`/api/v1/privacy/erase/${customerId}/cancel`);
      return response.data;
    },
    onSuccess: () => {
      toast.success('Erasure cancelled. Customer has been restored to active status.');
    },
    onError: (error: unknown) => {
      toast.error(`Cancel failed: ${getApiErrorMessage(error)}`);
    },
  });
}

export function useExportCustomer() {
  return useMutation({
    mutationFn: async (customerId: string): Promise<ExportResult> => {
      const response = await api.get<ExportResult>(`/api/v1/privacy/export/${customerId}`);
      return response.data;
    },
    onSuccess: () => {
      toast.success('Customer data exported successfully.');
    },
    onError: (error: unknown) => {
      toast.error(`Export failed: ${getApiErrorMessage(error)}`);
    },
  });
}