import { useEffect, useState } from 'react';
import type { Session } from '@/lib/auth';
import { authClient } from '@/lib/auth';
import api from '@/lib/api';
import { useTenantStore } from '@/stores/tenant.store';
import { normalizeAiMode } from '@/lib/ai-mode';
import type { UserRole } from './roles';

interface TenantSummary {
  id: string;
  name: string;
  ai_mode?: 'standard' | 'advanced' | 'concierge';
  plan_code?: string;
}

export interface CurrentUser {
  id: string;
  email: string;
  role: UserRole;
  tenant_id: string | null;
  name: string;
  created_at: string;
}

export function useSession() {
  const [session, setSession] = useState<Session | null>(() => authClient.getSession());
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const {
      data: { subscription },
    } = authClient.onAuthStateChange((_event, session) => {
      setSession(session);
      setIsLoading(false);
    });
    return () => {
      subscription?.unsubscribe();
    };
  }, []);

  return { session, isLoading };
}

export function useCurrentUser() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const setActiveTenant = useTenantStore((state) => state.setActiveTenant);

  useEffect(() => {
    async function fetchUser() {
      try {
        const response = await api.get<CurrentUser>('/api/v1/me');
        const tenantResponse = await api.get<TenantSummary>('/api/v1/tenant');
        setActiveTenant({
          id: tenantResponse.data.id,
          mode: normalizeAiMode(tenantResponse.data.ai_mode),
          name: tenantResponse.data.name,
          plan: tenantResponse.data.plan_code || 'trial',
        });
        setUser(response.data);
      } catch {
        setUser(null);
      } finally {
        setIsLoading(false);
      }
    }
    fetchUser();
  }, [setActiveTenant]);

  return { user, isLoading };
}
