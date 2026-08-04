import type { FacebookSDK } from '@/lib/facebook-sdk';
interface RazorpayOptions {
  key: string;
  subscription_id: string;
  name: string;
  description: string;
  handler: () => void;
  theme?: {
    color: string;
  };
}
interface RazorpayInstance {
  open(): void;
}
interface RazorpayConstructor {
  new (options: RazorpayOptions): RazorpayInstance;
}
interface TurnstileRenderOptions {
  sitekey: string;
  callback?: (token: string) => void;
  'expired-callback'?: () => void;
  'error-callback'?: () => void;
  theme?: 'light' | 'dark' | 'auto';
}
interface TurnstileApi {
  render(container: HTMLElement, options: TurnstileRenderOptions): string;
  remove(widgetId: string): void;
  reset(widgetId?: string): void;
}
declare global {
  interface Window {
    FB?: FacebookSDK;
    Razorpay?: RazorpayConstructor;
    turnstile?: TurnstileApi;
  }
}
export {};