import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import api from '@/lib/api';

export type LeadStatus = 'new' | 'contacted' | 'converted' | 'lost';

export interface Lead {
  id: string;
  tenant_id: string;
  customer_id: string | null;
  conversation_id: string | null;
  source_channel: string | null;
  name: string | null;
  phone: string | null;
  email: string | null;
  interest: string | null;
  status: LeadStatus;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  customer_name?: string | null;
  customer_phone?: string | null;
}

interface LeadsResponse {
  items: Lead[];
  meta: {
    next_cursor?: string | null;
  };
}

type LeadsQueryKey = ['leads', { status: string | undefined; search: string | undefined }];

export function useLeadsList(status?: string, search?: string) {
  return useInfiniteQuery<LeadsResponse, Error, InfiniteData<LeadsResponse>, LeadsQueryKey, string | null>({
    queryKey: ['leads', { status, search }],
    queryFn: async ({ pageParam }): Promise<LeadsResponse> => {
      const params = new URLSearchParams();
      if (pageParam) params.append('cursor', pageParam);
      if (status && status !== 'all') params.append('status', status);
      if (search) params.append('search', search);
      params.append('limit', '50');
      const response = await api.get<Lead[]>(`/api/v1/leads?${params.toString()}`);
      return {
        items: response.data,
        meta: { next_cursor: response.meta?.next_cursor },
      };
    },
    getNextPageParam: (lastPage) => lastPage.meta?.next_cursor ?? null,
    initialPageParam: null,
  });
}

export function useUpdateLeadStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, status }: { id: string; status: string }) => {
      const response = await api.patch<{ updated: boolean; lead_id: string; status: string }>(
        `/api/v1/leads/${id}`,
        { status }
      );
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['leads'] });
    },
  });
}
