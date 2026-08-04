export interface FacebookAuthResponse {
  code?: string;
}
export interface FacebookLoginResponse {
  authResponse?: FacebookAuthResponse | null;
  status?: string;
}
export interface FacebookLoginOptions {
  config_id: string;
  response_type: 'code';
  override_default_response_type: boolean;
  extras: {
    setup: Record<string, unknown>;
    featureType: string;
    sessionInfoVersion: string;
  };
}
export interface FacebookInitOptions {
  appId: string;
  cookie: boolean;
  xfbml: boolean;
  version: string;
}
export interface FacebookSDK {
  init(options: FacebookInitOptions): void;
  login(callback: (response: FacebookLoginResponse) => void, options: FacebookLoginOptions): void;
}
export function initializeFacebookSdk(facebook: FacebookSDK, appId: string) {
  facebook.init({
    appId,
    cookie: true,
    xfbml: true,
    version: import.meta.env.VITE_FACEBOOK_GRAPH_VERSION || 'v21.0',
  });
}
export function shouldUseMockEmbeddedSignup() {
  const appId = import.meta.env.VITE_FACEBOOK_APP_ID || '';
  const configId = import.meta.env.VITE_FACEBOOK_CONFIG_ID || '';
  const explicitMode = import.meta.env.VITE_WHATSAPP_MODE || import.meta.env.VITE_AWA_INTEGRATION_MODE || '';
  const mockSignupEnabled = import.meta.env.VITE_ENABLE_MOCK_WHATSAPP_SIGNUP === 'true';
  return (
    mockSignupEnabled &&
    (
      explicitMode === 'mock' ||
      !appId ||
      !configId ||
      appId === 'dummy_app_id' ||
      configId === 'dummy_config_id'
    )
  );
}