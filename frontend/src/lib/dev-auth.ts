const DEV_MOCK_TOKEN_KEY = 'awa_dev_mock_token';
const LEGACY_MOCK_TOKEN_KEY = 'mock_token';
export function isMockAuthEnabled() {
  return import.meta.env.VITE_ENABLE_MOCK_AUTH === 'true';
}
export function getDevMockToken() {
  if (!isMockAuthEnabled()) {
    clearDevMockToken();
    return null;
  }
  return sessionStorage.getItem(DEV_MOCK_TOKEN_KEY);
}
export function setDevMockToken(token: string) {
  if (!isMockAuthEnabled()) {
    return;
  }
  sessionStorage.setItem(DEV_MOCK_TOKEN_KEY, token);
  localStorage.removeItem(LEGACY_MOCK_TOKEN_KEY);
}
export function clearDevMockToken() {
  sessionStorage.removeItem(DEV_MOCK_TOKEN_KEY);
  localStorage.removeItem(LEGACY_MOCK_TOKEN_KEY);
}
export function isMockToken(value: string | null): value is string {
  return isMockAuthEnabled() && (value === 'mock_token' || value?.startsWith('mock_token:') === true);
}