import { create } from 'zustand';
import type { CanonicalAiMode } from '@/lib/ai-mode';
interface TenantStore {
  activeTenantId: string | null;
  activeTenantMode: CanonicalAiMode | null;
  activeTenantName: string | null;
  activeTenantPlan: string | null;
  setActiveTenant: (tenant: { id: string; mode: CanonicalAiMode; name: string; plan: string }) => void;
  clearTenant: () => void;
}
export const useTenantStore = create<TenantStore>((set) => ({
  activeTenantId: null,
  activeTenantMode: null,
  activeTenantName: null,
  activeTenantPlan: null,
  setActiveTenant: (tenant) => set({
    activeTenantId: tenant.id,
    activeTenantMode: tenant.mode,
    activeTenantName: tenant.name,
    activeTenantPlan: tenant.plan
  }),
  clearTenant: () => set({
    activeTenantId: null,
    activeTenantMode: null,
    activeTenantName: null,
    activeTenantPlan: null
  }),
}));
