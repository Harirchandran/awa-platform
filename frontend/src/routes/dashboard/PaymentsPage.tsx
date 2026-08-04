import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useCustomersList } from '@/features/customers/api';
import { useOrdersList, formatMoney } from '@/features/orders/api';
import { useBookingsList } from '@/features/bookings/api';
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  CreditCard,
  ExternalLink,
  Loader2,
  Plus,
  ReceiptText,
  Search,
  Send,
  Wallet,
  XCircle,
} from 'lucide-react';
import {
  formatPaymentMoney,
  type CreatePaymentPayload,
  type PaymentAction,
  type PaymentProvider,
  type PaymentRequest,
  type PaymentStatus,
  type PaymentType,
  useActionPayment,
  useCreatePayment,
  usePaymentDetail,
  usePaymentEvents,
  usePaymentsList,
  usePaymentMetrics,
} from '@/features/payments/api';
import { useTenantConfig, useUpdateConfig } from '@/features/config/api';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import { getApiErrorMessage } from '@/lib/api-errors';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetFooter } from '@/components/ui/sheet';
import { useMediaQuery } from '@/hooks/use-media-query';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DetailSheetSkeleton, TableSkeleton } from '@/components/ui/loading-states';
import { Textarea } from '@/components/ui/textarea';
import type { TenantConfig } from '@/types/config.types';
import { cn } from '@/lib/utils';
const PAYMENT_STATUS_COPY: Record<PaymentStatus, string> = {
  draft: 'Draft',
  sent: 'Link sent',
  viewed: 'Viewed',
  paid: 'Paid / collected',
  expired: 'Expired',
  cancelled: 'Cancelled',
  failed: 'Failed',
  refunded: 'Refunded',
};
const PAYMENT_TYPE_COPY: Record<PaymentType, string> = {
  booking_deposit: 'Booking deposit',
  booking_full_payment: 'Booking full payment',
  order_payment: 'Order payment',
  custom_payment: 'Custom payment',
  balance_due: 'Balance due',
};
const PROVIDER_COPY: Record<PaymentProvider, string> = {
  manual: 'Manual record',
  external: 'External link',
  razorpay: 'Razorpay',
  upi: 'UPI',
  cash: 'Cash',
  bank_transfer: 'Bank transfer',
};
type CreateForm = {
  type: PaymentType;
  provider: PaymentProvider;
  amount: string;
  currency: string;
  customer_id: string;
  booking_id: string;
  order_id: string;
  payment_url: string;
  expires_at: string;
  note: string;
};
const emptyCreateForm: CreateForm = {
  type: 'custom_payment',
  provider: 'manual',
  amount: '',
  currency: 'INR',
  customer_id: '',
  booking_id: '',
  order_id: '',
  payment_url: '',
  expires_at: '',
  note: '',
};
function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() || `payment-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
type PendingAction = {
  payment: PaymentRequest;
  action: PaymentAction;
  label: string;
  tone?: 'default' | 'destructive';
};
function toOptionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}
function toNumber(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}
function formatDateTime(value?: string | null): string {
  if (!value) return 'Not set';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Not set' : parsed.toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}
function statusTone(status: PaymentStatus): string {
  switch (status) {
    case 'paid': return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'sent':
    case 'viewed': return 'border-sky-200 bg-sky-50 text-sky-700';
    case 'failed':
    case 'expired': return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'cancelled': return 'border-border bg-muted text-muted-foreground';
    case 'refunded': return 'border-violet-200 bg-violet-50 text-violet-700';
    default: return 'border-border bg-background text-muted-foreground';
  }
}
function StatusPill({ status }: { status: PaymentStatus }) {
  return (
    <span className={cn('inline-flex h-6 items-center rounded-full border px-2 text-[11px] font-medium', statusTone(status))}>
      {PAYMENT_STATUS_COPY[status]}
    </span>
  );
}
function Metric({ label, value, helper }: { label: string; value: string | number; helper?: string }) {
  return (
    <div className="rounded-md border bg-card px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-normal text-muted-foreground">{label}</p>
      <p className="mt-1 text-[20px] font-semibold text-foreground">{value}</p>
      {helper && <p className="mt-1 truncate text-[11px] text-muted-foreground">{helper}</p>}
    </div>
  );
}
function PaymentsDisabledState({ config, canEnable, lockedReason }: { config: TenantConfig; canEnable: boolean; lockedReason?: string }) {
  const updateConfig = useUpdateConfig();
  const enablePayments = () => {
    const updatedConfig: TenantConfig = {
      ...config,
      modules: {
        ...config.modules,
        payments: {
          ...(config.modules?.payments || { fields: [] }),
          enabled: true,
          fields: config.modules?.payments?.fields || [],
        },
      },
    };
    updateConfig.mutate(updatedConfig, {
      onSuccess: () => toast.success('Payments enabled'),
      onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not enable payments')),
    });
  };
  return (
    <div className="flex min-h-[520px] items-center justify-center">
      <div className="max-w-xl rounded-md border bg-card p-6 text-center sm:p-8">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
          <Wallet className="h-5 w-5 text-muted-foreground" />
        </div>
        <h2 className="mt-4 text-[18px] font-semibold text-foreground">Payments are turned off</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          Enable payments when staff should create deposits, order payment requests, and manual collection records. Online collection still requires a configured live provider such as Razorpay.
        </p>
        {canEnable ? (
          <Button type="button" className="mt-5" onClick={enablePayments} disabled={updateConfig.isPending}>
            {updateConfig.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Enable Payments
          </Button>
        ) : (
          <p className="mt-5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[13px] text-amber-800 dark:text-amber-100">
            {lockedReason || 'Payments are not available on your current plan.'}
          </p>
        )}
      </div>
    </div>
  );
}
function PaymentCreateDialog({
  open,
  form,
  pending,
  onChange,
  onClose,
  onSave,
}: {
  open: boolean;
  form: CreateForm;
  pending: boolean;
  onChange: (updates: Partial<CreateForm>) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const isDesktop = useMediaQuery('(min-width: 768px)');

  const customersQuery = useCustomersList('');
  const ordersQuery = useOrdersList();
  const bookingsQuery = useBookingsList();

  const customers = customersQuery.data?.pages.flatMap((p) => p.items) || [];
  const orders = ordersQuery.data?.pages.flatMap((p) => p.items) || [];
  const bookings = bookingsQuery.data?.pages.flatMap((p) => p.items) || [];

  const customersLoading = customersQuery.isLoading;
  const showCustomersEmpty = !customersLoading && customers.length === 0;

  const ordersLoading = ordersQuery.isLoading;
  const showOrdersEmpty = !ordersLoading && orders.length === 0;

  const bookingsLoading = bookingsQuery.isLoading;
  const showBookingsEmpty = !bookingsLoading && bookings.length === 0;

  const formContent = (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-2">
        <Label>Type</Label>
        <Select value={form.type} onValueChange={(value) => onChange({ type: value as PaymentType })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {Object.entries(PAYMENT_TYPE_COPY).map(([value, label]) => (
              <SelectItem key={value} value={value}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label>Provider</Label>
        <Select value={form.provider} onValueChange={(value) => onChange({ provider: value as PaymentProvider })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {Object.entries(PROVIDER_COPY).map(([value, label]) => (
              <SelectItem key={value} value={value}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_88px] gap-2">
        <div className="space-y-2">
          <Label htmlFor="payment-amount">Amount</Label>
          <Input
            id="payment-amount"
            type="number"
            min="0"
            step="0.01"
            value={form.amount}
            onChange={(event) => onChange({ amount: event.target.value })}
            placeholder="Order total if blank"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="payment-currency">Currency</Label>
          <Input
            id="payment-currency"
            value={form.currency}
            onChange={(event) => onChange({ currency: event.target.value.toUpperCase() })}
            maxLength={8}
          />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="payment-expires">Expires at</Label>
        <Input
          id="payment-expires"
          type="datetime-local"
          value={form.expires_at}
          onChange={(event) => onChange({ expires_at: event.target.value })}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="payment-customer">Customer</Label>
        <Select
          value={form.customer_id || 'none'}
          onValueChange={(val) => onChange({ customer_id: val === 'none' ? '' : val })}
          disabled={customersLoading || showCustomersEmpty}
        >
          <SelectTrigger id="payment-customer">
            <SelectValue placeholder={
              customersLoading
                ? "Loading customers..."
                : showCustomersEmpty
                  ? "No recent customers available"
                  : "Select customer"
            } />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">None / Clear</SelectItem>
            {customers.map((customer) => (
              <SelectItem key={customer.id} value={customer.id}>
                {customer.name} {customer.phone_number ? `(${customer.phone_number})` : ''}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="payment-order">Order</Label>
        <Select
          value={form.order_id || 'none'}
          onValueChange={(val) => onChange({ order_id: val === 'none' ? '' : val })}
          disabled={ordersLoading || showOrdersEmpty}
        >
          <SelectTrigger id="payment-order">
            <SelectValue placeholder={
              ordersLoading
                ? "Loading orders..."
                : showOrdersEmpty
                  ? "No recent orders available"
                  : "Select order"
            } />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">None / Clear</SelectItem>
            {orders.map((order) => (
              <SelectItem key={order.id} value={order.id}>
                Order #{order.id.slice(0, 8)} ({formatMoney(order.total_amount)})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="payment-booking">Booking</Label>
        <Select
          value={form.booking_id || 'none'}
          onValueChange={(val) => onChange({ booking_id: val === 'none' ? '' : val })}
          disabled={bookingsLoading || showBookingsEmpty}
        >
          <SelectTrigger id="payment-booking">
            <SelectValue placeholder={
              bookingsLoading
                ? "Loading bookings..."
                : showBookingsEmpty
                  ? "No recent bookings available"
                  : "Select booking"
            } />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">None / Clear</SelectItem>
            {bookings.map((booking) => (
              <SelectItem key={booking.id} value={booking.id}>
                {booking.service_name || booking.service_type} on {formatDateTime(booking.booking_datetime)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="payment-url">Payment URL</Label>
        <Input id="payment-url" value={form.payment_url} onChange={(event) => onChange({ payment_url: event.target.value })} />
      </div>
      <div className="space-y-2 sm:col-span-2">
        <Label htmlFor="payment-note">Internal note</Label>
        <Textarea id="payment-note" rows={3} value={form.note} onChange={(event) => onChange({ note: event.target.value })} />
      </div>
    </div>
  );

  const footerContent = (
    <>
      <Button type="button" variant="ghost" onClick={onClose} disabled={pending}>Cancel</Button>
      <Button type="button" onClick={onSave} disabled={pending}>
        {pending && <Loader2 className="h-4 w-4 animate-spin" />}
        Create Request
      </Button>
    </>
  );

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Create payment request</DialogTitle>
            <DialogDescription>
              Link a request to a customer, booking, or order. Provider `Manual` records staff-tracked collection only; use a real payment URL/provider before treating a link as online payment.
            </DialogDescription>
          </DialogHeader>
          {formContent}
          <DialogFooter>
            {footerContent}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Sheet open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <SheetContent side="bottom" className="max-h-[85vh] overflow-y-auto rounded-t-xl p-4 sm:p-6">
        <SheetHeader className="text-left mb-4">
          <SheetTitle>Create payment request</SheetTitle>
          <SheetDescription>
            Link a request to a customer, booking, or order. Provider `Manual` records staff-tracked collection only; use a real payment URL/provider before treating a link as online payment.
          </SheetDescription>
        </SheetHeader>
        {formContent}
        <SheetFooter className="mt-4 flex flex-col gap-2">
          {footerContent}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
function PaymentActionDialog({
  pendingAction,
  note,
  providerPaymentId,
  paymentUrl,
  busy,
  onNoteChange,
  onProviderPaymentIdChange,
  onPaymentUrlChange,
  onClose,
  onConfirm,
}: {
  pendingAction: PendingAction | null;
  note: string;
  providerPaymentId: string;
  paymentUrl: string;
  busy: boolean;
  onNoteChange: (value: string) => void;
  onProviderPaymentIdChange: (value: string) => void;
  onPaymentUrlChange: (value: string) => void;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={!!pendingAction} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{pendingAction?.label || 'Payment action'}</DialogTitle>
          <DialogDescription>
            This records the action in the payment event timeline and syncs linked order payment state where applicable. Mark Paid only after staff verifies the money was actually collected.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {pendingAction?.action === 'mark_paid' && (
            <div className="space-y-2">
              <Label htmlFor="provider-payment-id">Provider payment ID</Label>
              <Input
                id="provider-payment-id"
                value={providerPaymentId}
                onChange={(event) => onProviderPaymentIdChange(event.target.value)}
                placeholder="Optional"
              />
            </div>
          )}
          {pendingAction?.action === 'send' && (
            <div className="space-y-2">
              <Label htmlFor="send-payment-url">Payment URL</Label>
              <Input
                id="send-payment-url"
                value={paymentUrl}
                onChange={(event) => onPaymentUrlChange(event.target.value)}
                placeholder="Optional"
              />
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="payment-action-note">Note</Label>
            <Textarea id="payment-action-note" rows={3} value={note} onChange={(event) => onNoteChange(event.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="button" variant={pendingAction?.tone || 'default'} onClick={onConfirm} disabled={busy}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            Confirm
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
export function PaymentsPage() {
  const { data: configData, isLoading: isConfigLoading } = useTenantConfig();
  const { data: toolsData, isLoading: isToolsLoading } = useTenantTools();
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(emptyCreateForm);
  const [selectedPaymentId, setSelectedPaymentId] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [actionNote, setActionNote] = useState('');
  const [providerPaymentId, setProviderPaymentId] = useState('');
  const [actionPaymentUrl, setActionPaymentUrl] = useState('');
  const createInFlightRef = useRef(false);
  const createIdempotencyKeyRef = useRef(createIdempotencyKey());
  const paymentAccess = getModuleEntitlement(toolsData, 'payments');
  const paymentsEnabled = paymentAccess.canMutateRuntimeData;
  const paymentsQuery = usePaymentsList(statusFilter, typeFilter, search.trim() || undefined);
  const payments = paymentsQuery.data?.pages.flatMap((page) => page.items) || [];
  const selectedFallback = payments.find((payment) => payment.id === selectedPaymentId) || null;
  const detailQuery = usePaymentDetail(selectedPaymentId || undefined);
  const selectedPayment = detailQuery.data || selectedFallback;
  const eventsQuery = usePaymentEvents(selectedPaymentId || undefined);
  const createPayment = useCreatePayment();
  const actionPayment = useActionPayment();
  const metricsQuery = usePaymentMetrics();
  const metrics = metricsQuery.data || { pending: 0, paidAmount: 0, needsAttention: 0 };
  if (isConfigLoading || isToolsLoading || !configData) {
    return <TableSkeleton columns={6} rows={5} />;
  }
  if (!paymentAccess.allowedByPlan) {
    return <PaymentsDisabledState config={configData} canEnable={false} lockedReason={paymentAccess.lockedReason} />;
  }
  if (!paymentsEnabled) {
    return <PaymentsDisabledState config={configData} canEnable={paymentAccess.canEnable} lockedReason={paymentAccess.lockedReason} />;
  }
  const savePayment = () => {
    if (createInFlightRef.current || createPayment.isPending) {
      return;
    }
    const amount = toNumber(createForm.amount);
    if (!amount && !createForm.order_id.trim()) {
      toast.error('Enter an amount or link an order');
      return;
    }
    const payload: CreatePaymentPayload = {
      type: createForm.type,
      provider: createForm.provider,
      amount,
      currency: createForm.currency.trim().toUpperCase() || 'INR',
      customer_id: toOptionalText(createForm.customer_id) || null,
      booking_id: toOptionalText(createForm.booking_id) || null,
      order_id: toOptionalText(createForm.order_id) || null,
      payment_url: toOptionalText(createForm.payment_url) || null,
      expires_at: createForm.expires_at ? new Date(createForm.expires_at).toISOString() : null,
      idempotency_key: createIdempotencyKeyRef.current,
      metadata: createForm.note.trim() ? { note: createForm.note.trim() } : {},
    };
    createInFlightRef.current = true;
    createPayment.mutate(payload, {
      onSuccess: () => {
        toast.success('Payment request created');
        setCreateForm(emptyCreateForm);
        setCreateOpen(false);
        createIdempotencyKeyRef.current = createIdempotencyKey();
      },
      onError: (error) => toast.error(getApiErrorMessage(error, 'Could not create payment request')),
      onSettled: () => {
        createInFlightRef.current = false;
      },
    });
  };
  const openAction = (payment: PaymentRequest, action: PaymentAction, label: string, tone?: 'default' | 'destructive') => {
    setPendingAction({ payment, action, label, tone });
    setActionNote('');
    setProviderPaymentId('');
    setActionPaymentUrl(payment.payment_url || '');
  };
  const confirmAction = () => {
    if (!pendingAction) return;
    actionPayment.mutate(
      {
        id: pendingAction.payment.id,
        action: pendingAction.action,
        note: toOptionalText(actionNote),
        provider_payment_id: toOptionalText(providerPaymentId),
        payment_url: pendingAction.action === 'send' ? toOptionalText(actionPaymentUrl) : undefined,
      },
      {
        onSuccess: () => {
          toast.success('Payment updated');
          setPendingAction(null);
        },
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not update payment')),
      }
    );
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
        <div>
          <h2 className="text-[16px] font-semibold text-foreground">Payments</h2>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            Deposits, payment links, and manual collection records for customers, bookings, and orders.
          </p>
        </div>
        <Button type="button" onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          New Payment
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="Pending" value={metrics.pending} helper="Draft, sent, or viewed" />
        <Metric label="Paid" value={formatPaymentMoney(metrics.paidAmount)} helper="Total paid" />
        <Metric label="Needs Attention" value={metrics.needsAttention} helper="Failed or expired" />
      </div>
      <div className="rounded-md border border-amber-500/25 bg-amber-500/10 p-3 text-[13px] text-amber-900 dark:text-amber-100">
        <div className="flex gap-2.5">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Payment status is a business record</p>
            <p className="mt-1 text-[12px] leading-5 opacity-90">
              Draft means not sent, Sent means a link/message was recorded, Paid means staff or a provider marked collection. Manual payments and mock/test provider flows are not automatic bank reconciliation.
            </p>
          </div>
        </div>
      </div>
      <div className="flex flex-col justify-between gap-3 rounded-md border bg-card p-3 md:flex-row md:items-center">
        <div className="flex items-center gap-2">
          <Wallet className="h-4 w-4 text-muted-foreground" />
          <p className="text-[13px] font-medium text-foreground">Payment requests</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative sm:w-[260px]">
            <Search className="absolute left-2.5 top-1/2 h-[14px] w-[14px] -translate-y-1/2 text-muted-foreground" />
            <Input className="pl-8" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search customer or ID" />
          </div>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="sm:w-[140px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All status</SelectItem>
              {Object.entries(PAYMENT_STATUS_COPY).map(([value, label]) => (
                <SelectItem key={value} value={value}>{label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={typeFilter} onValueChange={setTypeFilter}>
            <SelectTrigger className="sm:w-[180px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {Object.entries(PAYMENT_TYPE_COPY).map(([value, label]) => (
                <SelectItem key={value} value={value}>{label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {paymentsQuery.isLoading ? (
        <TableSkeleton columns={7} rows={6} />
      ) : payments.length === 0 ? (
        <div className="flex min-h-[360px] items-center justify-center rounded-md border bg-card">
          <div className="max-w-sm px-6 text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
              <ReceiptText className="h-5 w-5 text-muted-foreground" />
            </div>
            <h3 className="mt-4 text-[15px] font-semibold text-foreground">No payment requests</h3>
            <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
              Create a deposit, order payment, or manual collection record when staff has a customer, booking, or order to track. Configure live provider behavior before treating online payment as production-ready.
            </p>
            <div className="mt-4 flex flex-col justify-center gap-2 sm:flex-row">
              <Button type="button" onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4" />
                New Payment
              </Button>
              <Button asChild variant="outline">
                <Link to="/dashboard/billing">Review Billing</Link>
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Payment</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Linked</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payments.map((payment) => (
                <TableRow key={payment.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelectedPaymentId(payment.id)}>
                  <TableCell>
                    <p className="font-mono text-xs text-muted-foreground">{payment.id.split('-')[0]}</p>
                    <p className="mt-1 text-[13px] font-medium">{PAYMENT_TYPE_COPY[payment.type]}</p>
                  </TableCell>
                  <TableCell>
                    <p className="text-[13px] font-medium">{payment.customer_name || 'Unlinked customer'}</p>
                    <p className="text-xs text-muted-foreground">{payment.customer_phone || 'No phone'}</p>
                  </TableCell>
                  <TableCell className="font-medium">{formatPaymentMoney(payment.amount, payment.currency)}</TableCell>
                  <TableCell><StatusPill status={payment.status} /></TableCell>
                  <TableCell>{PROVIDER_COPY[payment.provider]}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {payment.order_id ? `Order ${payment.order_id.split('-')[0]}` : payment.booking_id ? `Booking ${payment.booking_id.split('-')[0]}` : 'Custom'}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">{formatDateTime(payment.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {paymentsQuery.hasNextPage && (
        <div className="flex justify-center pt-2">
          <Button variant="outline" onClick={() => paymentsQuery.fetchNextPage()} disabled={paymentsQuery.isFetchingNextPage}>
            {paymentsQuery.isFetchingNextPage && <Loader2 className="h-4 w-4 animate-spin" />}
            Load More Payments
          </Button>
        </div>
      )}
      <Sheet open={!!selectedPaymentId} onOpenChange={(open) => !open && setSelectedPaymentId(null)}>
        <SheetContent className="overflow-y-auto sm:max-w-xl">
          {!selectedPayment && detailQuery.isLoading ? (
            <DetailSheetSkeleton />
          ) : selectedPayment && (
            <>
              <SheetHeader className="mb-6 border-b pb-4">
                <SheetTitle>Payment Details</SheetTitle>
                <SheetDescription className="font-mono text-xs">{selectedPayment.id}</SheetDescription>
              </SheetHeader>
              <div className="space-y-6">
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-md border bg-card p-3">
                    <p className="text-xs text-muted-foreground">Amount</p>
                    <p className="mt-1 text-sm font-semibold">{formatPaymentMoney(selectedPayment.amount, selectedPayment.currency)}</p>
                  </div>
                  <div className="rounded-md border bg-card p-3">
                    <p className="text-xs text-muted-foreground">Status</p>
                    <div className="mt-1"><StatusPill status={selectedPayment.status} /></div>
                  </div>
                </div>
                <div className="rounded-md border p-3 text-sm">
                  <h4 className="mb-2 font-semibold">Context</h4>
                  <div className="space-y-2">
                    <div className="flex justify-between gap-3"><span className="text-muted-foreground">Customer</span><span>{selectedPayment.customer_name || 'Unlinked'}</span></div>
                    <div className="flex justify-between gap-3"><span className="text-muted-foreground">Type</span><span>{PAYMENT_TYPE_COPY[selectedPayment.type]}</span></div>
                    <div className="flex justify-between gap-3"><span className="text-muted-foreground">Provider</span><span>{PROVIDER_COPY[selectedPayment.provider]}</span></div>
                    <div className="flex justify-between gap-3"><span className="text-muted-foreground">Expires</span><span>{formatDateTime(selectedPayment.expires_at)}</span></div>
                  </div>
                  {selectedPayment.payment_url && (
                    <Button asChild variant="outline" size="sm" className="mt-3">
                      <a href={selectedPayment.payment_url} target="_blank" rel="noreferrer">
                        <ExternalLink className="h-3.5 w-3.5" />
                        Open Link
                      </a>
                    </Button>
                  )}
                </div>
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">Actions</h4>
                  <div className="grid gap-2">
                    {['draft', 'failed', 'expired'].includes(selectedPayment.status) && (
                      <Button className="justify-start" onClick={() => openAction(selectedPayment, 'send', 'Send Payment Request')}>
                        <Send className="h-4 w-4" />
                        Mark Link Sent
                      </Button>
                    )}
                    {!['paid', 'refunded', 'cancelled'].includes(selectedPayment.status) && (
                      <Button variant="outline" className="justify-start" onClick={() => openAction(selectedPayment, 'mark_paid', 'Mark Payment Paid')}>
                        <CheckCircle2 className="h-4 w-4" />
                        Mark Paid After Verification
                      </Button>
                    )}
                    {['sent', 'viewed'].includes(selectedPayment.status) && (
                      <Button variant="outline" className="justify-start" onClick={() => openAction(selectedPayment, 'mark_failed', 'Mark Payment Failed')}>
                        <AlertTriangle className="h-4 w-4" />
                        Mark Failed
                      </Button>
                    )}
                    {selectedPayment.status === 'paid' && (
                      <Button variant="outline" className="justify-start" onClick={() => openAction(selectedPayment, 'mark_refunded', 'Mark Payment Refunded')}>
                        <CreditCard className="h-4 w-4" />
                        Mark Refunded
                      </Button>
                    )}
                    {!['paid', 'refunded', 'cancelled'].includes(selectedPayment.status) && (
                      <Button variant="destructive" className="justify-start" onClick={() => openAction(selectedPayment, 'cancel', 'Cancel Payment Request', 'destructive')}>
                        <XCircle className="h-4 w-4" />
                        Cancel Request
                      </Button>
                    )}
                  </div>
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-semibold">Timeline</h4>
                  <div className="space-y-2 rounded-md border p-3">
                    {eventsQuery.isLoading ? (
                      <p className="text-sm text-muted-foreground">Loading events...</p>
                    ) : eventsQuery.data && eventsQuery.data.length > 0 ? (
                      eventsQuery.data.map((event) => (
                        <div key={event.id} className="flex items-start gap-2 rounded-md bg-muted/40 p-2 text-[12px]">
                          <Clock3 className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" />
                          <div className="min-w-0">
                            <p className="font-medium capitalize">{event.event_type.replace(/_/g, ' ')}</p>
                            <p className="text-muted-foreground">{formatDateTime(event.created_at)} by {event.actor_type.replace(/_/g, ' ')}</p>
                          </div>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-muted-foreground">No payment events yet.</p>
                    )}
                  </div>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
      <PaymentCreateDialog
        open={createOpen}
        form={createForm}
        pending={createPayment.isPending}
        onChange={(updates) => setCreateForm((current) => ({ ...current, ...updates }))}
        onClose={() => setCreateOpen(false)}
        onSave={savePayment}
      />
      <PaymentActionDialog
        pendingAction={pendingAction}
        note={actionNote}
        providerPaymentId={providerPaymentId}
        paymentUrl={actionPaymentUrl}
        busy={actionPayment.isPending}
        onNoteChange={setActionNote}
        onProviderPaymentIdChange={setProviderPaymentId}
        onPaymentUrlChange={setActionPaymentUrl}
        onClose={() => setPendingAction(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
