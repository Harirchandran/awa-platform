import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import toast from 'react-hot-toast';
export interface PromptEntry {
  key: string;
  description: string;
  body: string;
  version: number;
  updated_at: string;
  updated_by: string;
}
export function dedupePromptEntries(prompts: PromptEntry[]) {
  const byKey = new Map<string, PromptEntry>();
  for (const prompt of prompts) {
    const current = byKey.get(prompt.key);
    const promptTime = Date.parse(prompt.updated_at || '');
    const currentTime = Date.parse(current?.updated_at || '');
    if (
      !current ||
      prompt.version > current.version ||
      (prompt.version === current.version && promptTime > currentTime)
    ) {
      byKey.set(prompt.key, prompt);
    }
  }
  return Array.from(byKey.values()).sort((left, right) => left.key.localeCompare(right.key));
}
export function usePrompts() {
  return useQuery({
    queryKey: ['prompts'],
    queryFn: async (): Promise<PromptEntry[]> => {
      const response = await api.get<PromptEntry[]>('/api/v1/prompts');
      return dedupePromptEntries(response.data);
    },
  });
}
export function useUpdatePrompt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ key, body }: { key: string; body: string }) => {
      const response = await api.put<PromptEntry>(`/api/v1/prompts/${key}`, { body });
      return response.data;
    },
    onSuccess: (_data, variables) => {
      queryClient.setQueryData<PromptEntry[]>(['prompts'], (current) =>
        current?.map((prompt) =>
          prompt.key === variables.key
            ? {
                ...prompt,
                body: variables.body,
                updated_at: new Date().toISOString(),
                version: prompt.version + 1,
              }
            : prompt
        ) ?? current
      );
      toast.success(`System prompt '${variables.key}' saved successfully.`);
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
    },
    onError: (error: unknown) => {
      toast.error(`Failed to save prompt: ${getApiErrorMessage(error)}`);
    }
  });
}