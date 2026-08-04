import { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { CheckCircle2, AlertTriangle, Loader2, IndianRupee, HelpCircle } from 'lucide-react';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import toast from 'react-hot-toast';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { PageHeader } from '@/components/shared/PageHeader';
import { fetchBillingStatus, useBillingStatus, type BillingPlan, type BillingStatusResponse } from '@/features/billing/api';
function parsePlanFeatures(featuresJson: BillingPlan['features_json']) {
  if (!featuresJson) return [];
  if (Array.isArray(featuresJson)) return featuresJson.filter((f): f is string => typeof f === 'string');
  try { const p = JSON.parse(featuresJson); return Array.isArray(p) ? p.filter((f): f is string => typeof f === 'string') : []; }
  catch { return []; }
}
function formatLimit(value?: number | null) { return value == null ? 'Custom' : value.toLocaleString(); }
function usagePercent(current = 0, limit?: number | null) { return !limit ? 0 : Math.min(100, Math.round((current / limit) * 100)); }
function delay(ms: number) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function isPlanActive(status: BillingStatusResponse, plan: BillingPlan) {
  const tenant = status.tenant_billing;
  return tenant.status === 'active' && (tenant.plan_code === plan.plan_code || tenant.plan_code === plan.razorpay_plan_id);
}
export function BillingPage() {
  const queryClient = useQueryClient();
  const [upgradingProvider, setUpgradingProvider] = useState<string | null>(null);
  const [pendingActivationPlan, setPendingActivationPlan] = useState<string | null>(null);
  const [pendingCheckoutPlan, setPendingCheckoutPlan] = useState<BillingPlan | null>(null);
  const { data, isLoading } = useBillingStatus();
  const tenant = data?.tenant_billing;
  const plans = data?.available_plans || [];
  const policy = data?.plan_policy;
  const usage = data?.usage || {};
  const isMockBilling = data?.mock_billing || data?.billing_mode === 'mock';
  useEffect(() => {
    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.async = true;
    document.body.appendChild(script);
    return () => { document.body.removeChild(script); };
  }, []);
  const waitForActivation = async (plan: BillingPlan) => {
    setPendingActivationPlan(plan.id);
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await delay(2000);
      const latest = await queryClient.fetchQuery({
        queryKey: ['billing_status'],
        queryFn: fetchBillingStatus
      });
      if (isPlanActive(latest, plan)) {
        toast.success('Subscription active.');
        setPendingActivationPlan(null);
        return;
      }
    }
    setPendingActivationPlan(null);
    toast('Payment received. Activation is still waiting for Razorpay confirmation.');
  };
  const handleUpgrade = async (plan: BillingPlan) => {
    setUpgradingProvider(plan.id);
    try {
      const res = await api.post<{ subscription_id: string; customer_id?: string | null; activation_status: 'active' | 'pending_webhook'; mock?: boolean }>('/api/v1/billing/subscribe', { plan_id: plan.razorpay_plan_id });
      if (res.data.activation_status === 'active') {
        toast.success(res.data.mock || isMockBilling ? 'Mock subscription activated.' : 'Subscription active.');
        queryClient.invalidateQueries({ queryKey: ['billing_status'] });
        return;
      }
      const Razorpay = window.Razorpay;
      if (!Razorpay) throw new Error('Checkout unavailable.');
      const razorpayKey = import.meta.env.VITE_RAZORPAY_KEY_ID;
      if (!razorpayKey) throw new Error('Checkout is not configured.');
      const rzp = new Razorpay({
        key: razorpayKey,
        subscription_id: res.data.subscription_id,
        name: 'AWA', description: `Upgrade to ${plan.name}`,
        handler: async () => {
          if (res.data.activation_status === 'active') {
            toast.success('Subscription active.');
            queryClient.invalidateQueries({ queryKey: ['billing_status'] });
            return;
          }
          toast.success('Payment authorized. Waiting for Razorpay confirmation.');
          await waitForActivation(plan);
        },
        theme: { color: '#171717' }
      });
      rzp.open();
    } catch (error: unknown) { toast.error(`Checkout failed: ${getApiErrorMessage(error)}`); }
    finally { setUpgradingProvider(null); }
  };
  if (isLoading) return (
    <div className="space-y-5 max-w-4xl mx-auto">
      {[1,2].map(i => <div key={i} className="h-32 rounded-md bg-card border border-border animate-pulse" />)}
    </div>
  );
  const isTrial = tenant?.status === 'trial';
  const isSuspended = tenant?.status === 'suspended' || tenant?.status === 'expired';
  const isActive = tenant?.status === 'active';
  return (
    <div className="space-y-5 max-w-4xl mx-auto">
      <PageHeader
        title="Billing"
        description="Manage your subscription."
      />
      {isSuspended && (
        <div className="border border-destructive/20 bg-destructive/5 p-3 rounded-md flex gap-2.5 text-[13px]">
          <AlertTriangle className="h-4 w-4 text-destructive mt-0.5" />
          <div><p className="font-medium text-destructive">Subscription expired</p><p className="text-destructive/80 text-[12px] mt-0.5">Subscribe to resume your AI agent.</p></div>
        </div>
      )}
      {isTrial && (
        <div className="border border-blue-500/20 bg-blue-500/5 p-3 rounded-md flex gap-2.5 text-[13px]">
          <CheckCircle2 className="h-4 w-4 text-blue-500 mt-0.5" />
          <div><p className="font-medium text-blue-500">Free Trial</p><p className="text-[12px] text-muted-foreground mt-0.5">Expires {tenant?.trial_ends_at ? new Date(tenant.trial_ends_at).toLocaleDateString() : 'soon'}. Subscribe for uninterrupted service.</p></div>
        </div>
      )}
      {isMockBilling && (
        <div className="border border-amber-500/25 bg-amber-500/10 p-3 rounded-md flex gap-2.5 text-[13px] text-amber-900 dark:text-amber-100">
          <AlertTriangle className="h-4 w-4 mt-0.5" />
          <div>
            <p className="font-medium">Mock billing mode - no real charges</p>
            <p className="text-[12px] mt-0.5 opacity-90">
              This dashboard is using simulated billing for local or pilot testing. Plan changes do not open Razorpay checkout and do not prove paid-production readiness.
            </p>
          </div>
        </div>
      )}
      {policy && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Messages', val: usage.month_message_count || 0, max: policy.monthly_messages },
            { label: 'Credits', val: Number(usage.month_credits_used || 0), max: policy.monthly_credits },
            { label: 'AI Tokens', val: usage.month_ai_tokens_used || 0, max: policy.monthly_ai_tokens },
            { label: 'Tools', val: usage.month_tool_action_count || 0, max: null },
          ].map((m) => (
            <Card key={m.label}>
              <CardContent className="pt-4 pb-3 px-4">
                <div className="flex items-center gap-1.5 mb-2">
                  <p className="text-[11px] text-muted-foreground font-medium uppercase tracking-wide">{m.label}</p>
                  {m.label === 'Credits' && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button type="button" className="text-muted-foreground hover:text-foreground inline-flex items-center justify-center p-0.5" aria-label="Credits info">
                          <HelpCircle className="h-3.5 w-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p className="text-xs max-w-[220px] leading-relaxed">
                          1 Credit = 1 AI Action (e.g., booking an appointment or checking an order).
                        </p>
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
                <p className="text-[20px] font-semibold text-foreground mt-1 leading-none">{m.val.toLocaleString()}</p>
                {m.max != null && (
                  <>
                    <p className="text-[11px] text-muted-foreground mt-1">of {formatLimit(m.max)}</p>
                    <div className="h-1 rounded-full bg-secondary mt-2 overflow-hidden">
                      <div className="h-full bg-foreground/30 rounded-full" style={{ width: `${usagePercent(m.val, m.max)}%` }} />
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {plans.map((plan) => {
          const isCurrentPlan = isActive && (tenant.plan_code === plan.plan_code || tenant.plan_code === plan.razorpay_plan_id);
          const isConfirming = pendingActivationPlan === plan.id;
          const planActionHelpId = `billing-plan-action-help-${plan.id}`;
          const planActionHelp = isCurrentPlan
            ? 'This is the active plan. Choose another plan to change the subscription.'
            : isConfirming
              ? 'Waiting for billing provider confirmation before another change can start.'
              : upgradingProvider === plan.id
                ? 'Starting this subscription change.'
                : isMockBilling
                  ? 'Activates this plan in mock billing mode without a real charge.'
                  : 'Starts checkout for this plan.';
          return (
            <Card key={plan.id} className={isCurrentPlan ? 'border-foreground/30' : ''}>
              {isCurrentPlan && <div className="text-center text-[10px] font-semibold uppercase tracking-wider text-muted-foreground py-1.5 border-b border-border">Current Plan</div>}
              <CardHeader className="text-center pb-2">
                <CardTitle className="text-[15px]">{plan.name}</CardTitle>
                <div className="mt-3 flex items-center justify-center">
                  <IndianRupee className="w-4 h-4 text-muted-foreground" />
                  <span className="text-[32px] font-bold tracking-tight">{(plan.monthly_price_cents / 100).toFixed(0)}</span>
                  <span className="text-[13px] text-muted-foreground ml-0.5">/mo</span>
                </div>
              </CardHeader>
              <CardContent className="flex-1 flex flex-col">
                <ul className="mt-3 space-y-2 flex-1 mb-5">
                  {parsePlanFeatures(plan.features_json).map((feature, i) => (
                    <li key={i} className="flex items-start gap-2 text-[12px] text-muted-foreground">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 mt-[2px] shrink-0" />
                      {feature}
                    </li>
                  ))}
                </ul>
                <Button
                  onClick={() => setPendingCheckoutPlan(plan)}
                  disabled={upgradingProvider === plan.id || isConfirming || isCurrentPlan}
                  variant={isCurrentPlan ? 'outline' : 'default'}
                  className="w-full text-[13px]"
                  aria-describedby={planActionHelpId}
                  title={planActionHelp}
                >
                  {(upgradingProvider === plan.id || isConfirming) && <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />}
                  {isCurrentPlan ? 'Active' : isConfirming ? 'Confirming' : isMockBilling ? 'Activate Mock' : 'Subscribe'}
                </Button>
                <p id={planActionHelpId} className="mt-2 min-h-8 text-center text-[11px] leading-4 text-muted-foreground">
                  {planActionHelp}
                </p>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <Dialog open={!!pendingCheckoutPlan} onOpenChange={(open) => !open && setPendingCheckoutPlan(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{isMockBilling ? 'Activate mock subscription?' : 'Start subscription checkout?'}</DialogTitle>
            <DialogDescription>
              {isMockBilling
                ? `This activates ${pendingCheckoutPlan?.name} in mock billing mode for this business. No Razorpay checkout opens and no real charge occurs.`
                : `This opens Razorpay checkout for ${pendingCheckoutPlan?.name}. Continue only if you want to create a subscription for this business.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingCheckoutPlan(null)} disabled={!!upgradingProvider}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => {
                if (pendingCheckoutPlan) {
                  handleUpgrade(pendingCheckoutPlan);
                }
                setPendingCheckoutPlan(null);
              }}
              disabled={!!upgradingProvider}
            >
              {upgradingProvider && <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />}
              {isMockBilling ? 'Activate Mock Subscription' : 'Continue to Checkout'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}