import { useQuery } from '@tanstack/react-query';
import api from '@/lib/api';
export interface DailyCostRow {
  date: string;
  cost_usd: number;
  total_tokens: number;
  message_count: number;
  tenant_id: string;
}
export interface CostSummary {
  items: DailyCostRow[];
  total_spend: number;
  total_tokens: number;
  total_messages: number;
}
export function useCostMetrics() {
  return useQuery({
    queryKey: ['costs', 'metrics'],
    queryFn: async (): Promise<CostSummary> => {
      const response = await api.get<CostSummary>('/api/v1/costs');
      return response.data;
    },
    // Refresh costs every 5 minutes automatically
    refetchInterval: 1000 * 60 * 5,
  });
}