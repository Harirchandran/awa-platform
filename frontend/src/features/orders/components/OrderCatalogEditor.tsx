import { useState } from 'react';
import { useFormContext } from 'react-hook-form';
import { Plus, Trash2 } from 'lucide-react';
import type { OrderCategoryConfig, OrderItemConfig, OrderModuleConfig, TenantConfig } from '@/types/config.types';
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
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';

interface ControlledNumberInputProps extends Omit<React.ComponentPropsWithoutRef<typeof Input>, 'value' | 'onChange'> {
  value: number;
  onChange: (val: number) => void;
}

function ControlledNumberInput({
  value,
  onChange,
  className,
  ...props
}: ControlledNumberInputProps) {
  const [prevValue, setPrevValue] = useState(value);
  const [localValue, setLocalValue] = useState(String(value));

  if (value !== prevValue) {
    setPrevValue(value);
    setLocalValue(String(value));
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const valStr = e.target.value;
    setLocalValue(valStr);
    const valNum = Number(valStr);
    if (!isNaN(valNum) && valStr.trim() !== '') {
      onChange(valNum);
    }
  };

  const handleBlur = () => {
    const valNum = Number(localValue) || 0;
    setLocalValue(String(valNum));
    onChange(valNum);
  };

  return (
    <Input
      {...props}
      className={className}
      value={localValue}
      onChange={handleChange}
      onBlur={handleBlur}
    />
  );
}

type PendingCatalogAction =
  | { type: 'toggle-category'; categoryIndex: number; enabled: boolean; name: string }
  | { type: 'remove-category'; categoryIndex: number; name: string }
  | { type: 'toggle-item'; categoryIndex: number; itemIndex: number; enabled: boolean; name: string }
  | { type: 'remove-item'; categoryIndex: number; itemIndex: number; name: string };
function makeId(prefix: string): string {
  const randomPart = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().split('-')[0]
    : Math.random().toString(36).slice(2, 10);
  return `${prefix}_${randomPart}`;
}
function makeCategory(): OrderCategoryConfig {
  return {
    id: makeId('cat'),
    name: 'New category',
    enabled: true,
    items: [],
  };
}
function makeItem(): OrderItemConfig {
  return {
    id: makeId('item'),
    name: 'New item',
    description: '',
    price: 0,
    sku: '',
    enabled: true,
    preparation_minutes: 15,
  };
}
export function OrderCatalogEditor() {
  const { watch, setValue } = useFormContext<TenantConfig>();
  const [pendingAction, setPendingAction] = useState<PendingCatalogAction | null>(null);
  const orderConfig = watch('modules.order') as OrderModuleConfig | undefined;
  const categories = orderConfig?.categories || [];
  const saveCategories = (nextCategories: OrderCategoryConfig[]) => {
    setValue('modules.order.categories', nextCategories, { shouldDirty: true });
  };
  const updateCategory = (index: number, patch: Partial<OrderCategoryConfig>) => {
    saveCategories(categories.map((category, categoryIndex) => (
      categoryIndex === index ? { ...category, ...patch } : category
    )));
  };
  const removeCategory = (index: number) => {
    saveCategories(categories.filter((_, categoryIndex) => categoryIndex !== index));
  };
  const addItem = (categoryIndex: number) => {
    const nextCategories = categories.map((category, index) => (
      index === categoryIndex
        ? { ...category, items: [...(category.items || []), makeItem()] }
        : category
    ));
    saveCategories(nextCategories);
  };
  const updateItem = (categoryIndex: number, itemIndex: number, patch: Partial<OrderItemConfig>) => {
    const nextCategories = categories.map((category, index) => {
      if (index !== categoryIndex) return category;
      return {
        ...category,
        items: (category.items || []).map((item, currentItemIndex) => (
          currentItemIndex === itemIndex ? { ...item, ...patch } : item
        )),
      };
    });
    saveCategories(nextCategories);
  };
  const removeItem = (categoryIndex: number, itemIndex: number) => {
    const nextCategories = categories.map((category, index) => {
      if (index !== categoryIndex) return category;
      return {
        ...category,
        items: (category.items || []).filter((_, currentItemIndex) => currentItemIndex !== itemIndex),
      };
    });
    saveCategories(nextCategories);
  };
  const runPendingAction = () => {
    if (!pendingAction) return;
    if (pendingAction.type === 'toggle-category') {
      updateCategory(pendingAction.categoryIndex, { enabled: pendingAction.enabled });
    }
    if (pendingAction.type === 'remove-category') {
      removeCategory(pendingAction.categoryIndex);
    }
    if (pendingAction.type === 'toggle-item') {
      updateItem(pendingAction.categoryIndex, pendingAction.itemIndex, { enabled: pendingAction.enabled });
    }
    if (pendingAction.type === 'remove-item') {
      removeItem(pendingAction.categoryIndex, pendingAction.itemIndex);
    }
    setPendingAction(null);
  };
  const pendingCopy = (() => {
    if (!pendingAction) return null;
    if (pendingAction.type === 'toggle-category') {
      return {
        title: pendingAction.enabled ? 'Enable category?' : 'Disable category?',
        description: `${pendingAction.name || 'This category'} will ${pendingAction.enabled ? 'be available' : 'stop being available'} to AWA after you save the order setup.`,
        confirmLabel: pendingAction.enabled ? 'Enable Category' : 'Disable Category',
        destructive: !pendingAction.enabled,
      };
    }
    if (pendingAction.type === 'remove-category') {
      return {
        title: 'Remove category?',
        description: `${pendingAction.name || 'This category'} and its items will be removed from the setup form. Save Order Setup afterwards to remove them from customer ordering.`,
        confirmLabel: 'Remove Category',
        destructive: true,
      };
    }
    if (pendingAction.type === 'toggle-item') {
      return {
        title: pendingAction.enabled ? 'Enable item?' : 'Disable item?',
        description: `${pendingAction.name || 'This item'} will ${pendingAction.enabled ? 'be orderable' : 'stop being orderable'} after you save the order setup.`,
        confirmLabel: pendingAction.enabled ? 'Enable Item' : 'Disable Item',
        destructive: !pendingAction.enabled,
      };
    }
    return {
      title: 'Remove item?',
      description: `${pendingAction.name || 'This item'} will be removed from the setup form. Save Order Setup afterwards to remove it from customer ordering.`,
      confirmLabel: 'Remove Item',
      destructive: true,
    };
  })();
  return (
    <div className="max-w-5xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-lg font-medium">Order Catalog</h3>
          <p className="text-sm text-muted-foreground">
            Add what customers can order. AWA uses this catalog to understand item names, prices, and quick order requests.
          </p>
        </div>
        <Button type="button" onClick={() => saveCategories([...categories, makeCategory()])}>
          <Plus className="h-4 w-4" />
          Add Category
        </Button>
      </div>
      {categories.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-card/50 p-8 text-center">
          <p className="text-sm text-muted-foreground">No order catalog yet.</p>
          <p className="mt-1 text-xs text-muted-foreground">Create a category, then add items with prices. Customers will not be able to place orders until at least one item is enabled.</p>
          <Button type="button" className="mt-4" onClick={() => saveCategories([makeCategory()])}>
            <Plus className="h-4 w-4" />
            Create First Category
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          {categories.map((category, categoryIndex) => (
            <section key={category.id || categoryIndex} className="rounded-lg border bg-card p-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="grid flex-1 gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                  <div className="space-y-2">
                    <Label>Category Name</Label>
                    <Input
                      value={category.name}
                      placeholder="e.g. Cakes, Meals, Products"
                      onChange={(event) => updateCategory(categoryIndex, { name: event.target.value })}
                    />
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                    <div>
                      <Label>Available</Label>
                      <p className="text-xs text-muted-foreground">Visible to AWA</p>
                    </div>
                    <Switch
                      checked={category.enabled !== false}
                      onCheckedChange={(checked) => setPendingAction({
                        type: 'toggle-category',
                        categoryIndex,
                        enabled: checked,
                        name: category.name,
                      })}
                    />
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => setPendingAction({ type: 'remove-category', categoryIndex, name: category.name })}
                  aria-label="Remove category"
                >
                  <Trash2 className="h-4 w-4 text-muted-foreground" />
                </Button>
              </div>
              <div className="mt-5 space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h4 className="text-sm font-semibold">Items</h4>
                    <p className="text-xs text-muted-foreground">{category.items?.length || 0} item{category.items?.length === 1 ? '' : 's'} in this category</p>
                  </div>
                  <Button type="button" variant="outline" size="sm" onClick={() => addItem(categoryIndex)}>
                    <Plus className="h-4 w-4" />
                    Add Item
                  </Button>
                </div>
                {!category.items?.length ? (
                  <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                    No items in this category yet.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {category.items.map((item, itemIndex) => (
                      <div key={item.id || itemIndex} className="rounded-md border bg-background p-3 transition-colors hover:bg-muted/20">
                        <div className="flex items-start justify-between gap-3">
                          <div className="grid flex-1 gap-3 md:grid-cols-[minmax(0,1.5fr)_150px_100px_100px]">
                            <div className="space-y-2">
                              <Label>Item Name</Label>
                              <Input
                                value={item.name}
                                placeholder="e.g. Veg biriyani"
                                onChange={(event) => updateItem(categoryIndex, itemIndex, { name: event.target.value })}
                              />
                            </div>
                            <div className="space-y-2">
                              <Label>Item Code / Barcode (Optional)</Label>
                              <Input
                                value={item.sku || ''}
                                placeholder="Optional"
                                onChange={(event) => updateItem(categoryIndex, itemIndex, { sku: event.target.value })}
                              />
                            </div>
                            <div className="space-y-2">
                              <Label>Price</Label>
                              <ControlledNumberInput
                                type="number"
                                min="0"
                                step="1"
                                value={Number.isFinite(item.price) ? item.price : 0}
                                onChange={(value) => updateItem(categoryIndex, itemIndex, { price: value })}
                              />
                            </div>
                            <div className="space-y-2">
                              <Label>Prep mins</Label>
                              <ControlledNumberInput
                                type="number"
                                min="0"
                                step="5"
                                value={item.preparation_minutes ?? 15}
                                onChange={(value) => updateItem(categoryIndex, itemIndex, { preparation_minutes: value })}
                              />
                            </div>
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={() => setPendingAction({
                              type: 'remove-item',
                              categoryIndex,
                              itemIndex,
                              name: item.name,
                            })}
                            aria-label="Remove item"
                          >
                            <Trash2 className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        </div>
                        <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]">
                          <div className="space-y-2">
                            <Label>Description</Label>
                            <Textarea
                              value={item.description || ''}
                              placeholder="Short details customers may ask about"
                              onChange={(event) => updateItem(categoryIndex, itemIndex, { description: event.target.value })}
                            />
                          </div>
                          <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                            <div>
                              <Label>Item enabled</Label>
                              <p className="text-xs text-muted-foreground">Can be ordered</p>
                            </div>
                            <Switch
                              checked={item.enabled !== false}
                              onCheckedChange={(checked) => setPendingAction({
                                type: 'toggle-item',
                                categoryIndex,
                                itemIndex,
                                enabled: checked,
                                name: item.name,
                              })}
                            />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          ))}
        </div>
      )}
      <Dialog open={!!pendingAction} onOpenChange={(open) => !open && setPendingAction(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pendingCopy?.title}</DialogTitle>
            <DialogDescription>{pendingCopy?.description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setPendingAction(null)}>Cancel</Button>
            <Button
              type="button"
              variant={pendingCopy?.destructive ? 'destructive' : 'default'}
              onClick={runPendingAction}
            >
              {pendingCopy?.confirmLabel || 'Confirm'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}