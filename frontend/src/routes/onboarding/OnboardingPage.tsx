import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { CheckCircle2, Clock, MessageSquare, Phone, ArrowRight, SkipForward, Sparkles, AlertTriangle, Loader2, QrCode, ExternalLink, Video } from 'lucide-react';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { normalizeAiMode } from '@/lib/ai-mode';
import { initializeFacebookSdk, shouldUseMockEmbeddedSignup, type FacebookLoginResponse } from '@/lib/facebook-sdk';
import { WhatsAppSignupGuide } from '@/features/whatsapp/components/WhatsAppSignupGuide';
import type { TenantConfig, WorkingHours } from '@/types/config.types';

const STEPS = [
  { id: 'hours', title: 'Business Hours', icon: Clock, description: 'Used for booking availability and customer expectations.' },
  { id: 'greeting', title: 'AI Greeting', icon: MessageSquare, description: 'Customer-safe opening message for every new chat.' },
  { id: 'channel', title: 'Customer Chat', icon: MessageSquare, description: 'Create a browser chat link now or connect WhatsApp when provider setup is ready.' },
  { id: 'done', title: 'All Set!', icon: Sparkles, description: 'Review setup and continue to the dashboard.' },
];

const DAY_OPTIONS = [
  { key: 'monday', label: 'Mon' },
  { key: 'tuesday', label: 'Tue' },
  { key: 'wednesday', label: 'Wed' },
  { key: 'thursday', label: 'Thu' },
  { key: 'friday', label: 'Fri' },
  { key: 'saturday', label: 'Sat' },
  { key: 'sunday', label: 'Sun' },
] as const;

type DayKey = typeof DAY_OPTIONS[number]['key'];

interface TenantConfigRecord {
  config_json?: TenantConfig | string;
}

function extractTenantConfig(payload: TenantConfigRecord | TenantConfig): TenantConfig {
  const rawConfig =
    typeof payload === 'object' && payload !== null && 'config_json' in payload
      ? payload.config_json
      : payload;
  return (typeof rawConfig === 'string' ? JSON.parse(rawConfig) : rawConfig) as TenantConfig;
}

function buildWorkingHours(selectedDays: DayKey[], hours: { open: string; close: string }): WorkingHours {
  return DAY_OPTIONS.reduce<WorkingHours>((schedule, day) => {
    schedule[day.key] = {
      is_open: selectedDays.includes(day.key),
      open_time: hours.open,
      close_time: hours.close,
    };
    return schedule;
  }, {});
}

export function OnboardingPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const facebookReadyRef = useRef(false);
  const onboarding = JSON.parse(localStorage.getItem('awa_onboarding') || '{}');
  const businessName = onboarding.business_name || 'Your Business';

  // Form state
  const [hours, setHours] = useState({ open: '09:00', close: '18:00' });
  const [workingDays, setWorkingDays] = useState<DayKey[]>(['monday', 'tuesday', 'wednesday', 'thursday', 'friday']);
  const [greeting, setGreeting] = useState(
    `Hello! Welcome to ${businessName}. How can I help you today?`
  );
  const [basicsSaved, setBasicsSaved] = useState(false);
  const [isSavingBasics, setIsSavingBasics] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  const [waPhone, setWaPhone] = useState('');
  const [waConnecting, setWaConnecting] = useState(false);
  const [waConnected, setWaConnected] = useState(false);
  const [waError, setWaError] = useState<string | null>(null);
  const [awaChatUrl, setAwaChatUrl] = useState('');
  const [awaChatConnecting, setAwaChatConnecting] = useState(false);

  const [showWhatsAppSetup, setShowWhatsAppSetup] = useState(false);
  const [showSetupVideo, setShowSetupVideo] = useState(false);

  useEffect(() => {
    if (shouldUseMockEmbeddedSignup() || typeof window === 'undefined' || !window.FB || facebookReadyRef.current) {
      return;
    }
    const appId = import.meta.env.VITE_FACEBOOK_APP_ID;
    if (!appId) {
      return;
    }
    initializeFacebookSdk(window.FB, appId);
    facebookReadyRef.current = true;
  }, []);

  const handleSkip = () => {
    localStorage.setItem('awa_onboarding', JSON.stringify({ ...onboarding, completed: true }));
    navigate('/dashboard');
  };

  const validateBasicInputs = () => {
    if (!workingDays.length) {
      setConfigError('Select at least one working day before continuing.');
      return false;
    }
    if (hours.open >= hours.close) {
      setConfigError('Opening time must be earlier than closing time.');
      return false;
    }
    setConfigError(null);
    return true;
  };

  const saveBasicConfig = async () => {
    if (!validateBasicInputs()) {
      return false;
    }
    setIsSavingBasics(true);
    setConfigError(null);
    try {
      const response = await api.get<TenantConfigRecord | TenantConfig>('/api/v1/config');
      const currentConfig = extractTenantConfig(response.data);
      const updatedConfig: TenantConfig = {
        ...currentConfig,
        business_name: currentConfig.business_name || businessName,
        business_type: currentConfig.business_type || 'business',
        ai_mode: normalizeAiMode(currentConfig.ai_mode),
        ai_tone: currentConfig.ai_tone || 'professional and friendly',
        timezone: currentConfig.timezone || 'Asia/Kolkata',
        default_language: currentConfig.default_language || 'en',
        greeting_message: greeting.trim() || `Hello! Welcome to ${businessName}. How can I help you today?`,
        working_hours: buildWorkingHours(workingDays, hours),
      };
      await api.put<TenantConfigRecord>('/api/v1/config', { config_json: updatedConfig });
      setBasicsSaved(true);
      return true;
    } catch (error: unknown) {
      setConfigError(getApiErrorMessage(error, 'Could not save your business hours and greeting.'));
      return false;
    } finally {
      setIsSavingBasics(false);
    }
  };

  const handleNext = async () => {
    if (step === 0 && !validateBasicInputs()) {
      return;
    }
    if (step === 1 && !basicsSaved) {
      const saved = await saveBasicConfig();
      if (!saved) {
        return;
      }
    }
    if (step < STEPS.length - 1) {
      setStep(step + 1);
    }
  };

  const handleFinish = () => {
    localStorage.setItem('awa_onboarding', JSON.stringify({ ...onboarding, completed: true }));
    navigate(awaChatUrl ? '/dashboard/awa-chat' : '/dashboard');
  };

  const handleCreateAwaChat = async () => {
    setAwaChatConnecting(true);
    setWaError(null);
    try {
      const response = await api.post<{ public_url: string }>('/api/v1/awa-chat/links', {
        display_name: businessName,
      });
      setAwaChatUrl(response.data.public_url);
    } catch (error: unknown) {
      setWaError(getApiErrorMessage(error, 'Could not create AWA Chat link'));
    } finally {
      setAwaChatConnecting(false);
    }
  };

  const handleFacebookLogin = async () => {
    setWaConnecting(true);
    setWaError(null);
    const useMockSignup = shouldUseMockEmbeddedSignup();
    if (!useMockSignup && typeof window !== 'undefined' && window.FB) {
      const configId = import.meta.env.VITE_FACEBOOK_CONFIG_ID;
      if (!configId) {
        setWaError('Facebook signup is not configured.');
        setWaConnecting(false);
        return;
      }
      window.FB.login((response: FacebookLoginResponse) => {
        if (response.authResponse && response.authResponse.code) {
          api.post<{ phone_number: string }>('/api/v1/whatsapp-accounts/embedded-signup', {
            code: response.authResponse.code
          })
          .then((res) => {
            setWaPhone(res.data.phone_number);
            setWaConnected(true);
          })
          .catch((error: unknown) => {
            setWaError(getApiErrorMessage(error, 'Failed to finish WhatsApp connection'));
          })
          .finally(() => setWaConnecting(false));
        } else {
          setWaError('User cancelled login or did not fully authorize.');
          setWaConnecting(false);
        }
      }, {
        config_id: configId,
        response_type: 'code',
        override_default_response_type: true,
        extras: { setup: {}, featureType: '', sessionInfoVersion: '2' }
      });
    } else if (useMockSignup) {
      try {
        const res = await api.post<{ phone_number: string }>('/api/v1/whatsapp-accounts/embedded-signup', {
          code: 'mocked_oauth_code'
        });
        setWaPhone(res.data.phone_number);
        setWaConnected(true);
      } catch (error: unknown) {
        setWaError(getApiErrorMessage(error, 'Mock connection failed'));
      } finally {
        setWaConnecting(false);
      }
    } else {
      setWaError('Facebook SDK is unavailable. Check the signup configuration or browser blockers.');
      setWaConnecting(false);
    }
  };

  const updateHours = (nextHours: { open: string; close: string }) => {
    setHours(nextHours);
    setBasicsSaved(false);
    setConfigError(null);
  };

  const toggleWorkingDay = (day: DayKey) => {
    setWorkingDays((current) => {
      const next = current.includes(day)
        ? current.filter((selectedDay) => selectedDay !== day)
        : [...current, day];
      return DAY_OPTIONS.filter(option => next.includes(option.key)).map(option => option.key);
    });
    setBasicsSaved(false);
    setConfigError(null);
  };

  const updateGreeting = (nextGreeting: string) => {
    setGreeting(nextGreeting);
    setBasicsSaved(false);
    setConfigError(null);
  };

  return (
    <div className="min-h-[100dvh] bg-zinc-950 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-start gap-4 border-b border-white/5 px-4 py-4 sm:justify-between sm:px-6">
        <div className="flex items-center space-x-2">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center">
            <span className="font-bold text-sm text-white">AW</span>
          </div>
          <span className="font-semibold text-white">Setup Wizard</span>
        </div>
        <Button variant="ghost" onClick={handleSkip} className="text-zinc-400 hover:text-white">
          <SkipForward className="w-4 h-4 mr-2" />
          Skip for now
        </Button>
      </div>

      {/* Progress */}
      <div className="w-full max-w-[390px] px-4 pt-8 sm:mx-auto sm:max-w-4xl md:px-16">
        <div className="grid grid-cols-4 gap-2 mb-8">
          {STEPS.map((s, i) => (
            <div key={s.id} className="min-w-0">
              <div className="flex items-center">
                <div className={`w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm font-medium transition-colors ${
                  i < step ? 'bg-emerald-600 text-white' :
                  i === step ? 'bg-indigo-600 text-white' :
                  'bg-zinc-800 text-zinc-500'
                }`}>
                  {i < step ? <CheckCircle2 className="w-4 h-4" /> : i + 1}
                </div>
                {i < STEPS.length - 1 && (
                  <div className={`ml-2 h-0.5 min-w-0 flex-1 transition-colors ${
                    i < step ? 'bg-emerald-600' : 'bg-zinc-800'
                  }`} />
                )}
              </div>
              <p className={`mt-2 truncate text-[10px] font-medium sm:text-[11px] ${
                i === step ? 'text-white' : i < step ? 'text-emerald-300' : 'text-zinc-400'
              }`}>
                {s.title}
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* Step Content */}
      <div className="w-full max-w-[390px] flex-1 px-4 sm:mx-auto sm:max-w-4xl md:px-16">
        <div className="mb-6">
          <h2 className="text-2xl font-bold text-white mb-1">{STEPS[step].title}</h2>
          <p className="text-zinc-300">{STEPS[step].description}</p>
        </div>

        {/* Step 0: Business Hours */}
        {step === 0 && (
          <Card className="bg-zinc-900 border-zinc-800">
            <CardContent className="pt-6 space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label className="text-zinc-300">Opening Time</Label>
                  <Input
                    type="time"
                    value={hours.open}
                    onChange={(e) => updateHours({ ...hours, open: e.target.value })}
                    className="bg-zinc-800 border-zinc-700 text-white"
                  />
                </div>
                <div className="space-y-2">
                  <Label className="text-zinc-300">Closing Time</Label>
                  <Input
                    type="time"
                    value={hours.close}
                    onChange={(e) => updateHours({ ...hours, close: e.target.value })}
                    className="bg-zinc-800 border-zinc-700 text-white"
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label className="text-zinc-300">Working Days</Label>
                <p className="text-xs text-zinc-400">These days are saved into your tenant configuration and used by enabled booking workflows.</p>
                <div className="flex flex-wrap gap-2">
                  {DAY_OPTIONS.map((day) => {
                    const selected = workingDays.includes(day.key);
                    return (
                    <button
                      key={day.key}
                      type="button"
                      aria-pressed={selected}
                      onClick={() => toggleWorkingDay(day.key)}
                      className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                        selected
                          ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                          : 'bg-zinc-800 text-zinc-500 hover:bg-zinc-700 hover:text-white'
                      }`}
                    >
                      {day.label}
                    </button>
                    );
                  })}
                </div>
              </div>
              {configError && step === 0 && (
                <Alert variant="destructive" className="bg-destructive/10 border-destructive/30">
                  <AlertTriangle className="w-4 h-4" />
                  <AlertDescription>{configError}</AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>
        )}

        {/* Step 1: AI Greeting */}
        {step === 1 && (
          <Card className="bg-zinc-900 border-zinc-800">
            <CardContent className="pt-6 space-y-4">
              <div className="space-y-2">
                <Label className="text-zinc-300">Greeting Message</Label>
                <p className="text-xs text-zinc-400">This is the first message AWA sends. Avoid promises that depend on tools or providers you have not enabled yet.</p>
                <Textarea
                  value={greeting}
                  onChange={(e) => updateGreeting(e.target.value)}
                  rows={4}
                  className="bg-zinc-800 border-zinc-700 text-white resize-none"
                />
              </div>
              <div className="rounded-lg bg-zinc-800 p-4 border border-zinc-700">
                <div className="text-xs text-zinc-400 mb-2">Preview - WhatsApp</div>
                <div className="flex items-start space-x-3">
                  <div className="w-8 h-8 rounded-full bg-emerald-600/20 flex items-center justify-center shrink-0">
                    <MessageSquare className="w-4 h-4 text-emerald-400" />
                  </div>
                  <div className="bg-zinc-700 px-4 py-2.5 rounded-2xl rounded-tl-sm max-w-sm">
                    <p className="text-sm text-zinc-200">{greeting}</p>
                  </div>
                </div>
              </div>
              {basicsSaved && (
                <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
                  <CheckCircle2 className="h-4 w-4" />
                  Saved to your agent configuration.
                </div>
              )}
              {configError && (
                <Alert variant="destructive" className="bg-destructive/10 border-destructive/30">
                  <AlertTriangle className="w-4 h-4" />
                  <AlertDescription>{configError}</AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>
        )}

        {/* Step 2: WhatsApp Connection */}
        {step === 2 && (
          <Card className="bg-zinc-900 border-zinc-800">
            <CardContent className="pt-6 space-y-4">
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
                <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-4">
                  <div className="mb-3 inline-flex rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-200">
                    Recommended
                  </div>
                  <div className="flex items-start gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-500/20">
                      <QrCode className="h-5 w-5 text-emerald-300" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3 className="text-[15px] font-semibold text-white">Create AWA Chat link now</h3>
                      <p className="mt-1 text-[13px] leading-5 text-zinc-300">
                        Get a QR and browser chat link you can share immediately. Turnstile and OTP requirements still follow server-side settings.
                      </p>
                      {awaChatUrl ? (
                        <div className="mt-3 rounded-md border border-emerald-500/20 bg-zinc-950/40 p-3">
                          <p className="break-all text-[12px] text-emerald-200">{awaChatUrl}</p>
                          <Button
                            type="button"
                            variant="outline"
                            className="mt-3 h-8 border-emerald-500/30 bg-transparent text-[13px] text-emerald-200 hover:bg-emerald-500/10"
                            onClick={() => window.open(awaChatUrl, '_blank')}
                          >
                            <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                            Open link
                          </Button>
                        </div>
                      ) : (
                        <Button
                          type="button"
                          onClick={handleCreateAwaChat}
                          disabled={awaChatConnecting}
                          className="mt-3 bg-emerald-500 text-zinc-950 hover:bg-emerald-400"
                        >
                          {awaChatConnecting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <QrCode className="mr-2 h-4 w-4" />}
                          Create AWA Chat Link
                        </Button>
                      )}
                    </div>
                  </div>
                </div>

                <div className="rounded-lg border border-zinc-800 bg-zinc-950/35 p-4">
                  {waConnected ? (
                    <div className="space-y-3 text-center">
                      <div className="w-14 h-14 rounded-full bg-emerald-600/20 flex items-center justify-center mx-auto">
                        <CheckCircle2 className="w-7 h-7 text-emerald-400" />
                      </div>
                      <h3 className="text-lg font-semibold text-white">WhatsApp Connected!</h3>
                      <p className="text-zinc-300 text-sm">
                        <span className="font-mono text-emerald-400">{waPhone}</span> is now linked to your account.
                      </p>
                    </div>
                  ) : (
                    <div className="flex h-full flex-col justify-between gap-4">
                      <div className="flex items-start gap-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#1877F2]/15">
                          <Phone className="h-5 w-5 text-[#7DB0FF]" />
                        </div>
                        <div>
                          <h3 className="text-[15px] font-semibold text-white">Connect WhatsApp Business</h3>
                          <p className="mt-1 text-[13px] leading-5 text-zinc-300">
                            Optional setup for your official WhatsApp Business number. This requires real Meta app configuration outside local mock mode.
                          </p>
                        </div>
                      </div>
                      <div className="flex flex-col gap-2 sm:flex-row lg:flex-col xl:flex-row">
                        <Button
                          type="button"
                          variant="outline"
                          className="h-9 border-zinc-700 bg-transparent text-[13px] text-zinc-100 hover:bg-zinc-800"
                          onClick={() => {
                            setShowWhatsAppSetup(true);
                            setShowSetupVideo(false);
                          }}
                        >
                          <Phone className="mr-1.5 h-3.5 w-3.5" />
                          Connect WhatsApp
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          className="h-9 text-[13px] text-zinc-300 hover:text-white"
                          onClick={() => {
                            setShowWhatsAppSetup(true);
                            setShowSetupVideo(true);
                          }}
                        >
                          <Video className="mr-1.5 h-3.5 w-3.5" />
                          Watch setup video
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {showWhatsAppSetup && !waConnected && (
                <div className="space-y-4 rounded-lg border border-zinc-800 bg-zinc-950/35 p-4">
                  <WhatsAppSignupGuide tone="dark" showVideo={showSetupVideo} />
                  <Button
                    onClick={handleFacebookLogin}
                    disabled={waConnecting}
                    className="w-full bg-[#1877F2] hover:bg-[#1864D9] text-white"
                  >
                    {waConnecting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> :
                     <svg className="w-5 h-5 mr-3" fill="currentColor" viewBox="0 0 24 24"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.469h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.469h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
                    }
                    {waConnecting ? 'Connecting...' : shouldUseMockEmbeddedSignup() ? 'Connect mock number' : 'Connect with Facebook'}
                  </Button>
                  {!showSetupVideo && (
                    <Button
                      type="button"
                      variant="ghost"
                      className="w-full text-zinc-300 hover:text-white"
                      onClick={() => setShowSetupVideo(true)}
                    >
                      <Video className="mr-1.5 h-3.5 w-3.5" />
                      Watch setup video
                    </Button>
                  )}
                </div>
              )}

              {waError && (
                <Alert variant="destructive" className="bg-destructive/10 border-destructive/30">
                  <AlertTriangle className="w-4 h-4" />
                  <AlertDescription>{waError}</AlertDescription>
                </Alert>
              )}
              <p className="text-center text-xs text-zinc-400">
                You can continue now. Tools that require verification, billing, or provider setup stay governed by backend safety rules.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Step 3: Done */}
        {step === 3 && (
          <Card className="bg-zinc-900 border-zinc-800">
            <CardContent className="pt-8 pb-8 text-center space-y-4">
              <div className="w-16 h-16 rounded-full bg-emerald-600/20 flex items-center justify-center mx-auto">
                <Sparkles className="w-8 h-8 text-emerald-400" />
              </div>
              <h3 className="text-xl font-semibold text-white">Your AI agent is ready!</h3>
              <p className="text-zinc-300 max-w-sm mx-auto">
                {awaChatUrl
                  ? `Your AWA Chat QR link is ready. Share it with customers so they can message ${businessName} from any browser.`
                  : waConnected
                  ? `Your WhatsApp number is connected. Customers can now message ${businessName} and get instant AI responses.`
                  : `You can connect WhatsApp later from the dashboard. Provider-backed features remain disabled until real credentials are configured.`
                }
              </p>
            </CardContent>
          </Card>
        )}

        {/* Navigation */}
        <div className="flex justify-between mt-8 pb-12">
          <Button
            variant="ghost"
            onClick={() => step > 0 && setStep(step - 1)}
            disabled={step === 0}
            className="text-zinc-400"
          >
            Back
          </Button>
          {step < STEPS.length - 1 ? (
            <Button onClick={handleNext} disabled={isSavingBasics} className="bg-indigo-600 hover:bg-indigo-700 text-white">
              {isSavingBasics && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {isSavingBasics ? 'Saving...' : 'Continue'}
              <ArrowRight className="ml-2 w-4 h-4" />
            </Button>
          ) : (
            <Button onClick={handleFinish} className="bg-emerald-600 hover:bg-emerald-700 text-white">
              Go to Dashboard
              <ArrowRight className="ml-2 w-4 h-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
