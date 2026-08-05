import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import {
  MessageSquare,
  Zap,
  Shield,
  BarChart3,
  ArrowRight,
  Bot,
  CheckCircle2,
  CalendarCheck,
  Headphones,
  Settings2,
} from 'lucide-react';
const features = [
  {
    icon: Bot,
    title: 'AI Conversations',
    description: 'AI-assisted routing and configurable business workflows help manage customer requests.',
  },
  {
    icon: MessageSquare,
    title: 'WhatsApp Native',
    description: 'Connect to Meta Cloud API signup, webhooks, and messaging when provider access is configured.',
  },
  {
    icon: Zap,
    title: 'Operator Configuration',
    description: 'Configure business workflows and evaluate them from the dashboard in a local environment.',
  },
  {
    icon: Shield,
    title: 'Privacy Controls',
    description: 'Consent management, retention controls, and erasure workflows.',
  },
  {
    icon: BarChart3,
    title: 'Analytics',
    description: 'Review conversation activity, usage costs, and configured workflow activity in one place.',
  },
  {
    icon: Headphones,
    title: 'Human Handoff',
    description: 'Route sensitive or low-confidence conversations to your team without losing the customer context.',
  },
];
const trustSignals = [
  { title: 'Meta Cloud API', label: 'Includes WhatsApp Cloud API signup and webhook integration paths.' },
  { title: 'Test-mode first', label: 'Evaluate local test settings before configuring external integrations.' },
  { title: 'Human handoff', label: 'Escalate conversations when confidence is low.' },
  { title: 'Usage visibility', label: 'Track messages, AI credits, and billing lifecycle.' },
];
export function LandingPage() {
  return (
    <div className="min-h-screen overflow-x-hidden bg-background text-foreground">
      <nav className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-start gap-3 px-4 sm:justify-between sm:px-6">
          <div className="min-w-0">
            <p className="text-[14px] font-semibold leading-tight">AWA</p>
            <p className="hidden text-[10px] leading-tight text-muted-foreground sm:block">Always Working Assistant</p>
          </div>
          <div className="flex min-w-0 items-center gap-2 sm:gap-5">
            <a href="#features" className="hidden text-[13px] text-muted-foreground transition-colors hover:text-foreground sm:inline">
              Features
            </a>
            <a href="#pricing" className="hidden text-[13px] text-muted-foreground transition-colors hover:text-foreground sm:inline">
              Pricing
            </a>
            <Link to="/login">
              <Button variant="ghost" size="sm" className="h-8 text-[13px] text-muted-foreground">
                Sign In
              </Button>
            </Link>
            <Link to="/signup" className="shrink-0">
              <Button size="sm" className="h-8 text-[13px]">
                Get Started
              </Button>
            </Link>
          </div>
        </div>
      </nav>
      <section className="motion-page px-4 pb-12 pt-12 sm:px-6 sm:pb-16 sm:pt-20">
        <div className="mx-auto grid max-w-5xl items-center gap-10 lg:grid-cols-[minmax(0,1fr)_420px]">
          <div className="min-w-0 max-w-[358px] sm:max-w-xl">
            <p className="mb-4 text-[13px] font-medium uppercase text-muted-foreground">Always Working Assistant</p>
            <h1 className="mb-5 max-w-[11ch] text-[34px] font-bold leading-[1.08] text-foreground sm:max-w-none sm:text-[48px]">
              Manage customer conversations from one workspace.
            </h1>
            <p className="mb-8 max-w-[350px] text-[16px] leading-[1.6] text-muted-foreground sm:max-w-xl sm:text-[18px]">
              AWA turns WhatsApp and browser chat into an AI front desk for bookings, support, lead capture, and follow-ups.
            </p>
            <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
              <Link to="/signup" className="w-full sm:w-auto">
                <Button size="lg" className="h-10 w-full px-5 text-[13px] sm:w-auto">
                  Get Started
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
              <Link to="/login" className="w-full sm:w-auto">
                <Button variant="outline" size="lg" className="h-10 w-full px-5 text-[13px] sm:w-auto">
                  Sign In
                </Button>
              </Link>
            </div>
            <div className="mt-7 grid gap-2 text-[13px] text-muted-foreground sm:grid-cols-2">
              {['Embedded WhatsApp signup', 'Configurable business workflows', 'Billing test mode', 'Conversation audit trail'].map((item) => (
                <div key={item} className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="relative z-10 w-full max-w-[358px] min-w-0 sm:max-w-none">
            <div className="absolute top-1/2 left-1/2 -z-10 h-[500px] w-[500px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-500/20 blur-[120px] pointer-events-none" />
            <div className="motion-enter motion-soft rounded-lg border border-border bg-card p-3 shadow-2xl">
              <div className="rounded-md border border-border bg-background">
                <div className="flex items-center justify-between border-b border-border px-4 py-3">
                  <div>
                    <p className="text-[13px] font-semibold text-foreground">AWA Demo Agent</p>
                    <p className="text-[12px] text-muted-foreground">SmileDent demo workspace</p>
                  </div>
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-400">Ready</span>
                </div>
                <div className="space-y-3 bg-zinc-950 px-4 py-5">
                  <div className="max-w-[82%] break-words rounded-2xl rounded-tl-sm bg-zinc-800 px-3 py-2 text-[13px] leading-relaxed text-zinc-100">
                    Hi, I need a cleaning appointment tomorrow.
                  </div>
                  <div className="ml-auto max-w-[88%] break-words rounded-2xl rounded-tr-sm bg-emerald-600 px-3 py-2 text-[13px] leading-relaxed text-white">
                    Sure. We have 10:30 AM and 4:00 PM available. Which works best?
                  </div>
                  <div className="max-w-[76%] break-words rounded-2xl rounded-tl-sm bg-zinc-800 px-3 py-2 text-[13px] leading-relaxed text-zinc-100">
                    10:30 AM please.
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 border-t border-border p-3">
                  <div className="rounded-md border border-border bg-muted/30 p-3">
                    <CalendarCheck className="mb-2 h-4 w-4 text-muted-foreground" />
                    <p className="text-[12px] font-medium text-foreground">Booking created</p>
                    <p className="text-[11px] text-muted-foreground">10:30 AM, Cleaning</p>
                  </div>
                  <div className="rounded-md border border-border bg-muted/30 p-3">
                    <Settings2 className="mb-2 h-4 w-4 text-muted-foreground" />
                    <p className="text-[12px] font-medium text-foreground">Agent synced</p>
                    <p className="text-[11px] text-muted-foreground">Hours, FAQs, services</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
      <section className="border-y border-border">
        <div className="mx-auto grid max-w-5xl grid-cols-1 divide-y divide-border sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-4">
          {trustSignals.map((signal) => (
            <div key={signal.title} className="motion-soft px-5 py-6">
              <p className="text-[13px] font-semibold text-foreground">{signal.title}</p>
              <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{signal.label}</p>
            </div>
          ))}
        </div>
      </section>
      <section id="features" className="px-4 py-16 sm:px-6 sm:py-20">
        <div className="mx-auto max-w-5xl">
          <p className="mb-3 text-[13px] font-medium uppercase tracking-wide text-muted-foreground">Capabilities</p>
          <h2 className="mb-10 text-[24px] font-bold sm:mb-12 sm:text-[28px]">Tools for chat, bookings, orders, follow-ups, and human handoff.</h2>
          <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-3">
            {features.map((feature) => (
              <div key={feature.title} className="motion-soft bg-background p-6">
                <div className="mb-4 inline-flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/10 ring-1 ring-emerald-500/20">
                  <feature.icon className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                </div>
                <h3 className="mb-1.5 text-[14px] font-semibold text-foreground">{feature.title}</h3>
                <p className="break-words text-[13px] leading-relaxed text-muted-foreground">{feature.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
      <section id="pricing" className="border-t border-border px-4 py-16 sm:px-6 sm:py-20">
        <div className="mx-auto max-w-xl text-center">
          <h2 className="mb-3 text-[26px] font-bold sm:text-[28px]">Explore the project locally.</h2>
          <p className="mb-6 text-[15px] text-muted-foreground">Set up and evaluate AWA in a local development environment.</p>
          <Link to="/signup">
            <Button size="lg" className="h-10 px-6 text-[13px]">
              Get Started
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
        </div>
      </section>
      <footer className="border-t border-border px-4 py-6 sm:px-6">
        <div className="mx-auto flex max-w-5xl flex-col items-center justify-between gap-3 sm:flex-row">
          <p className="text-[12px] text-muted-foreground">&copy; {new Date().getFullYear()} AWA, Always Working Assistant</p>
          <div className="flex gap-6">
            <a href="#" className="text-[12px] text-muted-foreground transition-colors hover:text-foreground">
              Privacy
            </a>
            <a href="#" className="text-[12px] text-muted-foreground transition-colors hover:text-foreground">
              Terms
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
