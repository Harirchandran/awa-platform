import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import toast from 'react-hot-toast';
import { useMediaQuery } from '@/hooks/use-media-query';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetFooter,
} from '@/components/ui/sheet';
import {
  AlertTriangle,
  Archive,
  History,
  Image as ImageIcon,
  Loader2,
  MoreHorizontal,
  Package,
  PackagePlus,
  Pencil,
  Plus,
  SlidersHorizontal,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import {
  formatCatalogMoney,
  resolveMediaUrl,
  type CatalogCategory,
  type CatalogItem,
  useAdjustCatalogStock,
  useCatalogCategories,
  useCatalogItems,
  useCreateCatalogCategory,
  useCreateCatalogItem,
  useDeleteCatalogCategory,
  useDeleteCatalogItem,
  useDeleteCatalogItemImage,
  useInventoryEvents,
  useUpdateCatalogCategory,
  useUpdateCatalogItem,
  useUploadCatalogItemImage,
} from '@/features/catalog/api';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import { getApiErrorMessage } from '@/lib/api-errors';
import { SearchInputWithClear } from '@/components/shared/SearchInputWithClear';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { PageHeader } from '@/components/shared/PageHeader';
import { ResponsiveDataList } from '@/components/shared/ResponsiveDataList';
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
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { TableSkeleton } from '@/components/ui/loading-states';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
const ALL_CATEGORIES = 'all';
const NO_CATEGORY = '__none';
type ItemFormState = {
  category_id: string;
  name: string;
  description: string;
  price: string;
  currency: string;
  sku: string;
  image_url: string;
  is_active: boolean;
  is_available: boolean;
  stock_tracking_enabled: boolean;
  stock_quantity: string;
  low_stock_threshold: string;
};
const emptyItemForm: ItemFormState = {
  category_id: NO_CATEGORY,
  name: '',
  description: '',
  price: '0',
  currency: 'INR',
  sku: '',
  image_url: '',
  is_active: true,
  is_available: true,
  stock_tracking_enabled: false,
  stock_quantity: '0',
  low_stock_threshold: '',
};
function itemToForm(item: CatalogItem): ItemFormState {
  return {
    category_id: item.category_id || NO_CATEGORY,
    name: item.name,
    description: item.description || '',
    price: String(item.price ?? 0),
    currency: item.currency || 'INR',
    sku: item.sku || '',
    image_url: item.image_url || '',
    is_active: item.is_active,
    is_available: item.is_available,
    stock_tracking_enabled: item.stock_tracking_enabled,
    stock_quantity: String(item.stock_quantity ?? 0),
    low_stock_threshold: item.low_stock_threshold == null ? '' : String(item.low_stock_threshold),
  };
}
function toOptionalText(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}
function toNumber(value: string, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
function StatusPill({ active, children }: { active: boolean; children: string }) {
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center rounded-full border px-2 text-[11px] font-medium',
        active
          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
          : 'border-border bg-muted text-muted-foreground'
      )}
    >
      {children}
    </span>
  );
}
function StockPill({ item }: { item: CatalogItem }) {
  if (!item.stock_tracking_enabled) {
    return <StatusPill active={false}>Not tracked</StatusPill>;
  }
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center rounded-full border px-2 text-[11px] font-medium',
        item.low_stock
          ? 'border-amber-200 bg-amber-50 text-amber-700'
          : 'border-sky-200 bg-sky-50 text-sky-700'
      )}
    >
      {item.stock_quantity} in stock
    </span>
  );
}
function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border bg-card px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-normal text-muted-foreground">{label}</p>
      <p className="mt-1 text-[20px] font-semibold text-foreground">{value}</p>
    </div>
  );
}
function ItemImageControl({ item }: { item: CatalogItem }) {
  const uploadImage = useUploadCatalogItemImage();
  const deleteImage = useDeleteCatalogItemImage();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const imageUrl = resolveMediaUrl(item.image?.thumbnail_url || item.image?.url || item.image_url);
  const pending = uploadImage.isPending || deleteImage.isPending;
  const inputId = `catalog-image-${item.id}`;
  const onFileChange = (file?: File) => {
    if (!file) return;
    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp'];
    if (!allowedTypes.includes(file.type)) {
      toast.error('Use a JPG, PNG, or WebP image');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      toast.error('Keep images under 5 MB');
      return;
    }
    uploadImage.mutate(
      {
        itemId: item.id,
        file,
        altText: item.name,
        caption: item.description || undefined,
      },
      {
        onSuccess: () => toast.success('Image uploaded'),
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not upload image')),
      }
    );
  };
  return (
    <div className="flex items-center gap-2">
      <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted">
        {imageUrl ? (
          <img
            src={imageUrl}
            alt={item.image?.alt_text || item.name}
            className="h-full w-full object-cover"
            loading="lazy"
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
          />
        ) : (
          <ImageIcon className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className="flex gap-1">
        <input
          id={inputId}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="sr-only"
          onChange={(event) => {
            onFileChange(event.target.files?.[0]);
            event.currentTarget.value = '';
          }}
        />
        <Button type="button" size="icon" variant="ghost" className="h-8 w-8" disabled={pending} asChild>
          <label htmlFor={inputId} className="cursor-pointer">
            {uploadImage.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          </label>
        </Button>
        {item.image?.id && (
          <>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-8 w-8 text-muted-foreground hover:text-destructive"
              disabled={pending}
              onClick={() => setConfirmOpen(true)}
              aria-label="Remove item image"
            >
              {deleteImage.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
            </Button>
            <ConfirmActionDialog
              open={confirmOpen}
              onOpenChange={setConfirmOpen}
              title="Remove image?"
              description="This will remove the image from this catalog item. The item itself will not be deleted."
              confirmLabel="Remove image"
              variant="destructive"
              isLoading={deleteImage.isPending}
              onConfirm={() => {
                deleteImage.mutate(
                  { itemId: item.id, imageId: item.image!.id },
                  {
                    onSuccess: () => {
                      toast.success('Image removed');
                      setConfirmOpen(false);
                    },
                    onError: (error) => toast.error(getApiErrorMessage(error, 'Could not remove image')),
                  }
                );
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}
function CategoryRow({
  category,
  selected,
  onSelect,
  onDeleteClick,
}: {
  category: CatalogCategory;
  selected: boolean;
  onSelect: () => void;
  onDeleteClick: () => void;
}) {
  const updateCategory = useUpdateCatalogCategory();
  return (
    <div
      className={cn(
        'flex items-center gap-3 rounded-md border px-3 py-2',
        selected ? 'border-primary/40 bg-primary/5' : 'bg-card'
      )}
    >
      <button type="button" className="min-w-0 flex-1 text-left" onClick={onSelect}>
        <p className="truncate text-[13px] font-medium text-foreground">{category.name}</p>
        <p className="text-[11px] text-muted-foreground">{category.item_count || 0} items</p>
      </button>
      <Switch
        checked={category.is_active}
        onCheckedChange={(checked) =>
          updateCategory.mutate(
            { id: category.id, updates: { is_active: checked } },
            { onError: (error) => toast.error(getApiErrorMessage(error, 'Could not update category')) }
          )
        }
        aria-label={`Toggle ${category.name}`}
      />
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-8 w-8 text-muted-foreground hover:text-destructive"
        onClick={onDeleteClick}
      >
        <Trash2 className="h-4 w-4" />
      </Button>
    </div>
  );
}
function ItemDialog({
  open,
  mode,
  form,
  categories,
  pending,
  onChange,
  onClose,
  onSave,
}: {
  open: boolean;
  mode: 'create' | 'edit';
  form: ItemFormState;
  categories: CatalogCategory[];
  pending: boolean;
  onChange: (updates: Partial<ItemFormState>) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const isStockNegative = form.stock_tracking_enabled && toNumber(form.stock_quantity) < 0;
  const isLowStockNegative = form.stock_tracking_enabled && form.low_stock_threshold.trim() !== '' && toNumber(form.low_stock_threshold) < 0;
  const isFormInvalid = !form.name.trim() || isStockNegative || isLowStockNegative;

  const isDesktop = useMediaQuery('(min-width: 768px)');

  const formContent = (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-2 sm:col-span-2">
        <Label htmlFor="catalog-item-name">Name</Label>
        <Input
          id="catalog-item-name"
          value={form.name}
          onChange={(event) => onChange({ name: event.target.value })}
          placeholder="Service or product name"
        />
      </div>
      <div className="space-y-2">
        <Label>Category</Label>
        <Select value={form.category_id} onValueChange={(value) => onChange({ category_id: value })}>
          <SelectTrigger>
            <SelectValue placeholder="Choose category" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_CATEGORY}>No category</SelectItem>
            {categories.map((category) => (
              <SelectItem key={category.id} value={category.id}>
                {category.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_88px] gap-2">
        <div className="space-y-2">
          <Label htmlFor="catalog-item-price">Price</Label>
          <Input
            id="catalog-item-price"
            type="number"
            min="0"
            step="0.01"
            value={form.price}
            onChange={(event) => onChange({ price: event.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="catalog-item-currency">Currency</Label>
          <Input
            id="catalog-item-currency"
            value={form.currency}
            onChange={(event) => onChange({ currency: event.target.value.toUpperCase() })}
            maxLength={8}
          />
        </div>
      </div>
      <div className="space-y-2">
        <Label htmlFor="catalog-item-sku">SKU</Label>
        <Input
          id="catalog-item-sku"
          value={form.sku}
          onChange={(event) => onChange({ sku: event.target.value })}
          placeholder="Optional"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="catalog-item-image">Image URL</Label>
        <Input
          id="catalog-item-image"
          value={form.image_url}
          onChange={(event) => onChange({ image_url: event.target.value })}
          placeholder="Optional"
        />
      </div>
      <div className="space-y-2 sm:col-span-2">
        <Label htmlFor="catalog-item-description">Description</Label>
        <Textarea
          id="catalog-item-description"
          value={form.description}
          onChange={(event) => onChange({ description: event.target.value })}
          rows={3}
        />
      </div>
      <div className="rounded-md border p-3">
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="catalog-item-active">Active</Label>
          <Switch
            id="catalog-item-active"
            checked={form.is_active}
            onCheckedChange={(checked) => onChange({ is_active: checked })}
          />
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">Inactive items stay hidden from order lookup.</p>
      </div>
      <div className="rounded-md border p-3">
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="catalog-item-available">Available</Label>
          <Switch
            id="catalog-item-available"
            checked={form.is_available}
            onCheckedChange={(checked) => onChange({ is_available: checked })}
          />
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">Unavailable items remain in the catalog for staff.</p>
      </div>
      <div className="space-y-3 rounded-md border p-3 sm:col-span-2">
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="catalog-item-stock-tracking">Track stock</Label>
          <Switch
            id="catalog-item-stock-tracking"
            checked={form.stock_tracking_enabled}
            onCheckedChange={(checked) => onChange({ stock_tracking_enabled: checked })}
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="catalog-item-stock">Stock quantity</Label>
            <Input
              id="catalog-item-stock"
              type="number"
              step="0.01"
              value={form.stock_quantity}
              onChange={(event) => onChange({ stock_quantity: event.target.value })}
            />
            {isStockNegative && (
              <p className="text-xs text-destructive mt-1">Stock quantity cannot be negative</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="catalog-item-low-stock">Low stock threshold</Label>
            <Input
              id="catalog-item-low-stock"
              type="number"
              step="0.01"
              value={form.low_stock_threshold}
              onChange={(event) => onChange({ low_stock_threshold: event.target.value })}
              placeholder="Optional"
            />
            {isLowStockNegative && (
              <p className="text-xs text-destructive mt-1">Low stock threshold cannot be negative</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );

  const footerContent = (
    <>
      <Button type="button" variant="ghost" onClick={onClose} disabled={pending}>
        Cancel
      </Button>
      <Button type="button" onClick={onSave} disabled={pending || isFormInvalid}>
        {pending && <Loader2 className="h-4 w-4 animate-spin" />}
        {mode === 'create' ? 'Add Item' : 'Save Item'}
      </Button>
    </>
  );

  if (isDesktop) {
    return (
      <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{mode === 'create' ? 'Add catalog item' : 'Edit catalog item'}</DialogTitle>
            <DialogDescription>
              {mode === 'create' ? 'Create an item staff can manage and AWA can use for orders.' : 'Update the item customers can order.'}
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
          <SheetTitle>{mode === 'create' ? 'Add catalog item' : 'Edit catalog item'}</SheetTitle>
          <SheetDescription>
            {mode === 'create' ? 'Create an item staff can manage and AWA can use for orders.' : 'Update the item customers can order.'}
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
function StockDialog({
  item,
  open,
  onClose,
}: {
  item: CatalogItem | null;
  open: boolean;
  onClose: () => void;
}) {
  const adjustStock = useAdjustCatalogStock();
  const eventsQuery = useInventoryEvents(item?.id);
  const [quantityDelta, setQuantityDelta] = useState('');
  const [reason, setReason] = useState('');
  const isDeltaInvalid = quantityDelta.trim() !== '' && !!item && item.stock_tracking_enabled && (item.stock_quantity ?? 0) + toNumber(quantityDelta) < 0;

  const save = () => {
    if (!item) return;
    const quantity = toNumber(quantityDelta);
    if (!quantity) {
      toast.error('Enter a non-zero stock change');
      return;
    }
    if (item.stock_tracking_enabled && (item.stock_quantity ?? 0) + quantity < 0) {
      toast.error('Resulting stock cannot be negative');
      return;
    }
    adjustStock.mutate(
      { id: item.id, quantity_delta: quantity, reason: reason.trim() || undefined },
      {
        onSuccess: () => {
          toast.success('Stock updated');
          setQuantityDelta('');
          setReason('');
          onClose();
        },
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not update stock')),
      }
    );
  };
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Adjust stock</DialogTitle>
          <DialogDescription>{item ? item.name : 'Catalog item'}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="stock-delta">Change</Label>
              <Input
                id="stock-delta"
                type="number"
                step="0.01"
                value={quantityDelta}
                onChange={(event) => setQuantityDelta(event.target.value)}
                placeholder="10 or -2"
              />
              {isDeltaInvalid && (
                <p className="text-xs text-destructive mt-1">
                  Resulting stock cannot be negative (current: {item?.stock_quantity ?? 0})
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="stock-reason">Reason</Label>
              <Input
                id="stock-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Restock, correction"
              />
            </div>
          </div>
          <div className="rounded-md border">
            <div className="flex items-center gap-2 border-b px-3 py-2">
              <History className="h-4 w-4 text-muted-foreground" />
              <p className="text-[13px] font-medium">Recent inventory events</p>
            </div>
            <div className="max-h-52 overflow-y-auto p-3">
              {eventsQuery.isLoading ? (
                <p className="text-[13px] text-muted-foreground">Loading...</p>
              ) : eventsQuery.data && eventsQuery.data.length > 0 ? (
                <div className="space-y-2">
                  {eventsQuery.data.slice(0, 8).map((event) => (
                    <div key={event.id} className="flex items-center justify-between gap-3 rounded-md bg-muted/40 px-3 py-2 text-[12px]">
                      <span className="min-w-0 truncate">{event.reason || event.event_type}</span>
                      <span className={event.quantity_delta >= 0 ? 'text-emerald-700' : 'text-destructive'}>
                        {event.quantity_delta > 0 ? '+' : ''}{event.quantity_delta}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[13px] text-muted-foreground">No stock events yet.</p>
              )}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose} disabled={adjustStock.isPending}>
            Cancel
          </Button>
          <Button type="button" onClick={save} disabled={adjustStock.isPending || isDeltaInvalid}>
            {adjustStock.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Save Adjustment
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
export function CatalogPage() {
  const [selectedCategory, setSelectedCategory] = useState(ALL_CATEGORIES);
  const [search, setSearch] = useState('');
  const [newCategoryName, setNewCategoryName] = useState('');
  const [newCategoryDescription, setNewCategoryDescription] = useState('');
  const [itemDialogOpen, setItemDialogOpen] = useState(false);
  const [itemDialogMode, setItemDialogMode] = useState<'create' | 'edit'>('create');
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [itemForm, setItemForm] = useState<ItemFormState>(emptyItemForm);
  const [stockItem, setStockItem] = useState<CatalogItem | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string; type: 'category' | 'item' } | null>(null);
  const { data: toolsData, isLoading: isToolsLoading } = useTenantTools();
  const categoriesQuery = useCatalogCategories();
  const itemsQuery = useCatalogItems(selectedCategory, search.trim());
  const createCategory = useCreateCatalogCategory();
  const createItem = useCreateCatalogItem();
  const updateItem = useUpdateCatalogItem();
  const deleteCategory = useDeleteCatalogCategory();
  const deleteItem = useDeleteCatalogItem();
  const categories = useMemo(() => categoriesQuery.data || [], [categoriesQuery.data]);
  const items = useMemo(() => itemsQuery.data || [], [itemsQuery.data]);
  const catalogError = categoriesQuery.error || itemsQuery.error;
  const orderAccess = getModuleEntitlement(toolsData, 'order');
  const categoryItemCount = useMemo(
    () => categories.reduce((total, category) => total + (category.item_count || 0), 0),
    [categories]
  );
  const metrics = useMemo(() => {
    return {
      total: items.length,
      available: items.filter((item) => item.is_available && item.is_active).length,
      lowStock: items.filter((item) => item.low_stock).length,
    };
  }, [items]);
  const openCreateItem = () => {
    setItemDialogMode('create');
    setEditingItemId(null);
    setItemForm({
      ...emptyItemForm,
      category_id: selectedCategory === ALL_CATEGORIES ? NO_CATEGORY : selectedCategory,
    });
    setItemDialogOpen(true);
  };
  const openEditItem = (item: CatalogItem) => {
    setItemDialogMode('edit');
    setEditingItemId(item.id);
    setItemForm(itemToForm(item));
    setItemDialogOpen(true);
  };
  const saveCategory = () => {
    const name = newCategoryName.trim();
    if (!name) {
      toast.error('Enter a category name');
      return;
    }
    createCategory.mutate(
      { name, description: toOptionalText(newCategoryDescription) },
      {
        onSuccess: () => {
          toast.success('Category added');
          setNewCategoryName('');
          setNewCategoryDescription('');
        },
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not add category')),
      }
    );
  };
  const saveItem = () => {
    if (itemForm.stock_tracking_enabled && toNumber(itemForm.stock_quantity) < 0) {
      toast.error('Stock quantity cannot be negative');
      return;
    }
    if (itemForm.stock_tracking_enabled && itemForm.low_stock_threshold.trim() && toNumber(itemForm.low_stock_threshold) < 0) {
      toast.error('Low stock threshold cannot be negative');
      return;
    }
    const payload = {
      category_id: itemForm.category_id === NO_CATEGORY ? null : itemForm.category_id,
      name: itemForm.name.trim(),
      description: toOptionalText(itemForm.description),
      price: toNumber(itemForm.price),
      currency: itemForm.currency.trim().toUpperCase() || 'INR',
      sku: toOptionalText(itemForm.sku),
      image_url: toOptionalText(itemForm.image_url),
      is_active: itemForm.is_active,
      is_available: itemForm.is_available,
      stock_tracking_enabled: itemForm.stock_tracking_enabled,
      stock_quantity: toNumber(itemForm.stock_quantity),
      low_stock_threshold: itemForm.low_stock_threshold.trim() ? toNumber(itemForm.low_stock_threshold) : null,
    };
    const options = {
      onSuccess: () => {
        toast.success(itemDialogMode === 'create' ? 'Item added' : 'Item saved');
        setItemDialogOpen(false);
      },
      onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not save item')),
    };
    if (itemDialogMode === 'edit' && editingItemId) {
      updateItem.mutate({ id: editingItemId, updates: payload }, options);
    } else {
      createItem.mutate(payload, options);
    }
  };
  const handleDeleteConfirm = () => {
    if (!deleteTarget) return;
    if (deleteTarget.type === 'category') {
      deleteCategory.mutate(deleteTarget.id, {
        onSuccess: () => {
          toast.success('Category removed');
          setDeleteConfirmOpen(false);
          setDeleteTarget(null);
        },
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not remove category')),
      });
    } else {
      deleteItem.mutate(deleteTarget.id, {
        onSuccess: () => {
          toast.success('Item removed');
          setDeleteConfirmOpen(false);
          setDeleteTarget(null);
        },
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not remove item')),
      });
    }
  };
  if (isToolsLoading) {
    return <TableSkeleton columns={6} rows={5} />;
  }
  if (!orderAccess.allowedByPlan) {
    return (
      <div className="space-y-5">
        <PageHeader
          title="Catalog"
          description="Products, services, availability, and stock context used by order-taking workflows."
        />
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-[13px] leading-relaxed text-amber-800 dark:text-amber-100">
          {orderAccess.lockedReason}
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <PageHeader
        title="Catalog"
        description="Products, services, availability, and stock context used by order-taking workflows."
        actions={
          <Button type="button" onClick={openCreateItem}>
            <PackagePlus className="h-4 w-4" />
            Add Item
          </Button>
        }
      />
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric label="Items" value={metrics.total} />
        <Metric label="Available" value={metrics.available} />
        <Metric label="Low Stock" value={metrics.lowStock} />
      </div>
      <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="space-y-4">
          <div className="rounded-md border bg-card">
            <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
              <div className="flex items-center gap-2">
                <Archive className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-[14px] font-semibold text-foreground">Categories</h3>
              </div>
              <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedCategory(ALL_CATEGORIES)}>
                All
              </Button>
            </div>
            <div className="space-y-2 p-3">
              <button
                type="button"
                onClick={() => setSelectedCategory(ALL_CATEGORIES)}
                className={cn(
                  'flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-[13px]',
                  selectedCategory === ALL_CATEGORIES ? 'border-primary/40 bg-primary/5 text-foreground' : 'bg-background text-muted-foreground'
                )}
              >
                <span>All categories</span>
                <span>{categoryItemCount}</span>
              </button>
              {categories.map((category) => (
                <CategoryRow
                  key={category.id}
                  category={category}
                  selected={selectedCategory === category.id}
                  onSelect={() => setSelectedCategory(category.id)}
                  onDeleteClick={() => {
                    setDeleteTarget({ id: category.id, name: category.name, type: 'category' });
                    setDeleteConfirmOpen(true);
                  }}
                />
              ))}
            </div>
          </div>
          <div className="rounded-md border bg-card p-4">
            <div className="mb-3 flex items-center gap-2">
              <Plus className="h-4 w-4 text-muted-foreground" />
              <h3 className="text-[14px] font-semibold text-foreground">New category</h3>
            </div>
            <div className="space-y-3">
              <div className="space-y-2">
                <Label htmlFor="new-category-name">Name</Label>
                <Input
                  id="new-category-name"
                  value={newCategoryName}
                  onChange={(event) => setNewCategoryName(event.target.value)}
                  placeholder="Category name"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-category-description">Description</Label>
                <Textarea
                  id="new-category-description"
                  value={newCategoryDescription}
                  onChange={(event) => setNewCategoryDescription(event.target.value)}
                  rows={3}
                />
              </div>
              <Button type="button" className="w-full" onClick={saveCategory} disabled={createCategory.isPending}>
                {createCategory.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Add Category
              </Button>
            </div>
          </div>
        </aside>
        <section className="space-y-4">
          <div className="flex flex-col justify-between gap-3 rounded-md border bg-card p-3 md:flex-row md:items-center">
            <div className="flex items-center gap-2">
              <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
              <p className="text-[13px] font-medium text-foreground">Catalog items</p>
            </div>
          <SearchInputWithClear
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch('')}
            placeholder="Search item or SKU"
            aria-label="Search catalog"
            className="w-full md:w-[320px]"
          />
        </div>
          {categoriesQuery.isLoading || itemsQuery.isLoading ? (
            <TableSkeleton columns={6} rows={6} />
          ) : catalogError ? (
            <div role="alert" className="flex min-h-[360px] items-center justify-center rounded-md border border-destructive/40 bg-destructive/5">
              <div className="max-w-md px-6 text-center text-destructive">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-destructive/40 bg-background">
                  <AlertTriangle className="h-5 w-5" />
                </div>
                <h3 className="mt-4 text-[15px] font-semibold">Catalog unavailable</h3>
                <p className="mt-2 text-[13px] leading-relaxed">
                  {getApiErrorMessage(catalogError, 'Catalog data could not be loaded.')}
                </p>
              </div>
            </div>
          ) : items.length === 0 ? (
            <div className="flex min-h-[360px] items-center justify-center rounded-md border bg-card">
              <div className="max-w-sm px-6 text-center">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
                  <Package className="h-5 w-5 text-muted-foreground" />
                </div>
                <h3 className="mt-4 text-[15px] font-semibold text-foreground">No catalog items</h3>
                <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
                  Add the first product or service before customers place orders through AWA Chat or WhatsApp. Catalog items support Orders; they do not create orders by themselves.
                </p>
                <div className="mt-4 flex flex-col justify-center gap-2 sm:flex-row">
                  <Button type="button" onClick={openCreateItem}>
                    <PackagePlus className="h-4 w-4" />
                    Add Item
                  </Button>
                  <Button asChild variant="outline">
                    <Link to="/dashboard/orders">
                      Review Orders
                    </Link>
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <ResponsiveDataList
                desktop={
                  <div className="overflow-x-auto rounded-md border bg-card">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Image</TableHead>
                      <TableHead>Item</TableHead>
                      <TableHead>Price</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Stock</TableHead>
                      <TableHead>Available</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {items.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell>
                          <ItemImageControl item={item} />
                        </TableCell>
                        <TableCell>
                          <div className="min-w-0">
                            <p className="truncate text-[13px] font-medium text-foreground">{item.name}</p>
                            <p className="truncate text-[12px] text-muted-foreground">
                              {item.category_name || 'Uncategorized'}{item.sku ? ` / ${item.sku}` : ''}
                            </p>
                          </div>
                        </TableCell>
                        <TableCell className="text-[13px]">{formatCatalogMoney(item.price, item.currency)}</TableCell>
                        <TableCell>
                          <StatusPill active={item.is_active}>{item.is_active ? 'Active' : 'Inactive'}</StatusPill>
                        </TableCell>
                        <TableCell>
                          <StockPill item={item} />
                        </TableCell>
                        <TableCell>
                          <Switch
                            checked={item.is_available}
                            onCheckedChange={(checked) =>
                              updateItem.mutate(
                                { id: item.id, updates: { is_available: checked } },
                                { onError: (error) => toast.error(getApiErrorMessage(error, 'Could not update availability')) }
                              )
                            }
                            aria-label={`Toggle ${item.name} availability`}
                          />
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-1">
                            <Button type="button" size="icon" variant="ghost" className="h-8 w-8" onClick={() => setStockItem(item)} aria-label={`Adjust ${item.name} stock`}>
                              <AlertTriangle className="h-4 w-4" />
                            </Button>
                            <Button type="button" size="icon" variant="ghost" className="h-8 w-8" onClick={() => openEditItem(item)} aria-label={`Edit ${item.name}`}>
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              type="button"
                              size="icon"
                              variant="ghost"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                              onClick={() => {
                                setDeleteTarget({ id: item.id, name: item.name, type: 'item' });
                                setDeleteConfirmOpen(true);
                              }}
                              aria-label={`Delete ${item.name}`}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                  </div>
                }
                mobile={
                  <div className="grid gap-4 grid-cols-1 sm:grid-cols-2">
                    {items.map((item) => (
                      <div key={item.id} className="rounded-md border bg-card p-4 space-y-3 text-sm">
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-3">
                        <ItemImageControl item={item} />
                        <div className="min-w-0">
                          <p className="font-medium text-foreground text-sm leading-tight">{item.name}</p>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {item.category_name || 'Uncategorized'}{item.sku ? ` / ${item.sku}` : ''}
                          </p>
                        </div>
                      </div>
                      <div className="shrink-0">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button type="button" size="icon" variant="ghost" className="h-11 w-11" aria-label={`Actions for ${item.name}`}>
                              <MoreHorizontal className="h-5 w-5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => setStockItem(item)}>
                              <AlertTriangle className="mr-2 h-4 w-4" />
                              Adjust Stock
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => openEditItem(item)}>
                              <Pencil className="mr-2 h-4 w-4" />
                              Edit Item
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() => {
                                setDeleteTarget({ id: item.id, name: item.name, type: 'item' });
                                setDeleteConfirmOpen(true);
                              }}
                              className="text-destructive focus:text-destructive"
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              Delete Item
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>

                    <div className="grid gap-y-3 pt-3 border-t text-[13px] grid-cols-2 min-[340px]:grid-cols-2 grid-cols-1">
                      <div>
                        <span className="text-muted-foreground text-xs block">Price</span>
                        <span className="font-medium">{formatCatalogMoney(item.price, item.currency)}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground text-xs block">Status</span>
                        <div className="pt-0.5">
                          <StatusPill active={item.is_active}>{item.is_active ? 'Active' : 'Inactive'}</StatusPill>
                        </div>
                      </div>
                      <div>
                        <span className="text-muted-foreground text-xs block">Stock</span>
                        <div className="pt-0.5">
                          <StockPill item={item} />
                        </div>
                      </div>
                      <div>
                        <span className="text-muted-foreground text-xs block">Available</span>
                        <div className="pt-0.5">
                          <Switch
                            checked={item.is_available}
                            onCheckedChange={(checked) =>
                              updateItem.mutate(
                                { id: item.id, updates: { is_available: checked } },
                                { onError: (error) => toast.error(getApiErrorMessage(error, 'Could not update availability')) }
                              )
                            }
                            aria-label={`Toggle ${item.name} availability`}
                          />
                        </div>
                      </div>
                    </div>
                      </div>
                    ))}
                  </div>
                }
              />
            </div>
          )}
        </section>
      </div>
      <ItemDialog
        open={itemDialogOpen}
        mode={itemDialogMode}
        form={itemForm}
        categories={categories}
        pending={createItem.isPending || updateItem.isPending}
        onChange={(updates) => setItemForm((current) => ({ ...current, ...updates }))}
        onClose={() => setItemDialogOpen(false)}
        onSave={saveItem}
      />
      <StockDialog item={stockItem} open={!!stockItem} onClose={() => setStockItem(null)} />
      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete "{deleteTarget?.name}"?</DialogTitle>
            <DialogDescription>This action cannot be undone.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setDeleteConfirmOpen(false)} disabled={deleteCategory.isPending || deleteItem.isPending}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={deleteCategory.isPending || deleteItem.isPending}
            >
              {(deleteCategory.isPending || deleteItem.isPending) && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              Delete permanently
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
