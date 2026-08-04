import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, WifiOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface ChatWelcomeProps {
  businessName: string;
  privacyNote: string;
  isOnline: boolean;
  visitorName: string;
  onVisitorNameChange: (name: string) => void;
  captchaRequired?: boolean;
  captchaMode?: string;
  captchaToken: string | null;
  onCaptchaTokenChange: (token: string | null) => void;
  turnstileSiteKey?: string;
  errorMessage: string | null;
  isStarting: boolean;
  onStart: () => void;
}

interface TurnstileWidgetProps {
  siteKey: string;
  onTokenChange: (token: string | null) => void;
  onErrorChange: (message: string | null) => void;
}

let turnstileScriptPromise: Promise<void> | null = null;
function loadTurnstileScript() {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('Turnstile is not available during server rendering.'));
  }
  if (window.turnstile) {
    return Promise.resolve();
  }
  if (!turnstileScriptPromise) {
    turnstileScriptPromise = new Promise((resolve, reject) => {
      const existing = document.querySelector<HTMLScriptElement>('script[data-awa-turnstile="true"]');
      if (existing) {
        existing.addEventListener('load', () => resolve(), { once: true });
        existing.addEventListener('error', () => reject(new Error('Turnstile script failed to load.')), { once: true });
        return;
      }
      const script = document.createElement('script');
      script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
      script.async = true;
      script.defer = true;
      script.dataset.awaTurnstile = 'true';
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Turnstile script failed to load.'));
      document.head.appendChild(script);
    });
  }
  return turnstileScriptPromise;
}

function TurnstileWidget({ siteKey, onTokenChange, onErrorChange }: TurnstileWidgetProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetIdRef = useRef<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    onTokenChange(null);
    onErrorChange(null);
    loadTurnstileScript()
      .then(() => {
        if (cancelled || !containerRef.current || !window.turnstile) return;
        if (widgetIdRef.current !== null) return;
        widgetIdRef.current = window.turnstile.render(containerRef.current, {
          sitekey: siteKey,
          theme: 'auto',
          callback: (token) => {
            onErrorChange(null);
            onTokenChange(token);
          },
          'expired-callback': () => {
            onTokenChange(null);
            onErrorChange('Verification expired. Please complete the check again.');
          },
          'error-callback': () => {
            onTokenChange(null);
            onErrorChange('Verification failed to load. Refresh and try again.');
          },
        });
      })
      .catch(() => {
        if (!cancelled) {
          onTokenChange(null);
          onErrorChange('Verification could not load. Check your connection and refresh.');
        }
      });
    return () => {
      cancelled = true;
      if (widgetIdRef.current && window.turnstile) {
        window.turnstile.remove(widgetIdRef.current);
      }
      widgetIdRef.current = null;
    };
  }, [siteKey, onErrorChange, onTokenChange]);
  return <div ref={containerRef} className="min-h-[65px]" />;
}

export function ChatWelcome({
  businessName,
  privacyNote,
  isOnline,
  visitorName,
  onVisitorNameChange,
  captchaRequired,
  captchaMode,
  captchaToken,
  onCaptchaTokenChange,
  turnstileSiteKey,
  errorMessage,
  isStarting,
  onStart,
}: ChatWelcomeProps) {
  const [turnstileError, setTurnstileError] = useState<string | null>(null);
  const normalizedCaptchaMode = captchaMode || 'disabled';
  const needsMockCaptcha = Boolean(captchaRequired && normalizedCaptchaMode === 'mock');
  const needsTurnstile = Boolean(captchaRequired && normalizedCaptchaMode === 'turnstile');
  const unsupportedCaptcha = Boolean(
    captchaRequired && !['mock', 'turnstile'].includes(normalizedCaptchaMode)
  );
  const missingTurnstileSiteKey = Boolean(needsTurnstile && !turnstileSiteKey);
  const captchaSatisfied = !captchaRequired || Boolean(captchaToken);
  const captchaBlocked = Boolean(
    captchaRequired && (
      unsupportedCaptcha ||
      missingTurnstileSiteKey ||
      !captchaSatisfied ||
      Boolean(turnstileError)
    )
  );
  return (
    <section className="flex flex-1 flex-col justify-center gap-4 px-4 py-8">
      <div>
        <h2 className="text-[24px] font-semibold leading-tight text-foreground">Start a chat with {businessName}</h2>
        <p className="mt-2 text-[14px] leading-6 text-muted-foreground">{privacyNote}</p>
      </div>
      <div className="hidden sm:grid sm:grid-cols-3 gap-2">
        <div className="rounded-md border border-border bg-card px-3 py-2">
          <p className="text-[12px] font-medium text-foreground">Verified business</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">Connected by QR link</p>
        </div>
        <div className="rounded-md border border-border bg-card px-3 py-2">
          <p className="text-[12px] font-medium text-foreground">No app needed</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">Works in browser</p>
        </div>
        <div className="rounded-md border border-border bg-card px-3 py-2">
          <p className="text-[12px] font-medium text-foreground">Private session</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">Details only if needed</p>
        </div>
      </div>
      {!isOnline && (
        <div
          className="rounded-md border border-amber-200 dark:border-amber-500/20 bg-amber-50 dark:bg-amber-500/10 p-3 text-[12px] leading-5 text-amber-800 dark:text-amber-100"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <div className="flex gap-2">
            <WifiOff className="mt-0.5 h-4 w-4 shrink-0" />
            <p>You are offline. Reconnect to start the chat.</p>
          </div>
        </div>
      )}
      <div className="rounded-md border border-border bg-card p-4">
        <label htmlFor="public-chat-visitor-name" className="mb-2 block text-[13px] text-foreground">Name optional</label>
        <Input
          id="public-chat-visitor-name"
          value={visitorName}
          onChange={(event) => onVisitorNameChange(event.target.value)}
          maxLength={80}
          placeholder="Your name"
          className="h-10 border-input bg-background text-foreground placeholder:text-muted-foreground"
        />
        {needsMockCaptcha && (
          <label className="mt-3 flex items-start gap-2 rounded-md border border-border bg-background p-3 text-[12px] leading-5 text-foreground">
            <input
              type="checkbox"
              checked={captchaToken === 'local-captcha-ok'}
              onChange={(event) => onCaptchaTokenChange(event.target.checked ? 'local-captcha-ok' : null)}
              className="mt-1 cursor-pointer"
            />
            <span>
              I am not using automation to spam this chat.
              {' '}Test CAPTCHA mode is active locally.
            </span>
          </label>
        )}
        {needsTurnstile && turnstileSiteKey && (
          <div className="mt-3 rounded-md border border-border bg-background p-3">
            <TurnstileWidget
              siteKey={turnstileSiteKey}
              onTokenChange={onCaptchaTokenChange}
              onErrorChange={setTurnstileError}
            />
            <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
              Complete the verification check to start this chat.
            </p>
          </div>
        )}
        {missingTurnstileSiteKey && (
          <div className="mt-3 flex gap-2 rounded-md border border-red-500/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-800 dark:text-red-200" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>Chat verification is enabled, but the Turnstile site key is missing.</p>
          </div>
        )}
        {unsupportedCaptcha && (
          <div className="mt-3 flex gap-2 rounded-md border border-red-500/20 bg-red-500/10 p-3 text-[12px] leading-5 text-red-800 dark:text-red-200" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>Chat verification is enabled with an unsupported mode.</p>
          </div>
        )}
        {turnstileError && <p className="mt-2 text-[12px] text-red-600 dark:text-red-400" role="alert">{turnstileError}</p>}
        {errorMessage && (
          <p className="mt-2 text-[12px] text-red-600 dark:text-red-400" role="alert" aria-live="assertive" aria-atomic="true">
            {errorMessage}
          </p>
        )}
        <Button
          className="mt-4 h-10 w-full bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-zinc-950 dark:hover:bg-emerald-400"
          onClick={onStart}
          disabled={isStarting || !isOnline || captchaBlocked}
        >
          {isStarting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Start Chat
        </Button>
      </div>
    </section>
  );
}
