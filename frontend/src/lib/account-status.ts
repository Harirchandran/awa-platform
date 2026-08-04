export const SUSPENDED_ACCOUNT_STORAGE_KEY = 'awa_account_suspended';
export const SUSPENDED_TENANT_MESSAGE = 'Tenant is suspended or inactive';

export function markAccountSuspended() {
  localStorage.setItem(SUSPENDED_ACCOUNT_STORAGE_KEY, 'true');
}

export function clearAccountSuspendedMarker() {
  localStorage.removeItem(SUSPENDED_ACCOUNT_STORAGE_KEY);
}

export function isAccountSuspendedMarked() {
  return localStorage.getItem(SUSPENDED_ACCOUNT_STORAGE_KEY) === 'true';
}

export function redirectToSuspendedAccount() {
  if (window.location.pathname !== '/account-suspended') {
    window.location.href = '/account-suspended';
  }
}
