import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import type { ReceptionistBehaviorResponse, ReceptionistBehaviorUpdatePayload, TenantConfig } from '@/types/config.types';
import { useTenantStore } from '@/stores/tenant.store';
interface TenantConfigRecord {
  config_json?: TenantConfig | string;
  version?: number;
  plan_policy?: {
    plan_code: string;
    allowed_ai_modes?: string[];
    allowed_modules?: string[];
    ai_capability_profiles?: Array<{
      code: string;
      label: string;
      runtime_engine: string;
      summary: string;
      ui_description: string;
      allowed: boolean;
      locked_reason?: string | null;
    }>;
    ai_capability_policy?: {
      requested_mode: string;
      effective_mode: string;
      runtime_engine: string;
      label: string;
      summary: string;
      ui_description: string;
      downgraded: boolean;
      locked_reason?: string | null;
    };
  };
}
type TenantConfigWithVersion = TenantConfig & {
  __configVersion?: number;
  __planPolicy?: TenantConfigRecord['plan_policy'];
};
function attachConfigMetadata(
  config: TenantConfig,
  version?: number,
  planPolicy?: TenantConfigRecord['plan_policy'],
): TenantConfigWithVersion {
  const configWithVersion = config as TenantConfigWithVersion;
  Object.defineProperty(configWithVersion, '__configVersion', {
    configurable: true,
    enumerable: false,
    value: version,
    writable: true,
  });
  Object.defineProperty(configWithVersion, '__planPolicy', {
    configurable: true,
    enumerable: false,
    value: planPolicy,
    writable: true,
  });
  return configWithVersion;
}
export function useTenantConfig() {
  const activeTenantId = useTenantStore(state => state.activeTenantId);
  return useQuery({
    queryKey: ['config', activeTenantId],
    queryFn: async () => {
      const response = await api.get<TenantConfigRecord | TenantConfig>('/api/v1/config');
      const rawConfig =
        typeof response.data === 'object' &&
        response.data !== null &&
        'config_json' in response.data
          ? response.data.config_json
          : response.data;
      const version =
        typeof response.data === 'object' &&
        response.data !== null &&
        'config_json' in response.data
          ? response.data.version
          : undefined;
      const planPolicy =
        typeof response.data === 'object' &&
        response.data !== null &&
        'config_json' in response.data
          ? response.data.plan_policy
          : undefined;
      const parsedConfig = (typeof rawConfig === 'string' ? JSON.parse(rawConfig) : rawConfig) as TenantConfig;
      return attachConfigMetadata(parsedConfig, version, planPolicy);
    },
    enabled: !!activeTenantId,
    // Do not refetch on window focus because they might be in the middle of editing
    refetchOnWindowFocus: false,
  });
}
export function useUpdateConfig() {
  const queryClient = useQueryClient();
  const activeTenantId = useTenantStore(state => state.activeTenantId);
  return useMutation({
    mutationFn: async (updatedConfig: TenantConfig) => {
      const currentConfig = queryClient.getQueryData<TenantConfigWithVersion>(['config', activeTenantId]);
      // Backend expects the raw config object inside the payload
      const response = await api.put<{ activated: string; version: number }>('/api/v1/config', {
        config_json: updatedConfig,
        expected_version: currentConfig?.__configVersion,
      });
      return response.data;
    },
    onSuccess: (data, updatedConfig) => {
      const currentConfig = queryClient.getQueryData<TenantConfigWithVersion>(['config', activeTenantId]);
      queryClient.setQueryData(
        ['config', activeTenantId],
        attachConfigMetadata({ ...updatedConfig }, data.version, currentConfig?.__planPolicy),
      );
      queryClient.invalidateQueries({ queryKey: ['config', activeTenantId] });
      queryClient.invalidateQueries({ queryKey: ['tenant_tools', activeTenantId] });
    },
  });
}

export function useReceptionistBehavior() {
  const activeTenantId = useTenantStore(state => state.activeTenantId);
  return useQuery({
    queryKey: ['receptionist_behavior', activeTenantId],
    queryFn: async () => {
      const response = await api.get<ReceptionistBehaviorResponse>('/api/v1/config/receptionist-behavior');
      return response.data;
    },
    enabled: !!activeTenantId,
    refetchOnWindowFocus: false,
  });
}

export function useUpdateReceptionistBehavior() {
  const queryClient = useQueryClient();
  const activeTenantId = useTenantStore(state => state.activeTenantId);
  return useMutation({
    mutationFn: async (payload: ReceptionistBehaviorUpdatePayload) => {
      const response = await api.patch<ReceptionistBehaviorResponse>('/api/v1/config/receptionist-behavior', payload);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['receptionist_behavior', activeTenantId] });
      queryClient.invalidateQueries({ queryKey: ['config', activeTenantId] });
    },
  });
}

export function useResetReceptionistBehavior() {
  const queryClient = useQueryClient();
  const activeTenantId = useTenantStore(state => state.activeTenantId);
  return useMutation({
    mutationFn: async (expectedConfigVersion: number) => {
      const response = await api.post<ReceptionistBehaviorResponse>('/api/v1/config/receptionist-behavior/reset', {
        expected_config_version: expectedConfigVersion,
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['receptionist_behavior', activeTenantId] });
      queryClient.invalidateQueries({ queryKey: ['config', activeTenantId] });
    },
  });
}
