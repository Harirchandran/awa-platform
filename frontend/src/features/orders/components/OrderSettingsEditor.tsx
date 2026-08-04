import { useFormContext } from 'react-hook-form';
import type { OrderModuleConfig, TenantConfig } from '@/types/config.types';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
function parseStringList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim().toLowerCase().replace(/\s+/g, '_'))
    .filter(Boolean);
}
function displayList(values?: string[]): string {
  return (values || []).join(', ');
}
export function OrderSettingsEditor() {
  const { register, watch, setValue } = useFormContext<TenantConfig>();
  const orderConfig = watch('modules.order') as OrderModuleConfig | undefined;
  const fulfillmentTypes = orderConfig?.fulfillment_types || [];
  const fulfillmentOptionsHelpId = 'order-fulfillment-options-help';
  const isFulfillmentListEmpty = orderConfig?.enabled === true && fulfillmentTypes.length === 0;
  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h3 className="text-lg font-medium">Order Rules</h3>
        <p className="text-sm text-muted-foreground">
          Keep these simple for the first version: collect the item, quantity, fulfillment choice, customer details, and show a confirmation before saving.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border bg-card p-4 sm:p-5">
          <h4 className="text-sm font-semibold">Customer checkout</h4>
          <p className="mt-1 text-xs text-muted-foreground">Controls what AWA asks while taking an order.</p>
          <div className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label>Currency</Label>
              <Input placeholder="INR" {...register('modules.order.currency')} />
              <p className="text-xs text-muted-foreground">Use ISO currency codes like INR, USD, AED.</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="order-fulfillment-options">Fulfillment options</Label>
              <Input
                id="order-fulfillment-options"
                value={displayList(fulfillmentTypes)}
                placeholder="pickup, delivery"
                aria-invalid={isFulfillmentListEmpty}
                aria-describedby={fulfillmentOptionsHelpId}
                className={isFulfillmentListEmpty ? 'border-destructive focus-visible:ring-destructive/30' : undefined}
                onChange={(event) => setValue('modules.order.fulfillment_types', parseStringList(event.target.value), { shouldDirty: true, shouldTouch: true })}
              />
              <p
                id={fulfillmentOptionsHelpId}
                className={isFulfillmentListEmpty ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}
              >
                {isFulfillmentListEmpty
                  ? 'Add at least one fulfillment option so AWA can ask customers how they want the order.'
                  : 'Comma-separated. Supported now: pickup, delivery, in_store.'}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>Delivery fee</Label>
                <Input
                  type="number"
                  step="any"
                  min="0"
                  placeholder="0.00"
                  {...register('modules.order.delivery_fee', { valueAsNumber: true })}
                />
                <p className="text-[10px] text-muted-foreground">Default delivery fee.</p>
              </div>
              <div className="space-y-2">
                <Label>Tax amount</Label>
                <Input
                  type="number"
                  step="any"
                  min="0"
                  placeholder="0.00"
                  {...register('modules.order.tax_amount', { valueAsNumber: true })}
                />
                <p className="text-[10px] text-muted-foreground">Default tax amount.</p>
              </div>
            </div>
            <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
              <div>
                <Label>Confirm before placing order</Label>
                <p className="text-xs text-muted-foreground">AWA shows a review card and waits for Yes.</p>
              </div>
              <Switch
                checked={orderConfig?.requires_confirmation !== false}
                onCheckedChange={(checked) => setValue('modules.order.requires_confirmation', checked, { shouldDirty: true })}
              />
            </div>
          </div>
        </section>
        <section className="rounded-lg border bg-card p-4 sm:p-5">
          <h4 className="text-sm font-semibold">Payment handling</h4>
          <p className="mt-1 text-xs text-muted-foreground">We currently support manual recording of payments. Your staff can verify and record payments directly upon handoff.</p>
          <div className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label>Accepted payment methods</Label>
              <Input
                value={displayList(orderConfig?.payment_methods)}
                placeholder="cod, upi_on_delivery, pay_at_store"
                onChange={(event) => setValue('modules.order.payment_methods', parseStringList(event.target.value), { shouldDirty: true })}
              />
              <p className="text-xs text-muted-foreground">Comma-separated. These are saved for staff context; customers are not charged inside AWA Chat.</p>
            </div>
            <div className="rounded-md border border-amber-500/20 bg-amber-500/10 p-3">
              <p className="text-sm font-medium text-amber-800 dark:text-amber-200">Payment safety</p>
              <p className="mt-1 text-xs leading-5 text-amber-800/80 dark:text-amber-100/80">
                The current order tool is for order capture and staff confirmation. Future online payments should add gateway checks, duplicate payment protection, refund status, and explicit customer consent.
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}