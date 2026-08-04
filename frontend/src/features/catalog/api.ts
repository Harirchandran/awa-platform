import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
export interface CatalogCategory {
  id: string;
  tenant_id: string;
  name: string;
  description?: string | null;
  sort_order: number;
  is_active: boolean;
  metadata?: Record<string, unknown>;
  item_count?: number;
  created_at: string;
  updated_at?: string | null;
}
export interface CatalogItem {
  id: string;
  tenant_id: string;
  category_id?: string | null;
  category_name?: string | null;
  name: string;
  description?: string | null;
  price: number;
  currency: string;
  sku?: string | null;
  image_url?: string | null;
  image?: CatalogImage | null;
  is_active: boolean;
  is_available: boolean;
  stock_tracking_enabled: boolean;
  stock_quantity: number;
  low_stock_threshold?: number | null;
  low_stock?: boolean;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at?: string | null;
}
export interface CatalogImage {
  id: string;
  url?: string | null;
  thumbnail_url?: string | null;
  alt_text?: string | null;
  caption?: string | null;
  width?: number | null;
  height?: number | null;
  mime_type?: string | null;
  size_bytes?: number | null;
}
export interface InventoryEvent {
  id: string;
  tenant_id: string;
  item_id: string;
  variant_id?: string | null;
  event_type: string;
  quantity_delta: number;
  reason?: string | null;
  order_id?: string | null;
  created_by?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
}
type CatalogItemPayload = {
  category_id?: string | null;
  name: string;
  description?: string | null;
  price: number;
  currency: string;
  sku?: string | null;
  image_url?: string | null;
  is_active: boolean;
  is_available: boolean;
  stock_tracking_enabled: boolean;
  stock_quantity: number;
  low_stock_threshold?: number | null;
  metadata?: Record<string, unknown>;
};
type CategoryPayload = {
  name: string;
  description?: string | null;
  sort_order?: number;
  is_active?: boolean;
  metadata?: Record<string, unknown>;
};
function toNumber(value: number | string | null | undefined): number {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return Number.isFinite(parsed) ? Number(parsed) : 0;
}
function normalizeCategory(raw: CatalogCategory): CatalogCategory {
  return {
    ...raw,
    sort_order: toNumber(raw.sort_order),
    item_count: toNumber(raw.item_count),
    metadata: raw.metadata || {},
  };
}
function normalizeItem(raw: CatalogItem): CatalogItem {
  return {
    ...raw,
    price: toNumber(raw.price),
    stock_quantity: toNumber(raw.stock_quantity),
    low_stock_threshold: raw.low_stock_threshold == null ? null : toNumber(raw.low_stock_threshold),
    currency: raw.currency || 'INR',
    metadata: raw.metadata || {},
    image: raw.image || null,
  };
}
function normalizeEvent(raw: InventoryEvent): InventoryEvent {
  return {
    ...raw,
    quantity_delta: toNumber(raw.quantity_delta),
    metadata: raw.metadata || {},
  };
}
export function formatCatalogMoney(amount: number, currency = 'INR'): string {
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency,
      maximumFractionDigits: amount % 1 === 0 ? 0 : 2,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(amount % 1 === 0 ? 0 : 2)}`;
  }
}
export function useCatalogCategories() {
  return useQuery({
    queryKey: ['catalog', 'categories'],
    queryFn: async () => {
      const response = await api.get<CatalogCategory[]>('/api/v1/catalog/categories');
      return response.data.map(normalizeCategory);
    },
  });
}
export function useCatalogItems(categoryId?: string, search?: string) {
  return useQuery({
    queryKey: ['catalog', 'items', { categoryId, search }],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (categoryId && categoryId !== 'all') params.append('category_id', categoryId);
      if (search) params.append('search', search);
      const response = await api.get<CatalogItem[]>(`/api/v1/catalog/items?${params.toString()}`);
      return response.data.map(normalizeItem);
    },
  });
}
export function useInventoryEvents(itemId?: string) {
  return useQuery({
    queryKey: ['catalog', 'inventory-events', itemId],
    queryFn: async () => {
      const response = await api.get<InventoryEvent[]>(`/api/v1/catalog/items/${itemId}/inventory-events`);
      return response.data.map(normalizeEvent);
    },
    enabled: !!itemId,
  });
}
export function useCreateCatalogCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CategoryPayload) => {
      const response = await api.post<CatalogCategory>('/api/v1/catalog/categories', payload);
      return normalizeCategory(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function useUpdateCatalogCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, updates }: { id: string; updates: Partial<CategoryPayload> }) => {
      const response = await api.patch<CatalogCategory>(`/api/v1/catalog/categories/${id}`, updates);
      return normalizeCategory(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function useDeleteCatalogCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const response = await api.delete<{ deleted: boolean }>(`/api/v1/catalog/categories/${id}`);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function useCreateCatalogItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CatalogItemPayload) => {
      const response = await api.post<CatalogItem>('/api/v1/catalog/items', payload);
      return normalizeItem(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function useUpdateCatalogItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, updates }: { id: string; updates: Partial<CatalogItemPayload> }) => {
      const response = await api.patch<CatalogItem>(`/api/v1/catalog/items/${id}`, updates);
      return normalizeItem(response.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function useDeleteCatalogItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const response = await api.delete<{ deleted: boolean }>(`/api/v1/catalog/items/${id}`);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function resolveMediaUrl(url?: string | null): string | undefined {
  if (!url) return undefined;
  if (/^https?:\/\//i.test(url)) return url;
  const base = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');
  return `${base}${url.startsWith('/') ? url : `/${url}`}`;
}
export function useUploadCatalogItemImage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      itemId,
      file,
      altText,
      caption,
    }: {
      itemId: string;
      file: File;
      altText?: string;
      caption?: string;
    }) => {
      const formData = new FormData();
      formData.append('file', file);
      if (altText) formData.append('alt_text', altText);
      if (caption) formData.append('caption', caption);
      const response = await api.post<CatalogImage>(`/api/v1/media/catalog-items/${itemId}/image`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function useDeleteCatalogItemImage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ itemId, imageId }: { itemId: string; imageId: string }) => {
      const response = await api.delete<{ deleted: boolean }>(`/api/v1/media/catalog-items/${itemId}/image/${imageId}`);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
  });
}
export function useAdjustCatalogStock() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      quantity_delta,
      reason,
      event_type = 'manual_adjustment',
    }: {
      id: string;
      quantity_delta: number;
      reason?: string;
      event_type?: 'manual_adjustment' | 'restock' | 'correction';
    }) => {
      const response = await api.post<CatalogItem>(`/api/v1/catalog/items/${id}/stock-adjustments`, {
        quantity_delta,
        reason,
        event_type,
      });
      return normalizeItem(response.data);
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
      queryClient.invalidateQueries({ queryKey: ['catalog', 'inventory-events', variables.id] });
    },
  });
}
