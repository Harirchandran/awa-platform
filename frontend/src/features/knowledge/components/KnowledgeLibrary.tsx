import { useMemo, useState } from 'react';
import { Edit3 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ResponsiveDataList } from '@/components/shared/ResponsiveDataList';
import type { KnowledgeEntry } from '@/features/knowledge/api';
import { KnowledgeSourceBadge, KnowledgeStatusBadge } from './KnowledgeStatusBadge';
import { KnowledgeEntryEditor } from './KnowledgeEntryEditor';
import {
  extractKnowledgeAnswer,
  extractKnowledgeQuestion,
  formatKnowledgeAge,
} from '@/features/knowledge/utils';

interface KnowledgeLibraryProps {
  entries: KnowledgeEntry[];
}

export function KnowledgeLibrary({ entries }: KnowledgeLibraryProps) {
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(entries[0]?.id ?? null);
  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedEntryId) ?? entries[0] ?? null,
    [entries, selectedEntryId]
  );

  const desktop = (
    <div className="rounded-md border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Entry</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Updated</TableHead>
            <TableHead className="w-[96px] text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.map((entry) => {
            const selected = selectedEntry?.id === entry.id;
            return (
              <TableRow
                key={entry.id}
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                className={selected ? 'bg-muted/50' : ''}
                onClick={() => setSelectedEntryId(entry.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setSelectedEntryId(entry.id);
                  }
                }}
              >
                <TableCell>
                  <div className="max-w-[520px]">
                    <p className="truncate text-[13px] font-medium text-foreground">{extractKnowledgeQuestion(entry)}</p>
                    <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-muted-foreground">{extractKnowledgeAnswer(entry)}</p>
                  </div>
                </TableCell>
                <TableCell><KnowledgeSourceBadge source={entry.metadata?.source} /></TableCell>
                <TableCell><KnowledgeStatusBadge entry={entry} /></TableCell>
                <TableCell className="text-[12px] text-muted-foreground">{formatKnowledgeAge(entry.created_at)}</TableCell>
                <TableCell className="text-right">
                  <Button
                    type="button"
                    size="sm"
                    variant={selected ? 'default' : 'outline'}
                    onClick={(event) => {
                      event.stopPropagation();
                      setSelectedEntryId(entry.id);
                    }}
                    aria-label={`Edit ${entry.document_title}`}
                  >
                    <Edit3 className="h-3.5 w-3.5" aria-hidden="true" />
                    Edit
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );

  const mobile = (
    <div className="space-y-3">
      {entries.map((entry) => {
        const selected = selectedEntry?.id === entry.id;
        return (
          <article key={entry.id} className="rounded-md border bg-card p-4">
            <div className="flex flex-wrap items-center gap-2">
              <KnowledgeSourceBadge source={entry.metadata?.source} />
              <KnowledgeStatusBadge entry={entry} />
            </div>
            <h3 className="mt-3 text-[14px] font-semibold text-foreground">{extractKnowledgeQuestion(entry)}</h3>
            <p className="mt-2 line-clamp-3 text-[13px] leading-5 text-muted-foreground">{extractKnowledgeAnswer(entry)}</p>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[12px] text-muted-foreground">Saved {formatKnowledgeAge(entry.created_at)}</span>
              <Button
                type="button"
                size="sm"
                variant={selected ? 'default' : 'outline'}
                onClick={() => setSelectedEntryId(entry.id)}
                aria-label={`Edit ${entry.document_title}`}
              >
                <Edit3 className="h-3.5 w-3.5" aria-hidden="true" />
                Edit
              </Button>
            </div>
          </article>
        );
      })}
    </div>
  );

  return (
    <section className="space-y-3" aria-label="Knowledge entries">
      <ResponsiveDataList desktop={desktop} mobile={mobile} />
      {selectedEntry && (
        <KnowledgeEntryEditor
          key={selectedEntry.id}
          entry={selectedEntry}
          onDeleted={() => {
            const nextEntry = entries.find((entry) => entry.id !== selectedEntry.id) ?? null;
            setSelectedEntryId(nextEntry?.id ?? null);
          }}
        />
      )}
    </section>
  );
}
