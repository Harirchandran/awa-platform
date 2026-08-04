import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { useTenantStore } from '@/stores/tenant.store';
import toast from 'react-hot-toast';
export interface FeatureFlag {
  key: string;
  description: string;
  enabled: boolean;
  updated_at: string;
}
export function useFeatureFlags() {
  const tenantId = useTenantStore((s) => s.activeTenantId);
  return useQuery({
    queryKey: ['flags', tenantId],
    queryFn: async (): Promise<FeatureFlag[]> => {
      const response = await api.get<FeatureFlag[]>('/api/v1/feature-flags');
      return response.data;
    },
    enabled: !!tenantId,
  });
}
export function useToggleFlag() {
  const queryClient = useQueryClient();
  const tenantId = useTenantStore((s) => s.activeTenantId);
  return useMutation({
    mutationFn: async ({ key, enabled }: { key: string; enabled: boolean }) => {
      const response = await api.put(`/api/v1/feature-flags/${key}`, { enabled });
      return response.data;
    },
    onMutate: async ({ key, enabled }) => {
      await queryClient.cancelQueries({ queryKey: ['flags', tenantId] });
      const previousFlags = queryClient.getQueryData<FeatureFlag[]>(['flags', tenantId]);
      if (previousFlags) {
        queryClient.setQueryData<FeatureFlag[]>(['flags', tenantId], old =>
          old?.map(flag => flag.key === key ? { ...flag, enabled } : flag)
        );
      }
      return { previousFlags };
    },
    onError: (err, _variables, context) => {
      if (context?.previousFlags) {
        queryClient.setQueryData(['flags', tenantId], context.previousFlags);
      }
      toast.error(`Failed to toggle flag: ${getApiErrorMessage(err)}`);
    },
    onSuccess: (_, variables) => {
      toast.success(`${variables.key} mapped to ${variables.enabled ? 'ON' : 'OFF'}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['flags', tenantId] });
    },
  });
}