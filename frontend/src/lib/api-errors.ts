import type { AxiosError } from 'axios';
import type { ApiError, ApiResponse } from '@/types/api.types';
type ApiFailure = Pick<ApiResponse<unknown>, 'error'> & {
  detail?: string;
};
export function getApiErrorMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (!error || typeof error !== 'object') {
    return fallback;
  }
  const axiosError = error as AxiosError<ApiFailure>;
  const apiError = axiosError.response?.data?.error as ApiError | undefined;
  if (apiError?.message) {
    return apiError.message;
  }
  if (typeof axiosError.response?.data?.detail === 'string') {
    return axiosError.response.data.detail;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export function isSuspendedTenantError(error: unknown): boolean {
  return getApiErrorMessage(error, '').toLowerCase() === 'tenant is suspended or inactive';
}
