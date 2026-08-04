import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import { useMediaQuery } from '@/hooks/use-media-query';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetFooter,
} from '@/components/ui/sheet';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { getApiErrorMessage } from '@/lib/api-errors';
import {
  useCreateScheduledMessage,
  type CreateScheduledMessagePayload,
  type ReminderChannel,
  type ReminderRule,
  type ReminderTrigger,
} from '../api';
import { CHANNEL_COPY, TRIGGER_COPY } from '../constants';
import { useCustomersList } from '@/features/customers/api';
import { useOrdersList, formatMoney } from '@/features/orders/api';
import { useBookingsList } from '@/features/bookings/api';

/* ── types ─────────────────────────────────────────────────── */

const NO_RULE = '__none';

type MessageFormState = {
  rule_id: string;
  customer_id: string;
  conversation_id: string;
  booking_id: string;
  order_id: string;
  payment_request_id: string;
  review_request_id: string;
  trigger_type: ReminderTrigger;
  channel: ReminderChannel;
  message_text: string;
  scheduled_for: string;
  note: string;
};

const emptyMessageForm: MessageFormState = {
  rule_id: NO_RULE,
  customer_id: '',
  conversation_id: '',
  booking_id: '',
  order_id: '',
  payment_request_id: '',
  review_request_id: '',
  trigger_type: 'manual',
  channel: 'whatsapp',
  message_text: '',
  scheduled_for: '',
  note: '',
};

function toOptionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

/* ── props ──────────────────────────────────────────────────── */

interface ScheduleMessageDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rules: ReminderRule[];
}

/**
 * Dialog for manually scheduling a one-off follow-up message.
 * Manages its own form state and mutation — resets on success or explicit close.
 * API payload shape is unchanged from original implementation.
 */
function formatDateTime(value?: string | null): string {
  if (!value) return 'Not set';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Not set' : parsed.toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export function ScheduleMessageDialog({
  open,
  onOpenChange,
  rules,
}: ScheduleMessageDialogProps) {
  const [form, setForm] = useState<MessageFormState>(emptyMessageForm);
  const [errors, setErrors] = useState<Partial<Record<keyof MessageFormState, string>>>({});

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

  const createMessage = useCreateScheduledMessage();

  const handleClose = () => {
    setForm(emptyMessageForm);
    setErrors({});
    onOpenChange(false);
  };

  const handleRuleSelect = (value: string) => {
    const rule = rules.find((r) => r.id === value);
    setForm((prev) => ({
      ...prev,
      rule_id: value,
      trigger_type: rule?.trigger_type ?? prev.trigger_type,
      channel: rule?.channel ?? prev.channel,
      message_text: rule?.template_text ?? prev.message_text,
    }));
  };

  const validate = () => {
    const errs: { message_text?: string; scheduled_for?: string } = {};
    if (!form.message_text.trim()) errs.message_text = 'Message text is required.';
    if (!form.scheduled_for) errs.scheduled_for = 'Scheduled date and time is required.';
    return errs;
  };

  const handleSchedule = () => {
    const errs = validate();
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }
    setErrors({});

    const payload: CreateScheduledMessagePayload = {
      rule_id: form.rule_id === NO_RULE ? null : form.rule_id,
      customer_id: toOptionalText(form.customer_id) ?? null,
      conversation_id: toOptionalText(form.conversation_id) ?? null,
      booking_id: toOptionalText(form.booking_id) ?? null,
      order_id: toOptionalText(form.order_id) ?? null,
      payment_request_id: toOptionalText(form.payment_request_id) ?? null,
      review_request_id: toOptionalText(form.review_request_id) ?? null,
      trigger_type: form.trigger_type,
      channel: form.channel,
      message_text: form.message_text.trim(),
      scheduled_for: new Date(form.scheduled_for).toISOString(),
      metadata: form.note.trim() ? { note: form.note.trim() } : {},
    };

    createMessage.mutate(payload, {
      onSuccess: () => {
        toast.success('Message scheduled');
        handleClose();
      },
      onError: (error) =>
        toast.error(getApiErrorMessage(error, 'Could not schedule message')),
    });
  };

  const field = <K extends keyof MessageFormState>(key: K) => ({
    value: form[key] as string,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((prev) => ({ ...prev, [key]: e.target.value })),
  });

  const isDesktop = useMediaQuery('(min-width: 768px)');

  const formContent = (
    <div className="grid min-h-0 gap-4 overflow-y-auto pr-1 sm:grid-cols-2">
      {/* Rule */}
      <div className="space-y-1.5">
        <Label htmlFor="msg-rule">Rule (optional)</Label>
        <Select value={form.rule_id} onValueChange={handleRuleSelect}>
          <SelectTrigger id="msg-rule">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_RULE}>No rule</SelectItem>
            {rules.map((rule) => (
              <SelectItem key={rule.id} value={rule.id}>
                {rule.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-[11px] text-muted-foreground">
          Selecting a rule pre-fills trigger, channel, and message text.
        </p>
      </div>

      {/* Scheduled for */}
      <div className="space-y-1.5">
        <Label htmlFor="msg-time">
          Scheduled for{' '}
          <span aria-hidden="true" className="text-destructive">
            *
          </span>
        </Label>
        <Input
          id="msg-time"
          type="datetime-local"
          value={form.scheduled_for}
          onChange={(e) => {
            setForm((prev) => ({ ...prev, scheduled_for: e.target.value }));
            if (errors.scheduled_for)
              setErrors((prev) => ({ ...prev, scheduled_for: undefined }));
          }}
          aria-describedby={errors.scheduled_for ? 'msg-time-error' : undefined}
          aria-invalid={!!errors.scheduled_for}
          aria-required="true"
        />
        {errors.scheduled_for && (
          <p id="msg-time-error" role="alert" className="text-[12px] text-destructive">
            {errors.scheduled_for}
          </p>
        )}
      </div>

      {/* Trigger */}
      <div className="space-y-1.5">
        <Label htmlFor="msg-trigger">Trigger</Label>
        <Select
          value={form.trigger_type}
          onValueChange={(v) =>
            setForm((prev) => ({ ...prev, trigger_type: v as ReminderTrigger }))
          }
        >
          <SelectTrigger id="msg-trigger">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(TRIGGER_COPY).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Channel */}
      <div className="space-y-1.5">
        <Label htmlFor="msg-channel">Channel</Label>
        <Select
          value={form.channel}
          onValueChange={(v) =>
            setForm((prev) => ({ ...prev, channel: v as ReminderChannel }))
          }
        >
          <SelectTrigger id="msg-channel">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(CHANNEL_COPY).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* IDs */}
      <div className="space-y-1.5">
        <Label htmlFor="msg-customer">Customer</Label>
        <Select
          value={form.customer_id || 'none'}
          onValueChange={(val) =>
            setForm((prev) => ({ ...prev, customer_id: val === 'none' ? '' : val }))
          }
          disabled={customersLoading || showCustomersEmpty}
        >
          <SelectTrigger id="msg-customer">
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
      <div className="space-y-1.5">
        <Label htmlFor="msg-conversation">Conversation ID</Label>
        <Input id="msg-conversation" placeholder="Optional" {...field('conversation_id')} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="msg-booking">Booking</Label>
        <Select
          value={form.booking_id || 'none'}
          onValueChange={(val) =>
            setForm((prev) => ({ ...prev, booking_id: val === 'none' ? '' : val }))
          }
          disabled={bookingsLoading || showBookingsEmpty}
        >
          <SelectTrigger id="msg-booking">
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
      <div className="space-y-1.5">
        <Label htmlFor="msg-order">Order</Label>
        <Select
          value={form.order_id || 'none'}
          onValueChange={(val) =>
            setForm((prev) => ({ ...prev, order_id: val === 'none' ? '' : val }))
          }
          disabled={ordersLoading || showOrdersEmpty}
        >
          <SelectTrigger id="msg-order">
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
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="msg-payment">Payment request ID</Label>
        <Input id="msg-payment" placeholder="Optional" {...field('payment_request_id')} />
      </div>
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="msg-review">Review request ID</Label>
        <Input id="msg-review" placeholder="Optional" {...field('review_request_id')} />
      </div>

      {/* Message text */}
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="msg-text">
          Message{' '}
          <span aria-hidden="true" className="text-destructive">
            *
          </span>
        </Label>
        <Textarea
          id="msg-text"
          rows={4}
          value={form.message_text}
          onChange={(e) => {
            setForm((prev) => ({ ...prev, message_text: e.target.value }));
            if (errors.message_text)
              setErrors((prev) => ({ ...prev, message_text: undefined }));
          }}
          aria-describedby={errors.message_text ? 'msg-text-error' : undefined}
          aria-invalid={!!errors.message_text}
          aria-required="true"
        />
        {errors.message_text && (
          <p id="msg-text-error" role="alert" className="text-[12px] text-destructive">
            {errors.message_text}
          </p>
        )}
      </div>

      {/* Internal note */}
      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="msg-note">Internal note</Label>
        <Textarea
          id="msg-note"
          rows={2}
          placeholder="Optional internal note for staff reference"
          {...field('note')}
        />
      </div>
    </div>
  );

  const footerContent = (
    <>
      <Button
        type="button"
        variant="ghost"
        onClick={handleClose}
        disabled={createMessage.isPending}
      >
        Cancel
      </Button>
      <Button
        type="button"
        onClick={handleSchedule}
        disabled={createMessage.isPending}
      >
        {createMessage.isPending && (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
        )}
        Schedule
      </Button>
    </>
  );

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
        <DialogContent
          className={cn(
            "max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-2xl",
            createMessage.isPending && "[&>button:last-child]:pointer-events-none [&>button:last-child]:opacity-0"
          )}
          aria-labelledby="schedule-dialog-title"
          aria-describedby="schedule-dialog-desc"
          onPointerDownOutside={(e) => {
            if (createMessage.isPending) e.preventDefault();
          }}
          onEscapeKeyDown={(e) => {
            if (createMessage.isPending) e.preventDefault();
          }}
        >
          <DialogHeader>
            <DialogTitle id="schedule-dialog-title">Schedule follow-up</DialogTitle>
            <DialogDescription id="schedule-dialog-desc">
              Queue a one-off reminder or follow-up message. The worker will deliver it through the
              selected channel when it falls due.
            </DialogDescription>
          </DialogHeader>
          {formContent}
          <DialogFooter className="border-t pt-3">
            {footerContent}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Sheet open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <SheetContent
        side="bottom"
        className={cn(
          "max-h-[85vh] overflow-y-auto rounded-t-xl p-4 sm:p-6",
          createMessage.isPending && "[&>button:last-child]:pointer-events-none [&>button:last-child]:opacity-0"
        )}
        aria-labelledby="schedule-dialog-title"
        aria-describedby="schedule-dialog-desc"
        onPointerDownOutside={(e) => {
          if (createMessage.isPending) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (createMessage.isPending) e.preventDefault();
        }}
      >
        <SheetHeader className="text-left mb-4">
          <SheetTitle id="schedule-dialog-title">Schedule follow-up</SheetTitle>
          <SheetDescription id="schedule-dialog-desc">
            Queue a one-off reminder or follow-up message. The worker will deliver it through the
            selected channel when it falls due.
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
