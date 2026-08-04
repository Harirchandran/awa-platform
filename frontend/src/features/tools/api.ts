import { useQuery } from '@tanstack/react-query';
import api from '@/lib/api';
import { useTenantStore } from '@/stores/tenant.store';
import type { ToolPlanPolicy, ToolState } from '@/features/tenants/api';
export interface TenantToolState extends ToolState {
  icon_key?: string | null;
  route_path?: string | null;
  actions?: Array<{ label: string; href: string }>;
  sort_order?: number;
}
export interface TenantToolsResponse {
  tenant: {
    id: string;
    name: string;
    slug: string;
    status: string;
    plan_code: string;
  };
  config_version?: number | null;
  business_type: string;
  tools: TenantToolState[];
  active_modules: string[];
  plan_policy: ToolPlanPolicy;
  entitlements?: {
    modules: Record<string, {
      module: string;
      enabled_by_tenant: boolean;
      enabled: boolean;
      allowed_by_plan: boolean;
      plan_allowed: boolean;
      runtime_available: boolean;
      locked_by_plan: boolean;
      locked: boolean;
      can_view_config: boolean;
      can_edit_config: boolean;
      can_configure: boolean;
      can_enable_module: boolean;
      can_mutate_runtime_data: boolean;
      display_reason?: 'upgrade_required' | 'disabled_by_admin' | null;
      locked_reason?: string | null;
    }>;
  };
}
export function useTenantTools() {
  const activeTenantId = useTenantStore(state => state.activeTenantId);
  return useQuery({
    queryKey: ['tenant_tools', activeTenantId],
    queryFn: async (): Promise<TenantToolsResponse> => {
      const response = await api.get<TenantToolsResponse>('/api/v1/tools');
      return response.data;
    },
    enabled: !!activeTenantId,
  });
}
