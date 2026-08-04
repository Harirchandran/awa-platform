import { StatusBadge } from '@/components/shared/StatusBadge';
import type { KnowledgeEntry } from '@/features/knowledge/api';
import { isKnowledgeEnabled, sourceLabel } from '@/features/knowledge/utils';

export function KnowledgeStatusBadge({ entry }: { entry: KnowledgeEntry }) {
  const enabled = isKnowledgeEnabled(entry);

  return (
    <StatusBadge
      label={enabled ? 'Active' : 'Disabled'}
      tone={enabled ? 'success' : 'muted'}
      srLabel={enabled ? 'Available for AWA replies' : 'Not used in AWA replies'}
    />
  );
}

export function KnowledgeSourceBadge({ source }: { source?: string }) {
  const knownSource = source ?? 'knowledge';
  const tone =
    knownSource === 'website'
      ? 'info'
      : knownSource === 'document'
        ? 'warning'
        : knownSource === 'tenant_escalation_reply'
          ? 'success'
          : 'neutral';

  return <StatusBadge label={sourceLabel(source)} tone={tone} />;
}
