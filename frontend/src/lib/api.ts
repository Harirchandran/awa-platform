import axios from 'axios';
import type { AxiosRequestConfig } from 'axios';
import { authClient } from './auth';
import type { ApiResponse } from '@/types/api.types';
import { clearDevMockToken, getDevMockToken } from '@/lib/dev-auth';
import { isAccountSuspendedMarked, markAccountSuspended, redirectToSuspendedAccount } from '@/lib/account-status';
import { isSuspendedTenantError } from '@/lib/api-errors';

const client = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  headers: {
    'Content-Type': 'application/json'
  }
});

client.interceptors.request.use(async (config) => {
  const mockToken = getDevMockToken();
  if (mockToken) {
    config.headers.Authorization = `Bearer ${mockToken}`;
    return config;
  }
  let accessToken = authClient.getAccessToken();
  if (accessToken) {
    if (authClient.isTokenExpired(accessToken)) {
      // Proactively refresh the token
      const newAccessToken = await authClient.refresh();
      if (newAccessToken) {
        accessToken = newAccessToken;
      }
    }
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 403 && isSuspendedTenantError(error)) {
      clearDevMockToken();
      localStorage.removeItem('awa_onboarding');
      markAccountSuspended();
      authClient.clearSession();
      authClient.notify('SIGNED_OUT', null);
      redirectToSuspendedAccount();
      return Promise.reject(error);
    }
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;
      const newAccessToken = await authClient.refresh();
      if (newAccessToken) {
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return client(originalRequest);
      }
      if (isAccountSuspendedMarked()) {
        clearDevMockToken();
        localStorage.removeItem('awa_onboarding');
        authClient.clearSession();
        authClient.notify('SIGNED_OUT', null);
        redirectToSuspendedAccount();
        return Promise.reject(error);
      }
      clearDevMockToken();
      localStorage.removeItem('awa_onboarding');
      authClient.clearSession();
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

async function request<T>(config: AxiosRequestConfig): Promise<ApiResponse<T>> {
  const response = await client.request<ApiResponse<T>>(config);
  return response.data;
}

const api = {
  get<T>(url: string, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'GET', url });
  },
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'POST', url, data });
  },
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'PUT', url, data });
  },
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'PATCH', url, data });
  },
  delete<T>(url: string, config?: AxiosRequestConfig) {
    return request<T>({ ...config, method: 'DELETE', url });
  },
};

export default api;
