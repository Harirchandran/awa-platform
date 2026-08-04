import { useEffect, useRef, useState } from 'react';
import { Info, ShieldAlert, RefreshCw, Phone, Loader2 } from 'lucide-react';
import { useWhatsAppAccounts } from '@/features/whatsapp/api';
import { WhatsAppAccountCard } from '@/features/whatsapp/components/WhatsAppAccountCard';
import { IgnoredNumbersManager } from '@/features/whatsapp/components/IgnoredNumbersManager';
import { WhatsAppSignupGuide } from '@/features/whatsapp/components/WhatsAppSignupGuide';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useQueryClient } from '@tanstack/react-query';
import { useTenantStore } from '@/stores/tenant.store';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { initializeFacebookSdk, shouldUseMockEmbeddedSignup, type FacebookLoginResponse } from '@/lib/facebook-sdk';
import toast from 'react-hot-toast';
interface EmbeddedSignupInfo {
  phone_number_id?: string;
  waba_id?: string;
  business_id?: string;
}
const FACEBOOK_SIGNUP_TIMEOUT_MS = 10000;
export function WhatsAppPage() {
  const { data: accounts, isLoading, isError } = useWhatsAppAccounts();
  const queryClient = useQueryClient();
  const tenantId = useTenantStore((s) => s.activeTenantId);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [confirmConnectOpen, setConfirmConnectOpen] = useState(false);
  const facebookReadyRef = useRef(false);
  const embeddedSignupInfoRef = useRef<EmbeddedSignupInfo>({});
  const connectTimeoutRef = useRef<number | null>(null);
  const isMockSignup = shouldUseMockEmbeddedSignup();
  const signupMode = getWhatsAppSignupMode(isMockSignup);
  const signupModeCopy = getWhatsAppSignupModeCopy(signupMode);
  const connectDialogDescription = getWhatsAppConnectDialogDescription(signupMode);
  const clearConnectTimeout = () => {
    if (connectTimeoutRef.current !== null) {
      window.clearTimeout(connectTimeoutRef.current);
      connectTimeoutRef.current = null;
    }
  };
  useEffect(() => {
    if (shouldUseMockEmbeddedSignup() || typeof window === 'undefined' || !window.FB || facebookReadyRef.current) return;
    const appId = import.meta.env.VITE_FACEBOOK_APP_ID;
    if (!appId) return;
    initializeFacebookSdk(window.FB, appId);
    facebookReadyRef.current = true;
  }, []);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleMessage = (event: MessageEvent) => {
      if (!event.origin.endsWith('facebook.com')) return;
      if (typeof event.data !== 'string') return;
      try {
        const payload = JSON.parse(event.data);
        if (payload?.type !== 'WA_EMBEDDED_SIGNUP') return;
        const data = payload.data || {};
        if (payload.event === 'FINISH' || payload.event === 'FINISH_ONLY_WABA') {
          embeddedSignupInfoRef.current = {
            phone_number_id: data.phone_number_id,
            waba_id: data.waba_id,
            business_id: data.business_id,
          };
        }
      } catch {
        // Ignore unrelated Facebook SDK messages.
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);
  useEffect(() => clearConnectTimeout, []);
  const handleRefresh = async () => {
    setIsRefreshing(true);
    setConnectionError(null);
    await queryClient.invalidateQueries({ queryKey: ['whatsapp', tenantId] });
    setIsRefreshing(false);
  };
  const handleFacebookLogin = async () => {
    setIsConnecting(true);
    setConnectionError(null);
    const useMockSignup = shouldUseMockEmbeddedSignup();
    if (!useMockSignup && typeof window !== 'undefined' && window.FB) {
      const configId = import.meta.env.VITE_FACEBOOK_CONFIG_ID;
      const blockedReason = getFacebookSignupBlockedReason();
      if (blockedReason) {
        setConnectionError(blockedReason);
        toast.error(blockedReason);
        setIsConnecting(false);
        return;
      }
      if (!configId) {
        const message = 'Facebook signup is not configured. Add the Meta configuration ID, then try again.';
        setConnectionError(message);
        toast.error(message);
        setIsConnecting(false);
        return;
      }
      let settled = false;
      const settle = (next: () => void) => {
        if (settled) return;
        settled = true;
        clearConnectTimeout();
        next();
      };
      connectTimeoutRef.current = window.setTimeout(() => {
        settle(() => {
          const message = 'Facebook signup did not respond. Check pop-up blockers, Meta setup, or open AWA from an HTTPS domain, then try again.';
          setConnectionError(message);
          toast.error(message);
          setIsConnecting(false);
        });
      }, FACEBOOK_SIGNUP_TIMEOUT_MS);
      try {
        window.FB.login((response: FacebookLoginResponse) => {
          settle(() => {
            if (response.authResponse && response.authResponse.code) {
              api.post('/api/v1/whatsapp-accounts/embedded-signup', {
                code: response.authResponse.code,
                ...embeddedSignupInfoRef.current,
              })
              .then(() => {
                toast.success('Connected!');
                queryClient.invalidateQueries({ queryKey: ['whatsapp', tenantId] });
              })
              .catch((error: unknown) => {
                const message = `Failed: ${getApiErrorMessage(error)}`;
                setConnectionError(message);
                toast.error(message);
              })
              .finally(() => setIsConnecting(false));
            } else {
              const message = 'Facebook signup was cancelled before a WhatsApp number was connected.';
              setConnectionError(message);
              toast.error(message);
              setIsConnecting(false);
            }
          });
        }, {
          config_id: configId,
          response_type: 'code', override_default_response_type: true,
          extras: { setup: {}, featureType: '', sessionInfoVersion: '2' }
        });
      } catch (error: unknown) {
        settle(() => {
          const message = getFacebookLoginErrorMessage(error);
          setConnectionError(message);
          toast.error(message);
          setIsConnecting(false);
        });
      }
    } else if (useMockSignup) {
      try {
        await api.post('/api/v1/whatsapp-accounts/embedded-signup', { code: 'mocked_oauth_code', ...embeddedSignupInfoRef.current });
        toast.success('Mock connected!');
        setConnectionError(null);
        queryClient.invalidateQueries({ queryKey: ['whatsapp', tenantId] });
      } catch (error: unknown) {
        const message = `Failed: ${getApiErrorMessage(error)}`;
        setConnectionError(message);
        toast.error(message);
      }
      finally { setIsConnecting(false); }
    } else {
      const message = 'Facebook SDK is unavailable. Check the signup configuration or browser blockers, then refresh and try again.';
      setConnectionError(message);
      toast.error(message);
      setIsConnecting(false);
    }
  };
  const hasAccounts = Boolean(accounts && accounts.length > 0);
  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-[16px] font-semibold text-foreground">WhatsApp</h2>
          <p className="text-[13px] text-muted-foreground mt-0.5">Manage WhatsApp setup and test/live connection state.</p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
          {hasAccounts && !isMockSignup && (
            <Button onClick={() => setConfirmConnectOpen(true)} disabled={isConnecting} size="sm" className="h-9 w-full bg-[#1877F2] text-[13px] text-white hover:bg-[#1864D9] sm:h-8 sm:w-auto">
              {isConnecting ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : null}
              {isConnecting ? 'Connecting...' : 'Connect Another'}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={handleRefresh} disabled={isRefreshing || isLoading} className="h-9 w-full text-[13px] sm:h-8 sm:w-auto">
            <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isRefreshing ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
      </div>
      <div className="rounded-md border border-sky-500/25 bg-sky-500/10 p-3 text-[13px] text-sky-950 dark:text-sky-100">
        <div className="flex items-start gap-2.5">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">{signupModeCopy.title}</p>
            <p className="mt-1 text-[12px] leading-5 opacity-90">{signupModeCopy.description}</p>
          </div>
        </div>
      </div>
      {!isLoading && !isError && !hasAccounts && (
        <WhatsAppSignupGuide />
      )}
      <IgnoredNumbersManager />
      {connectionError && (
        <div role="alert" className="border border-destructive/20 bg-destructive/5 p-3 rounded-md flex items-start gap-2.5 text-destructive text-[13px]">
          <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0" />
          <p>{connectionError}</p>
        </div>
      )}
      {isError && (
        <div className="border border-destructive/20 bg-destructive/5 p-3 rounded-md flex items-start gap-2.5 text-destructive text-[13px]">
          <ShieldAlert className="w-4 h-4 mt-0.5" />
          <p>Failed to load connections. Try refreshing.</p>
        </div>
      )}
      {isLoading ? (
        <div className="h-40 rounded-md bg-card border border-border animate-pulse" />
      ) : accounts && accounts.length > 0 ? (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {accounts.map(account => <WhatsAppAccountCard key={account.id} account={account} />)}
        </div>
      ) : !isError && (
        <Card>
          <CardContent className="text-center py-12">
            <Phone className="w-6 h-6 text-muted-foreground mx-auto mb-3" />
            <p className="text-[14px] font-medium text-foreground mb-1">No number linked</p>
            <p className="text-[13px] text-muted-foreground max-w-sm mx-auto mb-4">
              Connect or test a WhatsApp Business number when you are ready. AWA Chat can be piloted without a live WhatsApp number.
            </p>
            <Button onClick={() => setConfirmConnectOpen(true)} disabled={isConnecting} size="sm" className="bg-[#1877F2] hover:bg-[#1864D9] text-white text-[13px] h-8">
              {isConnecting ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : null}
              {isConnecting ? 'Connecting...' : isMockSignup ? 'Connect Mock Number' : 'Connect Number'}
            </Button>
          </CardContent>
        </Card>
      )}
      <Dialog open={confirmConnectOpen} onOpenChange={setConfirmConnectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{isMockSignup ? 'Connect mock WhatsApp number?' : 'Connect WhatsApp Business?'}</DialogTitle>
            <DialogDescription>
              {connectDialogDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setConfirmConnectOpen(false)} disabled={isConnecting}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => {
                setConfirmConnectOpen(false);
                handleFacebookLogin();
              }}
              disabled={isConnecting}
            >
              {isConnecting ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
              Connect
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
type WhatsAppSignupMode = 'mock' | 'test' | 'live';
function getWhatsAppSignupMode(isMockSignup: boolean): WhatsAppSignupMode {
  if (isMockSignup) return 'mock';
  const explicitMode = (
    import.meta.env.VITE_WHATSAPP_MODE ||
    import.meta.env.VITE_AWA_INTEGRATION_MODE ||
    ''
  ).toLowerCase();
  if (explicitMode === 'live' || explicitMode === 'test') {
    return explicitMode;
  }
  return 'live';
}
function getWhatsAppSignupModeCopy(mode: WhatsAppSignupMode) {
  if (mode === 'mock') {
    return {
      title: 'Mock WhatsApp signup mode',
      description: 'Connections create a local simulated WhatsApp number. No Meta signup opens and no real WhatsApp credentials are stored.',
    };
  }
  if (mode === 'test') {
    return {
      title: 'Test WhatsApp signup mode',
      description: 'Connections use Meta Embedded Signup with test settings. Treat this as setup validation, not proof that the tenant is live on WhatsApp. Facebook requires HTTPS; local HTTP pages will show a recoverable setup error.',
    };
  }
  return {
    title: 'Live WhatsApp signup mode',
    description: 'Connections use Meta Embedded Signup and store the provider credentials needed for webhooks and outbound replies.',
  };
}
function getWhatsAppConnectDialogDescription(mode: WhatsAppSignupMode) {
  if (mode === 'mock') {
    return 'This creates or updates a simulated WhatsApp connection for local testing. No Meta signup opens, no real messages are sent, and no provider credentials are stored.';
  }
  if (mode === 'test') {
    return 'This starts WhatsApp setup validation with Meta test settings. Use it to verify setup flow, not as proof that this tenant is live for customer messaging.';
  }
  return 'This creates or updates a live WhatsApp connection for this tenant. AWA can receive messages on linked numbers and reply based on active tools and business rules.';
}
function getFacebookSignupBlockedReason() {
  if (typeof window === 'undefined') return null;
  if (window.location.protocol !== 'https:') {
    return 'Facebook Embedded Signup requires HTTPS. Use mock signup locally or open AWA from an HTTPS domain, then try again.';
  }
  return null;
}
function getFacebookLoginErrorMessage(error: unknown) {
  const fallback = 'Facebook signup could not start. Check the Meta app configuration, pop-up blockers, and HTTPS setup, then try again.';
  if (error instanceof Error && error.message) {
    if (/http|https|secure|origin/i.test(error.message)) {
      return 'Facebook Embedded Signup requires HTTPS. Use mock signup locally or open AWA from an HTTPS domain, then try again.';
    }
    return `${fallback} (${error.message})`;
  }
  return fallback;
}