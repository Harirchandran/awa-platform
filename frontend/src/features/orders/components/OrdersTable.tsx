import { useMemo, useState } from 'react';
import { format } from 'date-fns';
import { Link } from 'react-router-dom';
import { CheckCircle2, Clock3, Loader2, PackageCheck, ReceiptText, Settings, ShoppingBag, XCircle } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { formatMoney, useOrderDetail, useUpdateOrder, type Order, type OrderStatus, type PaymentStatus } from '../api';
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
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DetailSheetSkeleton, TableSkeleton } from '@/components/ui/loading-states';
import { cn } from '@/lib/utils';
interface OrdersTableProps {
  orders: Order[];
  isLoading: boolean;
  isFetching?: boolean;
  hasNextPage?: boolean;
  fetchNextPage: () => void;
  isFetchingNextPage: boolean;
}
const NEXT_STATUS: Partial<Record<OrderStatus, OrderStatus>> = {
  new: 'accepted',
  accepted: 'preparing',
  preparing: 'ready',
  ready: 'completed',
};
const STATUS_COPY: Record<OrderStatus, string> = {
  new: 'New',
  accepted: 'Accepted',
  preparing: 'Preparing',
  ready: 'Ready',
  completed: 'Completed',
  cancelled: 'Cancelled',
};
function formatOrderDate(value?: string | null): string {
  if (!value) return 'Date not set';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? 'Date not set' : format(parsed, 'MMM d, h:mm a');
}
function getSnapshotText(record: Record<string, unknown> | null | undefined, key: string): string | undefined {
  const value = record?.[key];
  return typeof value === 'string' && value.trim() ? value : undefined;
}
function getStatusBadge(status: string): string {
  switch (status) {
    case 'new': return 'bg-sky-500/10 text-sky-600 dark:text-sky-300';
    case 'accepted': return 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-300';
    case 'preparing': return 'bg-amber-500/10 text-amber-600 dark:text-amber-300';
    case 'ready': return 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300';
    case 'completed': return 'bg-muted text-muted-foreground';
    case 'cancelled': return 'bg-red-500/10 text-red-600 dark:text-red-300';
    default: return 'bg-muted text-muted-foreground';
  }
}
function getPaymentBadge(status: string): string {
  switch (status) {
    case 'paid': return 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300';
    case 'refunded': return 'bg-violet-500/10 text-violet-600 dark:text-violet-300';
    default: return 'bg-muted text-muted-foreground';
  }
}
export function OrdersTable({
  orders,
  isLoading,
  isFetching = false,
  hasNextPage,
  fetchNextPage,
  isFetchingNextPage,
}: OrdersTableProps) {
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  const [pendingUpdate, setPendingUpdate] = useState<{
    id: string;
    label: string;
    updates: { status?: OrderStatus; payment_status?: PaymentStatus };
  } | null>(null);
  const selectedFallback = useMemo(
    () => orders.find((order) => order.id === selectedOrderId) ?? null,
    [orders, selectedOrderId],
  );
  const detailQuery = useOrderDetail(selectedOrderId || undefined);
  const selectedOrder = detailQuery.data ?? selectedFallback;
  const selectedNextStatus = selectedOrder ? NEXT_STATUS[selectedOrder.status] : undefined;
  const updateMutation = useUpdateOrder();
  const handleUpdate = (id: string, updates: { status?: OrderStatus; payment_status?: PaymentStatus }) => {
    updateMutation.mutate(
      { id, updates },
      {
        onSuccess: () => {
          const label = updates.status ? STATUS_COPY[updates.status] : updates.payment_status;
          toast.success(`Order updated${label ? ` to ${label}` : ''}`);
          if (updates.status === 'completed' || updates.status === 'cancelled') {
            setSelectedOrderId(null);
          }
        },
        onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Failed to update order')),
      },
    );
  };
  if (isLoading) {
    return <TableSkeleton columns={7} rows={5} />;
  }
  if (!orders || orders.length === 0) {
    return (
      <div className="rounded-lg border bg-card/50 p-8 text-center sm:p-12">
        <ReceiptText className="mx-auto h-8 w-8 text-muted-foreground" />
        <h3 className="mt-3 text-[16px] font-medium">No orders yet</h3>
        <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-muted-foreground">
          Orders appear here when customers complete order details through AWA Chat or WhatsApp. Keep Orders enabled, add catalog items, and review fulfillment/payment rules before sharing the flow.
        </p>
        <div className="mt-5 flex flex-col justify-center gap-2 sm:flex-row">
          <Button asChild size="sm">
            <Link to="/dashboard/catalog">
              <ShoppingBag className="h-3.5 w-3.5" />
              Review Catalog
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/dashboard/tools">
              <Settings className="h-3.5 w-3.5" />
              Workflow Tools
            </Link>
          </Button>
        </div>
      </div>
    );
  }
  return (
    <>
      <div className={cn("space-y-4 transition-opacity duration-200", isFetching && !isLoading && "opacity-50 pointer-events-none")}>
        <div className="space-y-3 md:hidden">
        {orders.map((order) => (
          <button
            key={order.id}
            type="button"
            onClick={() => setSelectedOrderId(order.id)}
            className="w-full rounded-md border bg-card p-4 text-left transition-colors hover:bg-muted/40"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-[14px] font-semibold">{order.customer_name || 'Unknown customer'}</p>
                <p className="mt-0.5 text-[12px] text-muted-foreground">{order.customer_phone || 'Phone not saved'}</p>
              </div>
              <span className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-medium ${getStatusBadge(order.status)}`}>
                {STATUS_COPY[order.status]}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 text-[12px]">
              <div>
                <p className="text-muted-foreground">Total</p>
                <p className="mt-1 font-medium text-foreground">{formatMoney(order.total_amount, order.currency)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Fulfillment</p>
                <p className="mt-1 font-medium capitalize text-foreground">{order.fulfillment_type.replace(/_/g, ' ')}</p>
              </div>
            </div>
            <p className="mt-4 font-mono text-[11px] text-muted-foreground">ID {order.id.split('-')[0]} · {formatOrderDate(order.created_at)}</p>
          </button>
        ))}
      </div>
      <div className="hidden overflow-hidden rounded-md border bg-card md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Order ID</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Fulfillment</TableHead>
              <TableHead>Total</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Payment</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orders.map((order) => (
              <TableRow
                key={order.id}
                className="cursor-pointer hover:bg-muted/50"
                onClick={() => setSelectedOrderId(order.id)}
              >
                <TableCell className="font-mono text-xs text-muted-foreground">{order.id.split('-')[0]}</TableCell>
                <TableCell>
                  <div className="font-medium">{order.customer_name || 'Unknown'}</div>
                  <div className="text-xs text-muted-foreground">{order.customer_phone || 'Phone not saved'}</div>
                </TableCell>
                <TableCell className="capitalize">{order.fulfillment_type.replace(/_/g, ' ')}</TableCell>
                <TableCell>{formatMoney(order.total_amount, order.currency)}</TableCell>
                <TableCell>
                  <span className={`rounded-full px-2 py-1 text-xs font-medium ${getStatusBadge(order.status)}`}>
                    {STATUS_COPY[order.status]}
                  </span>
                </TableCell>
                <TableCell>
                  <span className={`rounded-full px-2 py-1 text-xs font-medium ${getPaymentBadge(order.payment_status)}`}>
                    {order.payment_status}
                  </span>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">{formatOrderDate(order.created_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {hasNextPage && (
        <div className="flex justify-center pt-4">
          <Button variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
            {isFetchingNextPage && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isFetchingNextPage ? 'Loading more' : 'Load More Orders'}
          </Button>
        </div>
      )}
      </div>
      <Sheet open={!!selectedOrderId} onOpenChange={(open) => !open && setSelectedOrderId(null)}>
        <SheetContent className="overflow-y-auto sm:max-w-lg">
          {!selectedOrder && detailQuery.isLoading ? (
            <DetailSheetSkeleton />
          ) : selectedOrder && (
            <>
              <SheetHeader className="mb-6 border-b pb-4">
                <SheetTitle>Order Details</SheetTitle>
                <SheetDescription className="font-mono text-xs">{selectedOrder.id}</SheetDescription>
              </SheetHeader>
              <div className="space-y-6">
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-md border bg-card p-3">
                    <p className="text-xs text-muted-foreground">Status</p>
                    <p className="mt-1 text-sm font-semibold">{STATUS_COPY[selectedOrder.status]}</p>
                  </div>
                  <div className="rounded-md border bg-card p-3">
                    <p className="text-xs text-muted-foreground">Total</p>
                    <p className="mt-1 text-sm font-semibold">{formatMoney(selectedOrder.total_amount, selectedOrder.currency)}</p>
                  </div>
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-semibold">Customer</h4>
                  <div className="space-y-1 rounded-md bg-muted p-3 text-sm">
                    <p><span className="text-muted-foreground">Name:</span> {selectedOrder.customer_name || getSnapshotText(selectedOrder.customer_snapshot, 'name') || 'Unknown'}</p>
                    <p><span className="text-muted-foreground">Phone:</span> {selectedOrder.customer_phone || getSnapshotText(selectedOrder.customer_snapshot, 'phone') || 'Not saved'}</p>
                    <p><span className="text-muted-foreground">Channel:</span> {selectedOrder.channel_type.replace(/_/g, ' ')}</p>
                  </div>
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-semibold">Items</h4>
                  <div className="divide-y rounded-md border">
                    {detailQuery.isLoading ? (
                      <div className="space-y-2 p-3">
                        <div className="h-12 animate-pulse rounded-md bg-muted" />
                        <div className="h-12 animate-pulse rounded-md bg-muted" />
                      </div>
                    ) : selectedOrder.items?.length ? (
                      selectedOrder.items.map((item) => (
                        <div key={item.id} className="flex items-start justify-between gap-3 p-3 text-sm">
                          <div className="min-w-0">
                            <p className="font-medium">{item.item_name}</p>
                            <p className="text-xs text-muted-foreground">Qty {item.quantity} × {formatMoney(item.unit_price, selectedOrder.currency)}</p>
                          </div>
                          <p className="shrink-0 font-medium">{formatMoney(item.line_total, selectedOrder.currency)}</p>
                        </div>
                      ))
                    ) : (
                      <div className="p-3 text-sm text-muted-foreground">{selectedOrder.item_count || 0} item{selectedOrder.item_count === 1 ? '' : 's'} saved.</div>
                    )}
                  </div>
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-semibold">Fulfillment</h4>
                  <div className="space-y-2 rounded-md border p-3 text-sm">
                    <div className="flex justify-between gap-3">
                      <span className="text-muted-foreground">Type:</span>
                      <span className="capitalize">{selectedOrder.fulfillment_type.replace(/_/g, ' ')}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-muted-foreground">Payment state:</span>
                      <span className="capitalize">{selectedOrder.payment_status}</span>
                    </div>
                    {selectedOrder.delivery_address && (
                      <div className="border-t pt-2">
                        <span className="text-muted-foreground">Address:</span>
                        <p className="mt-1 break-words">{selectedOrder.delivery_address}</p>
                      </div>
                    )}
                  </div>
                </div>
                {selectedOrder.order_data && Object.keys(selectedOrder.order_data).length > 0 && (
                  <div>
                    <h4 className="mb-2 text-sm font-semibold">Additional Details</h4>
                    <div className="space-y-2 rounded-md border p-3 text-sm">
                      {Object.entries(selectedOrder.order_data)
                        .filter(([key]) => {
                          const lowerKey = key.toLowerCase();
                          return !(
                            lowerKey.includes('idempotency') ||
                            lowerKey.includes('privacy') ||
                            lowerKey.includes('erased') ||
                            lowerKey.includes('raw') ||
                            lowerKey.includes('payload') ||
                            lowerKey === 'id' ||
                            lowerKey.endsWith('_id')
                          );
                        })
                        .map(([key, value]) => {
                          const formattedKey = key
                            .replace(/_/g, ' ')
                            .replace(/\b\w/g, (char) => char.toUpperCase());
                          return (
                            <div key={key} className="flex justify-between gap-3 border-b pb-1.5 last:border-0 last:pb-0">
                              <span className="text-muted-foreground">{formattedKey}:</span>
                              <span className="font-medium break-all text-right">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>
                            </div>
                          );
                        })}
                    </div>
                  </div>
                )}
                <div className="space-y-3 border-t pt-5">
                  <h4 className="text-sm font-semibold">Quick Actions</h4>
                  {selectedNextStatus && (
                    <Button
                      className="w-full justify-start"
                      onClick={() => setPendingUpdate({
                        id: selectedOrder.id,
                        label: `Mark as ${STATUS_COPY[selectedNextStatus]}`,
                        updates: { status: selectedNextStatus },
                      })}
                      disabled={updateMutation.isPending}
                    >
                      <PackageCheck className="h-4 w-4" />
                      Mark as {STATUS_COPY[selectedNextStatus]}
                    </Button>
                  )}
                  {selectedOrder.payment_status !== 'paid' && selectedOrder.status !== 'cancelled' && (
                    <Button
                      type="button"
                      variant="outline"
                      className="w-full justify-start"
                      onClick={() => setPendingUpdate({
                        id: selectedOrder.id,
                        label: 'Mark Payment Collected',
                        updates: { payment_status: 'paid' },
                      })}
                      disabled={updateMutation.isPending}
                    >
                      <CheckCircle2 className="h-4 w-4" />
                      Mark Manual Payment Collected
                    </Button>
                  )}
                  {selectedOrder.status !== 'cancelled' && selectedOrder.status !== 'completed' && (
                    <Button
                      type="button"
                      variant="destructive"
                      className="w-full justify-start"
                      onClick={() => setPendingUpdate({
                        id: selectedOrder.id,
                        label: 'Cancel Order',
                        updates: { status: 'cancelled' },
                      })}
                      disabled={updateMutation.isPending}
                    >
                      <XCircle className="h-4 w-4" />
                      Cancel Order
                    </Button>
                  )}
                  {selectedOrder.status === 'completed' && (
                    <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
                      <Clock3 className="h-4 w-4" />
                      This order is completed.
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
      <Dialog open={!!pendingUpdate} onOpenChange={(open) => !open && setPendingUpdate(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm order update</DialogTitle>
            <DialogDescription>
              This immediately updates the order record. Payment changes here are staff-recorded status updates and do not prove online provider reconciliation.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingUpdate(null)} disabled={updateMutation.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              variant={pendingUpdate?.updates.status === 'cancelled' ? 'destructive' : 'default'}
              onClick={() => {
                if (pendingUpdate) {
                  handleUpdate(pendingUpdate.id, pendingUpdate.updates);
                }
                setPendingUpdate(null);
              }}
              disabled={updateMutation.isPending}
            >
              {pendingUpdate?.label || 'Confirm Update'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
