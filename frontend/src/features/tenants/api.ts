import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import toast from 'react-hot-toast';
export interface TenantRecord {
  id: string;
  name: string;
  slug: string;
  status: 'active' | 'suspended';
  plan_code: string;
  created_at: string;
}
export interface CreateTenantPayload {
  name: string;
  slug?: string;
  plan_code: string;
  business_type?: string;
  tenant_admin_email?: string;
  tenant_admin_password?: string;
  tenant_admin_name?: string;
  whatsapp_phone_number_id?: string;
  whatsapp_phone_number?: string;
  whatsapp_display_name?: string;
}
export interface TenantAdminAccess {
  status: 'created' | 'not_configured';
  email?: string | null;
  user_id?: string | null;
  role?: string | null;
  requires_login?: boolean;
  dev_session_token?: string;
  message: string;
}
export interface CreateTenantResult extends TenantRecord {
  business_type?: string;
  admin_access: TenantAdminAccess;
  next_steps: string[];
}
export interface ToolState {
  key: string;
  label: string;
  category: string;
  description: string;
  enabled: boolean;
  enabled_by_tenant?: boolean;
  plan_allowed: boolean;
  allowed_by_plan?: boolean;
  runtime_available?: boolean;
  locked_by_plan?: boolean;
  can_view_config?: boolean;
  can_edit_config?: boolean;
  can_configure?: boolean;
  can_enable_module?: boolean;
  can_mutate_runtime_data?: boolean;
  display_reason?: 'upgrade_required' | 'disabled_by_admin' | null;
  field_count: number;
  locked_reason?: string | null;
}
export interface ToolPreset {
  label: string;
  business_type: string;
  enabled_modules: string[];
  note: string;
}
export interface ToolPlanPolicy {
  plan_code: string;
  allowed_ai_modes: string[];
  allowed_modules: string[];
}
export interface ToolControlTenantSummary extends TenantRecord {
  business_type: string;
  config_version?: number | null;
  active_modules: string[];
  plan_policy: ToolPlanPolicy;
}
export interface ToolControlsListResponse {
  tenants: ToolControlTenantSummary[];
  presets: Record<string, ToolPreset>;
}
export interface TenantToolControls {
  tenant: TenantRecord;
  config_version?: number | null;
  business_type: string;
  tools: ToolState[];
  active_modules: string[];
  presets: Record<string, ToolPreset>;
  plan_policy: ToolPlanPolicy;
}
export function useTenantsList() {
  return useQuery({
    queryKey: ['tenants'],
    queryFn: async (): Promise<TenantRecord[]> => {
      const response = await api.get<TenantRecord[]>('/api/v1/admin/tenants');
      return response.data;
    },
  });
}
export function useCreateTenant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateTenantPayload) => {
      const response = await api.post<CreateTenantResult>('/api/v1/admin/tenants', payload);
      return response.data;
    },
    onSuccess: (data) => {
      toast.success(`Business "${data.name}" created successfully!`);
      queryClient.invalidateQueries({ queryKey: ['tenants'] });
    },
    onError: (error: unknown) => {
      toast.error(`Failed to create business: ${getApiErrorMessage(error)}`);
    },
  });
}
export function useAdminToolControlsList() {
  return useQuery({
    queryKey: ['admin_tool_controls'],
    queryFn: async (): Promise<ToolControlsListResponse> => {
      const response = await api.get<ToolControlsListResponse>('/api/v1/admin/tool-controls');
      return response.data;
    },
  });
}
export function useTenantToolControls(tenantId?: string) {
  return useQuery({
    queryKey: ['admin_tool_controls', tenantId],
    queryFn: async (): Promise<TenantToolControls> => {
      const response = await api.get<TenantToolControls>(`/api/v1/admin/tenants/${tenantId}/tool-controls`);
      return response.data;
    },
    enabled: !!tenantId,
  });
}
export function useUpdateTenantToolControls() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      tenantId,
      enabled_modules,
      business_type,
      reason,
    }: {
      tenantId: string;
      enabled_modules: string[];
      business_type?: string;
      reason?: string;
    }) => {
      const response = await api.put<TenantToolControls>(`/api/v1/admin/tenants/${tenantId}/tool-controls`, {
        enabled_modules,
        business_type,
        reason: reason || 'super_admin_tool_control_update',
      });
      return response.data;
    },
    onSuccess: (data, variables) => {
      const requested = [...variables.enabled_modules].sort().join('|');
      const saved = [...data.active_modules].sort().join('|');
      if (requested === saved) {
        toast.success(`Tools updated for ${data.tenant.name}`);
      } else {
        toast.error(`Tool save response for ${data.tenant.name} did not match the requested modules. Please refresh and review.`);
      }
      queryClient.invalidateQueries({ queryKey: ['admin_tool_controls'] });
      queryClient.invalidateQueries({ queryKey: ['tenants'] });
      queryClient.invalidateQueries({ queryKey: ['config'] });
      queryClient.invalidateQueries({ queryKey: ['tenant_tools'] });
    },
    onError: (error: unknown) => {
      toast.error(`Failed to update tools: ${getApiErrorMessage(error)}`);
    },
  });
}

export interface BusinessType {
  code: string;
  label: string;
  enabled_modules: string[];
}

export function useAdminBusinessTypes() {
  return useQuery({
    queryKey: ['admin_business_types'],
    queryFn: async (): Promise<BusinessType[]> => {
      const response = await api.get<BusinessType[]>('/api/v1/admin/business-types');
      return response.data;
    },
  });
}

/**
 * Requests a short-lived impersonation JWT from the server.
 * The caller is responsible for storing the token and redirecting.
 * Returns only the access_token — no refresh token is issued server-side.
 */
export async function impersonateTenant(tenantId: string): Promise<string> {
  const response = await api.post<{ access_token: string }>(
    `/api/v1/admin/impersonate/${tenantId}`,
  );
  const token = response.data?.access_token;
  if (!token) throw new Error('Impersonation failed: no token returned');
  return token;
}

/**
 * Toggles a tenant's status between 'active' and 'suspended'.
 * Uses the existing PATCH /api/v1/admin/billing/tenants/{id} endpoint.
 */
export async function toggleTenantStatus(
  tenantId: string,
  currentStatus: TenantRecord['status'],
): Promise<void> {
  const nextStatus = currentStatus === 'active' ? 'suspended' : 'active';
  await api.patch(`/api/v1/admin/billing/tenants/${tenantId}`, { status: nextStatus });
}

// ---------------------------------------------------------------------------
// Platform Metrics (C-Suite Telemetry)
// ---------------------------------------------------------------------------

export interface PlatformMetrics {
  active_tenants: number;
  mtd_messages:   number;
  mtd_cost_usd:   number;
}

export function usePlatformMetrics() {
  return useQuery({
    queryKey: ['platform-metrics'],
    queryFn: async (): Promise<PlatformMetrics> => {
      const response = await api.get<PlatformMetrics>('/api/v1/admin/metrics');
      return response.data;
    },
    refetchInterval: 60_000,   // refresh every minute
    refetchOnMount: true,
  });
}
