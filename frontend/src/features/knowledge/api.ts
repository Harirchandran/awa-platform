import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
export interface KnowledgeEntry {
  id: string;
  tenant_id: string;
  document_title: string;
  chunk_text: string;
  metadata?: {
    source?: string;
    enabled?: boolean;
    conversation_id?: string;
    customer_id?: string;
    actor_id?: string;
    [key: string]: unknown;
  };
  created_at: string;
}
export interface KnowledgeSourceSummary {
  source: string;
  total_count: number;
  enabled_count: number;
  disabled_count: number;
  last_created_at?: string | null;
}
export function useKnowledgeEntries(search = '') {
  return useQuery({
    queryKey: ['knowledge-base', { search }],
    queryFn: async (): Promise<KnowledgeEntry[]> => {
      const params = new URLSearchParams({ limit: '200' });
      if (search.trim()) params.set('search', search.trim());
      const response = await api.get<KnowledgeEntry[]>(`/api/v1/knowledge-base?${params.toString()}`);
      return response.data;
    },
  });
}
export function useKnowledgeSourceSummary() {
  return useQuery({
    queryKey: ['knowledge-base-sources'],
    queryFn: async (): Promise<KnowledgeSourceSummary[]> => {
      const response = await api.get<KnowledgeSourceSummary[]>('/api/v1/knowledge-base/sources');
      return response.data;
    },
  });
}
export function useCreateKnowledgeEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      documentTitle: string;
      chunkText: string;
      source: 'manual' | 'document';
      fileName?: string;
    }): Promise<KnowledgeEntry[]> => {
      const response = await api.post<KnowledgeEntry[]>('/api/v1/knowledge-base', {
        document_title: payload.documentTitle,
        chunk_text: payload.chunkText,
        source: payload.source,
        file_name: payload.fileName,
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-base'] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base-sources'] });
    },
  });
}
export function useImportWebsiteKnowledge() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { url: string; title?: string }): Promise<KnowledgeEntry[]> => {
      const response = await api.post<KnowledgeEntry[]>('/api/v1/knowledge-base/import-website', {
        url: payload.url,
        title: payload.title,
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-base'] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base-sources'] });
    },
  });
}
export function useUploadDocumentKnowledge() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { title: string; file: File }): Promise<KnowledgeEntry[]> => {
      const data = new FormData();
      data.append('title', payload.title);
      data.append('file', payload.file);
      const response = await api.post<KnowledgeEntry[]>('/api/v1/knowledge-base/upload-document', data, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-base'] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base-sources'] });
    },
  });
}
export function useUpdateKnowledgeEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      id: string;
      documentTitle?: string;
      chunkText?: string;
      enabled?: boolean;
    }): Promise<KnowledgeEntry> => {
      const response = await api.patch<KnowledgeEntry>(`/api/v1/knowledge-base/${payload.id}`, {
        document_title: payload.documentTitle,
        chunk_text: payload.chunkText,
        enabled: payload.enabled,
      });
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-base'] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base-sources'] });
    },
  });
}
export function useDeleteKnowledgeEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<{ deleted: boolean }> => {
      const response = await api.delete<{ deleted: boolean }>(`/api/v1/knowledge-base/${id}`);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-base'] });
      queryClient.invalidateQueries({ queryKey: ['knowledge-base-sources'] });
    },
  });
}