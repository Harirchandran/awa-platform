export type CanonicalAiMode = 'standard' | 'advanced';
export type AiModeInput = CanonicalAiMode | 'concierge' | string | null | undefined;

export function normalizeAiMode(value: AiModeInput): CanonicalAiMode {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'concierge') return 'advanced';
  if (normalized === 'advanced') return 'advanced';
  return 'standard';
}

export function normalizeAllowedAiModes(values?: string[] | null): CanonicalAiMode[] {
  const modes: CanonicalAiMode[] = [];
  for (const value of values || []) {
    const mode = normalizeAiMode(value);
    if (!modes.includes(mode)) modes.push(mode);
  }
  return modes.length > 0 ? modes : ['standard'];
}

export function getAiModeLabel(value: AiModeInput) {
  return normalizeAiMode(value) === 'advanced' ? 'Advanced AI' : 'Standard AI';
}
