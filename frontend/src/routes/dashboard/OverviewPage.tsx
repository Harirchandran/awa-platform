import { Link } from 'react-router';
import { useCostMetrics } from '@/features/overview/api';
import { CostChart } from '@/features/overview/components/CostChart';
import { useBillingStatus } from '@/features/billing/api';
import { useSystemHealth } from '@/features/health/api';
import { useWhatsAppAccounts } from '@/features/whatsapp/api';
import { useAwaChatLinks } from '@/features/awa-chat/api';
import { useTenantTools } from '@/features/tools/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { PageHeader } from '@/components/shared/PageHeader';
import { CopyButton } from '@/components/shared/CopyButton';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useCurrentUser } from '@/features/auth/hooks';
import { useEscalationsList } from '@/features/conversations/api';
import { useBookingsList } from '@/features/bookings/api';
import { useOrdersList } from '@/features/orders/api';
import { useLeadsList } from '@/features/leads/api';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  CreditCard,
  Database,
  ExternalLink,
  IndianRupee,
  MessagesSquare,
  Phone,
  PlugZap,
  RefreshCw,
  Settings,
  WalletCards,
  LifeBuoy,
  CalendarCheck,
  ShoppingBag,
  Users,
} from 'lucide-react';

const launchSteps = [
  {
    title: 'Confirm business profile',
    description: 'Set profile, hours, FAQs, and the first customer greeting.',
    href: '/dashboard/config',
    icon: Settings,
  },
  {
    title: 'Share AWA Chat',
    description: 'Create a public browser link or QR for pilot customers.',
    href: '/dashboard/awa-chat',
    icon: MessagesSquare,
  },
  {
    title: 'Tune the AI agent',
    description: 'Review knowledge, tone, and enabled tool behavior.',
    href: '/dashboard/config',
    icon: Bot,
  },
  {
    title: 'Check billing mode',
    description: 'Confirm test or live Razorpay settings before paid pilots.',
    href: '/dashboard/billing',
    icon: CreditCard,
  },
  {
    title: 'Optional WhatsApp',
    description: 'Connect only when Meta setup and webhooks are configured.',
    href: '/dashboard/whatsapp',
    icon: Phone,
  },
];
function StatCard({ title, value, subtitle, icon: Icon, href }: {
  title: string;
  value: string;
  subtitle: string;
  icon: React.ElementType;
  href?: string;
}) {
  const content = (
    <CardContent className="pt-5 pb-4 px-5">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[12px] text-muted-foreground font-medium uppercase tracking-wide">{title}</p>
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <p className="text-[24px] font-semibold tracking-tight text-foreground leading-none">{value}</p>
      <p className="text-[12px] text-muted-foreground mt-1.5">{subtitle}</p>
    </CardContent>
  );

  if (href) {
    return (
      <Card className="motion-soft cursor-pointer hover:bg-muted/50 hover:-translate-y-[2px] transition-all duration-200 shadow-sm">
        <Link to={href}>
          {content}
        </Link>
      </Card>
    );
  }

  return (
    <Card className="motion-soft shadow-sm">
      {content}
    </Card>
  );
}
function titleizeStatus(value?: string | null) {
  if (!value) return 'Checking';
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}
function StatusItem({ label, value, detail, tone = 'ok', icon: Icon }: {
  label: string;
  value: string;
  detail: string;
  tone?: 'ok' | 'warn' | 'bad' | 'idle';
  icon: React.ElementType;
}) {
  const dotClass = {
    ok: 'bg-emerald-500',
    warn: 'bg-amber-500',
    bad: 'bg-destructive',
    idle: 'bg-muted-foreground',
  }[tone];
  return (
    <Card>
      <CardContent className="flex h-full items-start gap-3 p-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-border bg-background">
          <Icon className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${dotClass}`} />
            <p className="truncate text-[12px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
          </div>
          <p className="mt-1 text-[15px] font-semibold text-foreground">{value}</p>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{detail}</p>
        </div>
      </CardContent>
    </Card>
  );
}
function LaunchChecklist() {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {launchSteps.map((step) => (
        <Card key={step.title} className="border-primary/15 bg-primary/[0.03]">
          <CardContent className="flex h-full flex-col gap-4 p-4">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-border bg-background">
                <step.icon className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="min-w-0">
                <p className="text-[13px] font-semibold text-foreground">{step.title}</p>
                <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{step.description}</p>
              </div>
            </div>
            <Button asChild variant="ghost" size="sm" className="mt-auto h-8 justify-between px-2 text-[12px]">
              <Link to={step.href}>
                Open
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
type ReadinessItemState = 'done' | 'review' | 'attention';
function ReadinessItem({ label, detail, state }: {
  label: string;
  detail: string;
  state: ReadinessItemState;
}) {
  const styles = {
    done: {
      icon: CheckCircle2,
      className: 'border-emerald-500/25 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300',
      iconClassName: 'text-emerald-600 dark:text-emerald-400',
      label: 'Ready',
    },
    review: {
      icon: AlertTriangle,
      className: 'border-amber-500/25 bg-amber-500/5 text-amber-800 dark:text-amber-200',
      iconClassName: 'text-amber-600 dark:text-amber-300',
      label: 'Review',
    },
    attention: {
      icon: AlertTriangle,
      className: 'border-destructive/25 bg-destructive/5 text-destructive',
      iconClassName: 'text-destructive',
      label: 'Needs action',
    },
  }[state];
  const Icon = styles.icon;
  return (
    <div className="flex items-start gap-3 rounded-md border bg-background p-3">
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${styles.iconClassName}`} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[13px] font-medium text-foreground">{label}</p>
          <span className={`rounded border px-2 py-0.5 text-[11px] font-medium ${styles.className}`}>
            {styles.label}
          </span>
        </div>
        <p className="mt-1 text-[12px] leading-5 text-muted-foreground">{detail}</p>
      </div>
    </div>
  );
}

function PilotReadinessPanel({
  hasAwaLink,
  awaPublicUrl,
  toolsEnabled,
  toolsChecked,
  whatsAppValue,
  whatsAppDetail,
  billingMode,
  billingStatus,
}: {
  hasAwaLink: boolean;
  awaPublicUrl?: string;
  toolsEnabled: number;
  toolsChecked: boolean;
  whatsAppValue: string;
  whatsAppDetail: string;
  billingMode?: string;
  billingStatus: string;
}) {
  const setupNeedsReview = toolsChecked && toolsEnabled === 0;
  const primaryAction: {
    href: string;
    label: string;
    icon: React.ElementType;
    external?: boolean;
  } = !hasAwaLink
    ? { href: '/dashboard/awa-chat', label: 'Create AWA Chat link', icon: MessagesSquare }
    : setupNeedsReview
      ? { href: '/dashboard/config', label: 'Review Business Setup', icon: Settings }
      : { href: awaPublicUrl || '/dashboard/awa-chat', label: 'Test / Open AWA Chat', icon: ExternalLink, external: Boolean(awaPublicUrl) };
  const PrimaryIcon = primaryAction.icon;
  const readinessItems = [
    {
      label: 'Business setup reviewed',
      detail: setupNeedsReview
        ? 'No enabled workflow tools were detected. Review services, hours, FAQs, and workflow setup before sharing.'
        : 'Confirm profile, hours, services, FAQs, and verification settings in Business Setup.',
      state: setupNeedsReview ? 'attention' : 'review',
    },
    {
      label: 'Knowledge reviewed',
      detail: 'Remove personal IDs, confidential customer data, irrelevant uploads, and sensitive records before pilot users ask AWA questions.',
      state: 'review',
    },
    {
      label: 'AWA Chat link active',
      detail: hasAwaLink ? 'A public customer link exists. Test it yourself before sharing.' : 'Create a public browser link before inviting friendly pilot users.',
      state: hasAwaLink ? 'done' : 'attention',
    },
    {
      label: 'Workflow tools reviewed',
      detail: toolsChecked
        ? `${toolsEnabled} enabled workflow tool${toolsEnabled === 1 ? '' : 's'} detected. Confirm each tool should be usable by AWA.`
        : 'Checking enabled workflow tools.',
      state: toolsChecked && toolsEnabled > 0 ? 'done' : toolsChecked ? 'attention' : 'review',
    },
    {
      label: 'WhatsApp optional',
      detail: `${whatsAppValue}: ${whatsAppDetail}. AWA Chat can be piloted without live WhatsApp.`,
      state: whatsAppValue === 'Connected' ? 'done' : 'review',
    },
    {
      label: 'Billing mode understood',
      detail: billingMode === 'mock'
        ? 'Mock billing is active. Do not treat plan actions as real charges.'
        : `Billing status is ${titleizeStatus(billingStatus)}. Confirm billing state before any paid pilot.`,
      state: billingMode === 'mock' || billingStatus === 'trial' ? 'review' : 'done',
    },
  ] satisfies Array<{ label: string; detail: string; state: ReadinessItemState }>;
  return (
    <Card className="border-primary/20 bg-primary/[0.03]">
      <CardContent className="space-y-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-[12px] font-medium uppercase tracking-wide text-primary">Friendly pilot readiness</p>
            <h2 className="mt-1 text-[18px] font-semibold text-foreground">Know what to check before sharing AWA Chat</h2>
            <p className="mt-1 max-w-3xl text-[13px] leading-6 text-muted-foreground">
              This workspace can be prepared for an AWA Chat-first pilot. Review setup, Knowledge, workflow tools, channel state, and billing mode before sending the public link to customers.
            </p>
          </div>
          {primaryAction.external ? (
            <div className="flex w-full flex-col gap-2 shrink-0 sm:w-auto sm:flex-row">
              <Button asChild className="w-full sm:w-auto">
                <a href={primaryAction.href} target="_blank" rel="noreferrer">
                  <PrimaryIcon className="h-3.5 w-3.5" />
                  {primaryAction.label}
                </a>
              </Button>
              <CopyButton
                variant="outline"
                className="w-full sm:w-auto"
                value={primaryAction.href}
              >
                Copy Link
              </CopyButton>
            </div>
          ) : (
            <Button asChild className="w-full shrink-0 sm:w-auto">
              <Link to={primaryAction.href}>
                <PrimaryIcon className="h-3.5 w-3.5" />
                {primaryAction.label}
              </Link>
            </Button>
          )}
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          {readinessItems.map((item) => (
            <ReadinessItem key={item.label} {...item} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
export function OverviewPage() {
  const { user } = useCurrentUser();
  const { data: escalationsData, isLoading: isEscalationsLoading } = useEscalationsList('');
  const { data: bookingsData, isLoading: isBookingsLoading } = useBookingsList(undefined, 'all');
  const { data: ordersData, isLoading: isOrdersLoading } = useOrdersList('new');
  const { data: leadsData, isLoading: isLeadsLoading } = useLeadsList();

  const { data, isLoading, isError: isCostError, error: costError } = useCostMetrics();
  const { data: billing, isLoading: isBillingLoading, isError: isBillingError } = useBillingStatus();
  const { data: health, isError: isHealthError, isFetching: isHealthFetching } = useSystemHealth();
  const { data: whatsappAccounts, isLoading: isWhatsAppLoading, isError: isWhatsAppError } = useWhatsAppAccounts();
  const { data: awaLinks } = useAwaChatLinks();
  const { data: toolsData, isFetched: toolsFetched } = useTenantTools();

  const isMetricsLoading = isEscalationsLoading || isBookingsLoading || isOrdersLoading || isLeadsLoading;
  const pageLoading = isLoading || isBillingLoading || isMetricsLoading;

  if (pageLoading) {
    return (
      <div className="space-y-5">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="h-[110px] rounded-lg bg-card border border-border animate-pulse" />
          ))}
        </div>
        <div className="h-[380px] rounded-lg bg-card border border-border animate-pulse" />
      </div>
    );
  }
  const summary = data || { items: [], total_spend: 0, total_tokens: 0, total_messages: 0 };

  const escalations = escalationsData?.pages.flatMap((page) => page.items) || [];
  const escalationsCount = escalations.length;

  const bookings = bookingsData?.pages.flatMap((page) => page.items) || [];
  const todayLocal = new Date();
  const year = todayLocal.getFullYear();
  const month = String(todayLocal.getMonth() + 1).padStart(2, '0');
  const day = String(todayLocal.getDate()).padStart(2, '0');
  const todayStr = `${year}-${month}-${day}`;
  const bookingsCount = bookings.filter(b => b.booking_date === todayStr && b.status !== 'cancelled').length;

  const orders = ordersData?.pages.flatMap((page) => page.items) || [];
  const ordersCount = orders.filter(o => o.status === 'new').length;

  const leads = leadsData?.pages.flatMap((page) => page.items) || [];
  const leadsCount = leads.length;
  const costErrorMessage = getApiErrorMessage(costError, 'Cost metrics are unavailable.');
  const tenantBilling = billing?.tenant_billing;
  const planName = billing?.current_plan?.name || tenantBilling?.plan_code || (tenantBilling?.status === 'trial' ? 'Free Trial' : 'No Plan');
  const billingStatus = tenantBilling?.status || 'trial';
  const monthlyCredits = billing?.plan_policy?.monthly_credits ?? null;
  const usedCredits = Number(billing?.usage?.month_credits_used || 0);
  const remainingCredits = monthlyCredits == null ? null : Math.max(monthlyCredits - usedCredits, 0);
  const creditPercent = monthlyCredits ? Math.round((usedCredits / monthlyCredits) * 100) : 0;
  const whatsappAccountCount = whatsappAccounts?.length || 0;
  const connectedAccounts = whatsappAccounts?.filter((account) => account.status === 'connected') || [];
  const activeAwaLink = awaLinks?.find((link) => (link.is_publicly_accessible ?? link.status === 'active') && link.public_status !== 'needs_rotation') || awaLinks?.[0];
  const hasAwaLink = Boolean(activeAwaLink && (activeAwaLink.is_publicly_accessible ?? activeAwaLink.status === 'active'));
  const enabledWorkflowTools = toolsData?.tools.filter((tool) => tool.enabled && tool.plan_allowed).length ?? 0;
  const hasDisconnectedWhatsAppAccount = whatsappAccountCount > 0 && connectedAccounts.length === 0;
  const whatsappMode = (import.meta.env.VITE_WHATSAPP_MODE || import.meta.env.VITE_AWA_INTEGRATION_MODE || '').toLowerCase();
  const whatsappStatusValue = isWhatsAppLoading
    ? 'Checking'
    : connectedAccounts.length > 0
      ? 'Connected'
      : hasDisconnectedWhatsAppAccount
        ? 'Account disconnected'
        : 'No account configured';
  const whatsappStatusDetail = connectedAccounts.length > 0
    ? `${connectedAccounts.length} active number${connectedAccounts.length === 1 ? '' : 's'}`
    : hasDisconnectedWhatsAppAccount
      ? whatsappMode === 'test'
        ? 'Test setup mode, reconnect before sharing WhatsApp'
        : 'Reconnect the business number'
      : whatsappMode === 'test'
        ? 'Test setup mode, no account yet'
        : 'Connect a business number';
  const queueDepth = health?.queue_depth ?? 0;
  const queueHealthy = !health || queueDepth === 0 || ((health.queue_alert === 'normal' || health.queue_alert === 'ok') && queueDepth < 100);
  const creditTone = remainingCredits == null ? 'idle' : creditPercent >= 95 ? 'bad' : creditPercent >= 80 ? 'warn' : 'ok';
  const billingTone = isBillingError || billingStatus === 'suspended' || billingStatus === 'expired' ? 'bad' : billingStatus === 'trial' ? 'warn' : 'ok';
  const whatsappTone = isWhatsAppError ? 'bad' : connectedAccounts.length > 0 ? 'ok' : whatsappAccountCount > 0 ? 'bad' : 'warn';

  const creditsSubtitle = monthlyCredits == null
    ? 'Plan limit not configured'
    : `${usedCredits.toLocaleString('en-IN')} used of ${monthlyCredits.toLocaleString('en-IN')}`;
  return (
    <div className="flex-1 space-y-5 flex flex-col h-full">
      <PageHeader
        title="Overview"
        description="Business metrics, quick actions, and workspace setup."
        actions={
          <Button asChild variant="outline" size="sm" className="w-full sm:w-auto">
            <Link to="/dashboard/billing">
              <IndianRupee className="h-3.5 w-3.5" />
              Billing
            </Link>
          </Button>
        }
      />
      <PilotReadinessPanel
        hasAwaLink={hasAwaLink}
        awaPublicUrl={activeAwaLink?.public_url}
        toolsEnabled={enabledWorkflowTools}
        toolsChecked={toolsFetched}
        whatsAppValue={whatsappStatusValue}
        whatsAppDetail={whatsappStatusDetail}
        billingMode={billing?.billing_mode}
        billingStatus={billingStatus}
      />
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Chats needing reply"
          value={escalationsCount.toLocaleString('en-IN')}
          subtitle={escalationsCount === 1 ? "1 chat requires team handoff" : `${escalationsCount} chats require team handoff`}
          icon={LifeBuoy}
          href="/dashboard/escalations"
        />
        <StatCard
          title="Appointments Today"
          value={bookingsCount.toLocaleString('en-IN')}
          subtitle={bookingsCount === 1 ? "1 booking scheduled for today" : `${bookingsCount} bookings scheduled for today`}
          icon={CalendarCheck}
          href="/dashboard/bookings"
        />
        <StatCard
          title="Pending Orders"
          value={ordersCount.toLocaleString('en-IN')}
          subtitle={ordersCount === 1 ? "1 order awaiting fulfillment" : `${ordersCount} orders awaiting fulfillment`}
          icon={ShoppingBag}
          href="/dashboard/orders"
        />
        <StatCard
          title="Recent Leads"
          value={leadsCount.toLocaleString('en-IN')}
          subtitle={leadsCount === 1 ? "1 new customer lead captured" : `${leadsCount} customer leads captured`}
          icon={Users}
          href="/dashboard/customers"
        />
      </div>
      {isCostError && (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-medium">Cost metrics unavailable</p>
              <p className="mt-1 leading-relaxed">{costErrorMessage}</p>
            </div>
          </div>
        </div>
      )}
      {user?.role === 'super_admin' && (
        <div>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <h3 className="text-[14px] font-semibold text-foreground">Live Status (Super Admin Only)</h3>
              <p className="text-[12px] text-muted-foreground">Auto-refreshing health from existing operational signals.</p>
            </div>
            <RefreshCw className={`h-4 w-4 text-muted-foreground ${isHealthFetching ? 'animate-spin' : ''}`} />
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <StatusItem
              label="Platform"
              value={isHealthError ? 'Degraded' : titleizeStatus(health?.status)}
              detail={health?.version ? `Version ${health.version}` : 'API health endpoint'}
              tone={isHealthError || health?.status === 'unhealthy' ? 'bad' : health?.status === 'degraded' ? 'warn' : 'ok'}
              icon={Activity}
            />
            <StatusItem
              label="Database"
              value={titleizeStatus(health?.services.database)}
              detail="Tenant data and conversations"
              tone={health?.services.database === 'ok' || health?.services.database === 'healthy' ? 'ok' : isHealthError ? 'bad' : 'idle'}
              icon={Database}
            />
            <StatusItem
              label="Queue"
              value={queueHealthy ? 'Ready' : 'Busy'}
              detail={`${queueDepth.toLocaleString('en-IN')} messages waiting`}
              tone={queueHealthy ? 'ok' : 'warn'}
              icon={PlugZap}
            />
            <StatusItem
              label="WhatsApp"
              value={whatsappStatusValue}
              detail={whatsappStatusDetail}
              tone={whatsappTone}
              icon={Phone}
            />
            <StatusItem
              label="Billing"
              value={isBillingLoading ? 'Checking' : titleizeStatus(billingStatus)}
              detail={planName}
              tone={billingTone}
              icon={CreditCard}
            />
            <StatusItem
              label="Credits"
              value={remainingCredits == null ? 'Configured' : remainingCredits > 0 ? 'Available' : 'Recharge'}
              detail={creditsSubtitle}
              tone={creditTone}
              icon={WalletCards}
            />
          </div>
        </div>
      )}
      <LaunchChecklist />
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <CostChart data={summary.items} />
      </div>
    </div>
  );
}
