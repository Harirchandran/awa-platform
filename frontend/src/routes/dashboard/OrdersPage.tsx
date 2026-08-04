import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FormProvider, useForm } from 'react-hook-form';
import toast from 'react-hot-toast';
import { AlertTriangle, Archive, Loader2, PackagePlus, Settings } from 'lucide-react';
import { useOrdersList } from '@/features/orders/api';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import { OrdersTable } from '@/features/orders/components/OrdersTable';
import { OrderSettingsEditor } from '@/features/orders/components/OrderSettingsEditor';
import { useTenantConfig, useUpdateConfig } from '@/features/config/api';
import type { OrderModuleConfig, TenantConfig } from '@/types/config.types';
import { getApiErrorMessage } from '@/lib/api-errors';
import { StickyFormActions } from '@/components/shared/StickyFormActions';
import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import { PageHeader } from '@/components/shared/PageHeader';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ConfigSkeleton } from '@/components/ui/loading-states';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { validateTenantConfig } from '@/lib/tenant-config-validation';
const DEFAULT_ORDER_MODULE: OrderModuleConfig = {
  enabled: true,
  fields: [],
  currency: 'INR',
  fulfillment_types: ['pickup', 'delivery'],
  payment_methods: ['cod', 'upi_on_delivery', 'pay_at_store'],
  requires_confirmation: true,
  categories: [],
  delivery_fee: 0,
  tax_amount: 0,
};
function OrderDisabledState({ config, canEnable, lockedReason }: { config: TenantConfig; canEnable: boolean; lockedReason?: string }) {
  const updateConfig = useUpdateConfig();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const enableOrders = () => {
    const existingOrder = (config.modules?.order || {}) as Partial<OrderModuleConfig>;
    const updatedConfig: TenantConfig = {
      ...config,
      modules: {
        ...config.modules,
        order: {
          ...DEFAULT_ORDER_MODULE,
          ...existingOrder,
          enabled: true,
          fields: existingOrder.fields || [],
          categories: existingOrder.categories || [],
        },
      },
    };
    updateConfig.mutate(updatedConfig, {
      onSuccess: () => toast.success('Orders enabled'),
      onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not enable orders')),
    });
  };
  return (
    <div className="flex min-h-[520px] items-center justify-center">
      <Card className="max-w-xl">
        <CardContent className="space-y-5 p-6 text-center sm:p-8">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
            <PackagePlus className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <h2 className="text-[18px] font-semibold text-foreground">Orders are turned off</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Enable orders when you want AWA to collect item, quantity, pickup or delivery choice, and customer details from AWA Chat or WhatsApp. Add catalog items and review payment labels before sharing.
            </p>
          </div>
          {canEnable ? (
            <Button onClick={() => setConfirmOpen(true)} disabled={updateConfig.isPending}>
              {updateConfig.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Enable Orders
            </Button>
          ) : (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[13px] text-amber-800 dark:text-amber-100">
              {lockedReason || 'Orders are not available on your current plan.'}
            </p>
          )}
        </CardContent>
      </Card>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enable orders?</DialogTitle>
            <DialogDescription>
              AWA will be allowed to collect order details and create order records for this business after this change is saved. This does not make online payments live by itself.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setConfirmOpen(false)} disabled={updateConfig.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => {
                setConfirmOpen(false);
                enableOrders();
              }}
              disabled={updateConfig.isPending}
            >
              Enable Orders
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
function OrderOperationsTab({ orderEnabled }: { orderEnabled: boolean }) {
  const [statusFilter, setStatusFilter] = useState('all');
  const [fulfillmentFilter, setFulfillmentFilter] = useState('all');
  const [search, setSearch] = useState('');
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading, isFetching } = useOrdersList(
    statusFilter,
    fulfillmentFilter,
    search || undefined,
    undefined,
    undefined,
    orderEnabled,
  );
  const orders = data?.pages.flatMap((page) => page.items) || [];
  return (
    <div className="space-y-4">
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
        <div>
          <h3 className="text-[15px] font-semibold text-foreground">Manage Orders</h3>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            Track customer-created orders, update preparation status, and record manual payment collection when staff has verified it.
          </p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
          <SearchInputWithClear
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch('')}
            placeholder="Search by Name, Phone, or Email..."
            aria-label="Search orders"
            className="w-full sm:w-[220px]"
          />
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="h-9 w-full text-[13px] sm:h-8 sm:w-[132px]"><SelectValue placeholder="All status" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All status</SelectItem>
              <SelectItem value="new">New</SelectItem>
              <SelectItem value="accepted">Accepted</SelectItem>
              <SelectItem value="preparing">Preparing</SelectItem>
              <SelectItem value="ready">Ready</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="cancelled">Cancelled</SelectItem>
            </SelectContent>
          </Select>
          <Select value={fulfillmentFilter} onValueChange={setFulfillmentFilter}>
            <SelectTrigger className="h-9 w-full text-[13px] sm:h-8 sm:w-[132px]"><SelectValue placeholder="All types" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              <SelectItem value="pickup">Pickup</SelectItem>
              <SelectItem value="delivery">Delivery</SelectItem>
              <SelectItem value="in_store">In store</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <OrdersTable
        orders={orders}
        isLoading={isLoading}
        isFetching={isFetching}
        hasNextPage={hasNextPage}
        fetchNextPage={fetchNextPage}
        isFetchingNextPage={isFetchingNextPage}
      />
    </div>
  );
}
function CatalogSourceTab() {
  return (
    <div className="max-w-4xl">
      <Card>
        <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border bg-background">
              <Archive className="h-4 w-4 text-muted-foreground" />
            </div>
            <div>
              <h3 className="text-[15px] font-semibold text-foreground">Product catalog source</h3>
              <p className="mt-1 max-w-2xl text-[13px] leading-5 text-muted-foreground">
                Products, services, images, stock, and availability are managed from the dedicated Catalog page. Order rules here only control checkout behavior, fulfillment choices, and payment labels.
              </p>
            </div>
          </div>
          <Button asChild className="w-full sm:w-auto">
            <Link to="/dashboard/catalog">Open Catalog</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
export function OrdersPage() {
  const { data: configData, isLoading: isConfigLoading } = useTenantConfig();
  const { data: toolsData, isLoading: isToolsLoading } = useTenantTools();
  const updateConfig = useUpdateConfig();
  const [hasUserEdited, setHasUserEdited] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [pendingConfig, setPendingConfig] = useState<TenantConfig | null>(null);
  const methods = useForm<TenantConfig>({
    defaultValues: configData || {},
  });
  const { reset, handleSubmit, formState: { isDirty, isSubmitting } } = methods;
  useEffect(() => {
    if (configData) {
      reset(configData);
    }
  }, [configData, reset]);
  if (isConfigLoading || isToolsLoading || !configData) {
    return <ConfigSkeleton tabs={3} rows={5} label="Loading order setup" />;
  }
  const orderAccess = getModuleEntitlement(toolsData, 'order');
  const orderEnabled = orderAccess.enabledByTenant;
  if (!orderAccess.allowedByPlan) {
    return <OrderDisabledState config={configData} canEnable={false} lockedReason={orderAccess.lockedReason} />;
  }
  if (!orderEnabled) {
    return <OrderDisabledState config={configData} canEnable={orderAccess.canEnable} lockedReason={orderAccess.lockedReason} />;
  }
  const saveOrderSetup = (data: TenantConfig) => {
    updateConfig.mutate(data, {
      onSuccess: () => {
        toast.success('Order setup saved');
        reset(data);
        setHasUserEdited(false);
        setPendingConfig(null);
        setValidationErrors([]);
      },
      onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Failed to save order setup')),
    });
  };
  const onSubmit = (data: TenantConfig) => {
    const errors = validateTenantConfig(data);
    if (errors.length > 0) {
      setValidationErrors(errors);
      setPendingConfig(null);
      return;
    }
    setValidationErrors([]);
    setPendingConfig(data);
  };
  const discardOrderChanges = () => {
    reset(configData);
    setHasUserEdited(false);
    setValidationErrors([]);
    setPendingConfig(null);
  };
  return (
    <FormProvider {...methods}>
      <form
        onPointerDownCapture={() => setHasUserEdited(true)}
        onKeyDownCapture={() => setHasUserEdited(true)}
        onSubmit={handleSubmit(onSubmit)}
        className="flex h-full flex-1 flex-col space-y-5"
      >
        <PageHeader
          title="Orders"
          description="Capture orders from AWA Chat or WhatsApp, update staff-facing order status, and tune checkout rules. Product catalog management lives in Catalog."
          actions={
            <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-[12px] text-muted-foreground">
              <Settings className="h-4 w-4" />
              {orderAccess.runtimeAvailable ? 'Order tool enabled' : 'Order setup only'}
            </div>
          }
        />
        {validationErrors.length > 0 && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <p className="font-medium">Fix these before saving order setup</p>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {validationErrors.slice(0, 6).map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                  {validationErrors.length > 6 && <li>{validationErrors.length - 6} more validation issues.</li>}
                </ul>
              </div>
            </div>
          </div>
        )}
        <Tabs defaultValue="orders" className="space-y-5">
          <TabsList className="grid h-auto w-full grid-cols-3 gap-2 bg-transparent p-0 md:w-[560px]">
            <TabsTrigger value="orders" className="justify-start data-[state=active]:bg-primary/10 data-[state=active]:text-primary">Orders</TabsTrigger>
            <TabsTrigger value="settings" className="justify-start data-[state=active]:bg-primary/10 data-[state=active]:text-primary">Rules</TabsTrigger>
            <TabsTrigger value="catalog-source" className="justify-start data-[state=active]:bg-primary/10 data-[state=active]:text-primary">Catalog Source</TabsTrigger>
          </TabsList>
          <TabsContent value="orders" className="m-0">
            <OrderOperationsTab orderEnabled={orderAccess.canMutateRuntimeData} />
          </TabsContent>
          <TabsContent value="settings" className="m-0">
            <OrderSettingsEditor />
          </TabsContent>
          <TabsContent value="catalog-source" className="m-0">
            <CatalogSourceTab />
          </TabsContent>
        </Tabs>
        <StickyFormActions
          visible={isDirty && hasUserEdited}
          isSaving={updateConfig.isPending}
          onSave={handleSubmit(onSubmit)}
          onDiscard={discardOrderChanges}
          disabled={isSubmitting}
          message="You have unsaved order setup changes"
          saveLabel="Review & Save"
        />
      </form>
      <Dialog open={!!pendingConfig} onOpenChange={(open) => !open && setPendingConfig(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save order setup?</DialogTitle>
            <DialogDescription>
              This updates the catalog, payment labels, fulfillment options, and order rules AWA uses for customer orders.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingConfig(null)} disabled={updateConfig.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => pendingConfig && saveOrderSetup(pendingConfig)}
              disabled={updateConfig.isPending}
            >
              {updateConfig.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Save Order Setup
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </FormProvider>
  );
}
