import axios from 'axios';
import { getDevMockToken } from './dev-auth';
import { markAccountSuspended } from './account-status';
import { isSuspendedTenantError } from './api-errors';

const ACCESS_TOKEN_KEY = 'awa_access_token';
const REFRESH_TOKEN_KEY = 'awa_refresh_token';

// Simple JWT parser for client-side expiration checks
export function parseJwt(token: string) {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      window.atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(jsonPayload);
  } catch {
    return null;
  }
}

export interface Session {
  access_token: string;
  refresh_token: string;
  expires_at?: number;
  user: {
    id: string;
    email: string;
  };
}

type AuthChangeListener = (event: string, session: Session | null) => void;
const listeners = new Set<AuthChangeListener>();
let refreshPromise: Promise<string | null> | null = null;
const apiBaseUrl = import.meta.env.VITE_API_URL || '';

export const authClient = {
  onAuthStateChange(listener: AuthChangeListener) {
    listeners.add(listener);
    const session = this.getSession();
    listener(session ? 'INITIAL_SESSION' : 'SIGNED_OUT', session);
    return {
      data: {
        subscription: {
          unsubscribe() {
            listeners.delete(listener);
          }
        }
      }
    };
  },
  notify(event: string, session: Session | null) {
    listeners.forEach((listener) => {
      try {
        listener(event, session);
      } catch (err) {
        console.error('Auth change listener error:', err);
      }
    });
  },
  getAccessToken(): string | null {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
  },
  getRefreshToken(): string | null {
    return localStorage.getItem(REFRESH_TOKEN_KEY);
  },
  setSession(accessToken: string, refreshToken: string) {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  },
  clearSession() {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  },
  getSession(): Session | null {
    // If mock auth is active, generate a mock session
    const mockToken = getDevMockToken();
    if (mockToken) {
      const [, , userId] = mockToken.split(':');
      return {
        access_token: mockToken,
        refresh_token: mockToken,
        expires_at: Math.floor(Date.now() / 1000) + 999999,
        user: {
          id: userId || '00000000-0000-0000-0000-000000000100',
          email: 'mock@example.com'
        }
      };
    }
    const accessToken = this.getAccessToken();
    const refreshToken = this.getRefreshToken();
    if (!accessToken || !refreshToken) {
      return null;
    }
    const payload = parseJwt(accessToken);
    if (!payload) {
      return null;
    }
    return {
      access_token: accessToken,
      refresh_token: refreshToken,
      expires_at: payload.exp,
      user: {
        id: payload.sub || '',
        email: payload.email || ''
      }
    };
  },
  isTokenExpired(token: string): boolean {
    const payload = parseJwt(token);
    if (!payload || !payload.exp) {
      return true;
    }
    // Expired or within 30 seconds of expiry
    return payload.exp * 1000 - 30000 < Date.now();
  },
  async login(email: string, password: string): Promise<Session> {
    const response = await axios.post(`${apiBaseUrl}/api/v1/auth/login`, {
      email,
      password,
    });
    const { access_token, refresh_token, user_id } = response.data.data;
    if (!access_token || !refresh_token) {
      throw new Error('Authentication succeeded but tokens were not returned');
    }
    this.setSession(access_token, refresh_token);
    const session = this.getSession();
    this.notify('SIGNED_IN', session);
    return session || {
      access_token,
      refresh_token,
      user: { id: user_id, email }
    };
  },
  async register(payload: {
    business_name: string;
    email: string;
    password: string;
    business_type: string;
  }) {
    const response = await axios.post(`${apiBaseUrl}/api/v1/auth/register`, payload);
    const result = response.data.data;
    // In postgres auth mode, the register endpoint returns access_token and refresh_token directly
    if (result.access_token && result.refresh_token) {
      this.setSession(result.access_token, result.refresh_token);
      const session = this.getSession();
      this.notify('SIGNED_IN', session);
    }
    return response.data;
  },
  async logout(): Promise<void> {
    const refreshToken = this.getRefreshToken();
    this.clearSession();
    this.notify('SIGNED_OUT', null);
    if (refreshToken) {
      try {
        await axios.post(`${apiBaseUrl}/api/v1/auth/logout`, {
          refresh_token: refreshToken,
        });
      } catch {
        // Silently fail logout API call if session is already invalid
      }
    }
  },
  async refresh(): Promise<string | null> {
    // If a refresh is already in progress, return the existing promise
    if (refreshPromise) {
      return refreshPromise;
    }
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) {
      this.clearSession();
      this.notify('SIGNED_OUT', null);
      return null;
    }
    refreshPromise = (async () => {
      try {
        const response = await axios.post(`${apiBaseUrl}/api/v1/auth/refresh`, {
          refresh_token: refreshToken,
        });
        const { access_token, refresh_token: newRefreshToken } = response.data.data;
        if (access_token && newRefreshToken) {
          this.setSession(access_token, newRefreshToken);
          const session = this.getSession();
          this.notify('TOKEN_REFRESHED', session);
          return access_token;
        }
        this.clearSession();
        this.notify('SIGNED_OUT', null);
        return null;
      } catch (error: unknown) {
        if (isSuspendedTenantError(error)) {
          markAccountSuspended();
        }
        this.clearSession();
        this.notify('SIGNED_OUT', null);
        return null;
      } finally {
        refreshPromise = null;
      }
    })();
    return refreshPromise;
  },
  isAuthenticated(): boolean {
    const session = this.getSession();
    if (!session) {
      return false;
    }
    // Note: session is valid if we have tokens; axios interceptor will refresh if access token is expired
    return !!session.refresh_token;
  }
};
