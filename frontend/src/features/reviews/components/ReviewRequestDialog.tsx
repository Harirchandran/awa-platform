import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ReviewSource } from '../api';
import type { CreateReviewForm } from '../types';
import { REVIEW_SOURCE_COPY } from '../constants';
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
import { Textarea } from '@/components/ui/textarea';
import { PublicReviewLinkField } from './PublicReviewLinkField';
import { useCustomersList } from '@/features/customers/api';
import { useOrdersList, formatMoney } from '@/features/orders/api';
import { useBookingsList } from '@/features/bookings/api';

interface ReviewRequestDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  form: CreateReviewForm;
  onChange: (patch: Partial<CreateReviewForm>) => void;
  onSubmit: () => void;
  isPending: boolean;
  isUrlInvalid: boolean;
  defaultPublicReviewUrl?: string;
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'Not set';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Not set' : parsed.toLocaleString('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export function ReviewRequestDialog({
  open,
  onOpenChange,
  form,
  onChange,
  onSubmit,
  isPending,
  isUrlInvalid,
  defaultPublicReviewUrl,
}: ReviewRequestDialogProps) {
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "sm:max-w-2xl",
          isPending && "[&>button:last-child]:pointer-events-none [&>button:last-child]:opacity-0"
        )}
        onPointerDownOutside={(e) => {
          if (isPending) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (isPending) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle>Create review request</DialogTitle>
          <DialogDescription>
            Link a request to a customer, conversation, booking, or order. This creates a review request record; it does not post a public review.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="review-source">Source</Label>
            <Select value={form.source} onValueChange={(value) => onChange({ source: value as ReviewSource })}>
              <SelectTrigger id="review-source"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(REVIEW_SOURCE_COPY).map(([value, label]) => (
                  <SelectItem key={value} value={value}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <PublicReviewLinkField
            id="review-url"
            value={form.public_review_url}
            onChange={(value) => onChange({ public_review_url: value })}
            placeholder={defaultPublicReviewUrl || 'Uses settings when blank'}
            invalid={isUrlInvalid}
          />
          <div className="space-y-2">
            <Label htmlFor="review-customer">Customer</Label>
            <Select
              value={form.customer_id || 'none'}
              onValueChange={(val) => onChange({ customer_id: val === 'none' ? '' : val })}
              disabled={customersLoading || showCustomersEmpty}
            >
              <SelectTrigger id="review-customer">
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
            <Label htmlFor="review-conversation">Conversation ID</Label>
            <Input id="review-conversation" value={form.conversation_id} onChange={(event) => onChange({ conversation_id: event.target.value })} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="review-booking">Booking</Label>
            <Select
              value={form.booking_id || 'none'}
              onValueChange={(val) => onChange({ booking_id: val === 'none' ? '' : val })}
              disabled={bookingsLoading || showBookingsEmpty}
            >
              <SelectTrigger id="review-booking">
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
            <Label htmlFor="review-order">Order</Label>
            <Select
              value={form.order_id || 'none'}
              onValueChange={(val) => onChange({ order_id: val === 'none' ? '' : val })}
              disabled={ordersLoading || showOrdersEmpty}
            >
              <SelectTrigger id="review-order">
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
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="review-note">Internal note</Label>
            <Textarea id="review-note" rows={3} value={form.note} onChange={(event) => onChange({ note: event.target.value })} />
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} disabled={isPending}>Cancel</Button>
          <Button type="button" onClick={onSubmit} disabled={isPending || isUrlInvalid}>
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Create Request
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
