import { useQuery } from '@tanstack/react-query';
import api from '@/lib/api';
export interface BillingPlan {
  id: string;
  plan_code?: string;
  name: string;
  monthly_price_cents: number;
  currency: string;
  razorpay_plan_id: string;
  features_json: string[] | string | null;
  limits_json?: Record<string, unknown>;
}
export interface TenantBillingStatus {
  status: 'trial' | 'active' | 'suspended' | 'expired';
  plan_code?: string;
  trial_ends_at?: string;
}
export interface BillingStatusResponse {
  tenant_billing: TenantBillingStatus;
  available_plans: BillingPlan[];
  current_plan?: BillingPlan | null;
  billing_mode?: 'mock' | 'test' | 'live';
  mock_billing?: boolean;
  plan_policy?: {
    plan_code: string;
    monthly_messages?: number | null;
    daily_messages?: number | null;
    monthly_ai_tokens?: number | null;
    monthly_credits?: number | null;
    allowed_ai_modes: string[];
    allowed_modules: string[];
    ai_capability_profiles?: Array<{
      code: string;
      label: string;
      runtime_engine: string;
      summary: string;
      ui_description: string;
      allowed: boolean;
      locked_reason?: string | null;
    }>;
  };
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
  usage?: {
    today_message_count?: number;
    month_message_count?: number;
    month_ai_tokens_used?: number;
    month_credits_used?: number;
    month_tool_action_count?: number;
    month_cost?: number;
  };
}
export async function fetchBillingStatus() {
  const res = await api.get<BillingStatusResponse>('/api/v1/billing/status');
  return res.data;
}
export function useBillingStatus() {
  return useQuery<BillingStatusResponse>({
    queryKey: ['billing_status'],
    queryFn: fetchBillingStatus,
    refetchInterval: 60000,
    refetchOnMount: true,
  });
}