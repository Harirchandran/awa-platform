import { useState } from 'react';
import { AlertTriangle, CheckCircle2, CreditCard, ExternalLink, Loader2, MessageCircle, PhoneCall, PlayCircle, ShieldCheck, Video } from 'lucide-react';
import { cn } from '@/lib/utils';
const DEFAULT_SIGNUP_VIDEO_URL = 'https://www.youtube-nocookie.com/embed/2Y6IGrhNi40';
const DEFAULT_SIGNUP_WATCH_URL = 'https://www.youtube.com/watch?v=2Y6IGrhNi40';
const GUIDE_STEPS = [
  {
    title: 'Keep a fresh business number ready',
    description: 'Best choice is a number that can receive SMS or voice OTP and is not active on normal WhatsApp.',
    icon: PhoneCall,
  },
  {
    title: 'Use Facebook to verify ownership',
    description: 'Meta will ask the owner to log in, select or create the business, and approve AWA access.',
    icon: ShieldCheck,
  },
  {
    title: 'AWA replies only after customers message',
    description: 'Inbound chats, quick replies, lists, and booking steps stay in the customer service flow by default.',
    icon: MessageCircle,
  },
] as const;
function getSignupVideo() {
  const configured = import.meta.env.VITE_WHATSAPP_SIGNUP_VIDEO_URL || DEFAULT_SIGNUP_VIDEO_URL;
  return normalizeVideoUrl(configured);
}
function normalizeVideoUrl(rawUrl: string) {
  try {
    const url = new URL(rawUrl);
    if (url.hostname.includes('youtu.be')) {
      const videoId = url.pathname.replace('/', '');
      return videoId
        ? buildYoutubeVideo(videoId)
        : buildYoutubeVideo('2Y6IGrhNi40');
    }
    if (url.hostname.includes('youtube.com')) {
      if (url.pathname.startsWith('/embed/')) {
        const videoId = url.pathname.split('/embed/')[1]?.split('/')[0];
        return videoId
          ? buildYoutubeVideo(videoId, rawUrl)
          : { embedUrl: rawUrl, watchUrl: DEFAULT_SIGNUP_WATCH_URL, thumbnailUrl: null };
      }
      const videoId = url.searchParams.get('v');
      return videoId ? buildYoutubeVideo(videoId) : buildYoutubeVideo('2Y6IGrhNi40');
    }
    return { embedUrl: rawUrl, watchUrl: rawUrl, thumbnailUrl: null };
  } catch {
    return buildYoutubeVideo('2Y6IGrhNi40');
  }
}
function buildYoutubeVideo(videoId: string, embedUrl = `https://www.youtube-nocookie.com/embed/${videoId}`) {
  return {
    embedUrl,
    watchUrl: `https://www.youtube.com/watch?v=${videoId}`,
    thumbnailUrl: `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
  };
}
interface WhatsAppSignupGuideProps {
  className?: string;
  tone?: 'default' | 'dark';
  showVideo?: boolean;
}
export function WhatsAppSignupGuide({ className, tone = 'default', showVideo = true }: WhatsAppSignupGuideProps) {
  const [inlineVideoRequested, setInlineVideoRequested] = useState(false);
  const [inlineVideoLoaded, setInlineVideoLoaded] = useState(false);
  const [inlineVideoFailed, setInlineVideoFailed] = useState(false);
  const dark = tone === 'dark';
  const panelClass = dark
    ? 'border-zinc-700 bg-zinc-900/85 text-zinc-100'
    : 'border-border bg-card text-card-foreground';
  const mutedClass = dark ? 'text-zinc-300' : 'text-muted-foreground';
  const softPanelClass = dark ? 'border-zinc-700 bg-zinc-900/70' : 'border-border bg-muted/30';
  const video = getSignupVideo();
  const handlePlayInlineVideo = () => {
    setInlineVideoRequested(true);
    setInlineVideoLoaded(false);
    setInlineVideoFailed(false);
  };
  return (
    <div className={cn('space-y-4', className)}>
      <div className={cn('rounded-lg border p-4', panelClass)}>
        <div className="mb-4 flex items-start gap-3">
          <div className={cn('mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md', dark ? 'bg-emerald-500/15 text-emerald-300' : 'bg-emerald-500/10 text-emerald-600')}>
            <CheckCircle2 className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-[14px] font-semibold">Before you connect WhatsApp</h3>
            <p className={cn('mt-1 text-[12px] leading-5', mutedClass)}>
              This setup connects the business number to AWA. It does not charge the tenant in AWA.
            </p>
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {GUIDE_STEPS.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.title} className={cn('rounded-md border p-3', softPanelClass)}>
                <Icon className={cn('mb-2 h-4 w-4', dark ? 'text-zinc-300' : 'text-foreground')} />
                <p className="text-[12px] font-semibold leading-5">{item.title}</p>
                <p className={cn('mt-1 text-[12px] leading-5', mutedClass)}>{item.description}</p>
              </div>
            );
          })}
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-[0.95fr_1.05fr]">
        <div className={cn('rounded-lg border p-4', panelClass)}>
          <div className="flex items-start gap-3">
            <div className={cn('mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md', dark ? 'bg-amber-500/15 text-amber-300' : 'bg-amber-500/10 text-amber-700')}>
              <CreditCard className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-[14px] font-semibold">Payment clarity</h3>
              <p className={cn('mt-1 text-[12px] leading-5', mutedClass)}>
                Meta may ask for a payment method. That is not an AWA checkout and it does not charge now.
                AWA keeps paid templates and marketing off by default.
              </p>
            </div>
          </div>
          <div className={cn('mt-3 flex items-start gap-2 rounded-md border px-3 py-2 text-[12px] leading-5', dark ? 'border-amber-400/20 bg-amber-400/10 text-amber-100' : 'border-amber-500/20 bg-amber-500/10 text-amber-900')}>
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>Using an existing normal WhatsApp number can require migration or coexistence. Fresh number is simpler.</span>
          </div>
        </div>
        {showVideo && (
          <div className={cn('rounded-lg border p-4', panelClass)}>
            <div className="mb-3 flex items-center gap-2">
              <Video className={cn('h-4 w-4', dark ? 'text-zinc-300' : 'text-foreground')} />
              <h3 className="text-[14px] font-semibold">Watch the signup flow</h3>
            </div>
            <div className={cn(
              'relative aspect-video overflow-hidden rounded-md border',
              dark ? 'border-zinc-700 bg-zinc-950' : 'border-border bg-slate-950'
            )}>
              {inlineVideoRequested && !inlineVideoFailed && (
                <iframe
                  className={cn('absolute inset-0 h-full w-full transition-opacity duration-200', inlineVideoLoaded ? 'opacity-100' : 'opacity-0')}
                  src={video.embedUrl}
                  title="WhatsApp Embedded Signup walkthrough"
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                  allowFullScreen
                  onLoad={() => setInlineVideoLoaded(true)}
                  onError={() => setInlineVideoFailed(true)}
                />
              )}
              {(!inlineVideoRequested || !inlineVideoLoaded || inlineVideoFailed) && (
                <div className="absolute inset-0">
                  {video.thumbnailUrl && (
                    <img
                      src={video.thumbnailUrl}
                      alt=""
                      className={cn(
                        'absolute inset-0 h-full w-full object-cover',
                        inlineVideoRequested && !inlineVideoFailed ? 'opacity-25' : 'opacity-40'
                      )}
                    />
                  )}
                  <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(16,185,129,0.22),transparent_32%),linear-gradient(135deg,rgba(9,9,11,0.92),rgba(24,24,27,0.78))]" />
                  <div className="relative flex h-full flex-col justify-between p-4">
                    <div>
                      <div className={cn('mb-2 inline-flex h-9 w-9 items-center justify-center rounded-full', dark ? 'bg-white/10 text-white' : 'bg-white/15 text-white')}>
                        {inlineVideoRequested && !inlineVideoFailed ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
                      </div>
                      <p className="text-[13px] font-semibold leading-5 text-white">
                        {inlineVideoFailed
                          ? 'Video preview is unavailable'
                          : inlineVideoRequested
                          ? 'Loading signup walkthrough'
                          : 'WhatsApp setup walkthrough'}
                      </p>
              <p className="mt-1 max-w-[25rem] text-[12px] leading-5 text-zinc-200">
                        {inlineVideoRequested
                          ? 'If the embed stays blank, open the walkthrough in a new tab.'
                          : 'A short guide opens the Meta embedded signup flow without leaving this setup.'}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {!inlineVideoRequested || inlineVideoFailed ? (
                        <button
                          type="button"
                          onClick={handlePlayInlineVideo}
                          className="inline-flex h-8 items-center rounded-md bg-white px-3 text-[12px] font-medium text-zinc-950 transition hover:bg-zinc-200"
                        >
                          <PlayCircle className="mr-1.5 h-3.5 w-3.5" />
                          Play here
                        </button>
                      ) : null}
                      <a
                        href={video.watchUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex h-8 items-center rounded-md border border-white/20 bg-white/10 px-3 text-[12px] font-medium text-white transition hover:bg-white/15"
                      >
                        <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                        Open video
                      </a>
                    </div>
                  </div>
                </div>
              )}
              {inlineVideoLoaded && !inlineVideoFailed && (
                <a
                  href={video.watchUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="absolute right-2 top-2 inline-flex h-7 items-center rounded-md border border-white/20 bg-black/55 px-2.5 text-[11px] font-medium text-white backdrop-blur transition hover:bg-black/70"
                >
                  <ExternalLink className="mr-1 h-3 w-3" />
                  Open
                </a>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}