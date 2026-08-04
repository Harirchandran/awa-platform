import type { TenantToolsResponse } from '@/features/tools/api';

export function getModuleEntitlement(toolsData: TenantToolsResponse | undefined, moduleKey: string) {
  const tool = toolsData?.tools.find((item) => item.key === moduleKey);
  const allowedByPlan = tool?.allowed_by_plan ?? tool?.plan_allowed ?? true;
  const enabledByTenant = tool?.enabled_by_tenant ?? tool?.enabled ?? false;
  return {
    tool,
    enabledByTenant,
    allowedByPlan,
    runtimeAvailable: tool?.runtime_available ?? Boolean(enabledByTenant && allowedByPlan),
    lockedByPlan: tool?.locked_by_plan ?? !allowedByPlan,
    canConfigure: tool?.can_configure ?? tool?.can_edit_config ?? allowedByPlan,
    canEnable: tool?.can_enable_module ?? allowedByPlan,
    canMutateRuntimeData: tool?.can_mutate_runtime_data ?? Boolean(enabledByTenant && allowedByPlan),
    lockedReason: tool?.locked_reason || 'This workflow is not available on your current plan.',
  };
}
