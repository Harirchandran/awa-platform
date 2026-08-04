import { useQuery } from '@tanstack/react-query';
import api from '@/lib/api';
import { useTenantStore } from '@/stores/tenant.store';
export interface WhatsAppAccount {
  id: string;
  tenant_id: string;
  phone_number: string;
  phone_number_id: string;
  waba_id?: string | null;
  business_id?: string | null;
  display_name: string;
  verification_status?: string;
  onboarding_mode?: string;
  status: 'connected' | 'disconnected';
  last_message_at: string | null;
  connected_at?: string | null;
}
export function useWhatsAppAccounts() {
  const tenantId = useTenantStore((s) => s.activeTenantId);
  return useQuery({
    queryKey: ['whatsapp', tenantId],
    queryFn: async (): Promise<WhatsAppAccount[]> => {
      const response = await api.get<WhatsAppAccount[]>('/api/v1/whatsapp-accounts');
      return response.data;
    },
    enabled: !!tenantId,
    refetchInterval: 30000,
  });
}