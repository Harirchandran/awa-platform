import { useState } from 'react';
import { ChevronDown, ChevronUp, RefreshCcw, Save, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import {
  useDeleteKnowledgeEntry,
  useUpdateKnowledgeEntry,
  type KnowledgeEntry,
} from '@/features/knowledge/api';
import { KnowledgeSourceBadge, KnowledgeStatusBadge } from './KnowledgeStatusBadge';
import {
  buildKnowledgeChunk,
  extractKnowledgeAnswer,
  extractKnowledgeQuestion,
  formatKnowledgeAge,
  isKnowledgeEnabled,
} from '@/features/knowledge/utils';

interface KnowledgeEntryEditorProps {
  entry: KnowledgeEntry;
  onDeleted?: () => void;
  onClose?: () => void;
}

export function KnowledgeEntryEditor({ entry, onDeleted, onClose }: KnowledgeEntryEditorProps) {
  const updateEntry = useUpdateKnowledgeEntry();
  const deleteEntry = useDeleteKnowledgeEntry();
  const [question, setQuestion] = useState(extractKnowledgeQuestion(entry));
  const [answer, setAnswer] = useState(extractKnowledgeAnswer(entry));
  const [enabled, setEnabled] = useState(isKnowledgeEnabled(entry));
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [saveAttempted, setSaveAttempted] = useState(false);

  const initialQuestion = extractKnowledgeQuestion(entry);
  const initialAnswer = extractKnowledgeAnswer(entry);
  const initialEnabled = isKnowledgeEnabled(entry);
  const dirty = question.trim() !== initialQuestion || answer.trim() !== initialAnswer || enabled !== initialEnabled;
  const questionError = saveAttempted && question.trim().length < 2 ? 'Enter at least 2 characters.' : '';
  const answerError = saveAttempted && answer.trim().length < 10 ? 'Enter at least 10 characters.' : '';
  const canSave = dirty && question.trim().length >= 2 && answer.trim().length >= 10 && !updateEntry.isPending;
  const isLongAnswer = answer.length > 700 || answer.split('\n').length > 10;
  const answerRows = isLongAnswer && !expanded ? 5 : 10;

  const save = () => {
    setSaveAttempted(true);
    if (!canSave) return;
    updateEntry.mutate(
      {
        id: entry.id,
        documentTitle: question.trim().slice(0, 120),
        chunkText: buildKnowledgeChunk(question, answer),
        enabled,
      },
      {
        onSuccess: () => {
          setNotice('Saved');
          setSaveAttempted(false);
        },
        onError: () => setNotice(null),
      }
    );
  };

  const discard = () => {
    setQuestion(initialQuestion);
    setAnswer(initialAnswer);
    setEnabled(initialEnabled);
    setNotice(null);
    setSaveAttempted(false);
  };

  const remove = () => {
    deleteEntry.mutate(entry.id, {
      onSuccess: () => {
        toast.success('Knowledge entry removed');
        setDeleteConfirmOpen(false);
        onDeleted?.();
      },
    });
  };

  return (
    <article className="rounded-md border bg-card p-4" aria-label={`Edit knowledge entry ${entry.document_title}`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[15px] font-semibold text-foreground">{entry.document_title}</h3>
            <KnowledgeSourceBadge source={entry.metadata?.source} />
            <KnowledgeStatusBadge entry={entry} />
          </div>
          <p className="mt-1 text-[12px] text-muted-foreground">Saved {formatKnowledgeAge(entry.created_at)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-[12px] text-muted-foreground">
            <Switch checked={enabled} onCheckedChange={(value) => { setEnabled(value); setNotice(null); }} aria-label="Enable saved answer for AWA replies" />
            <span>Use in AWA</span>
          </label>
          <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => setDeleteConfirmOpen(true)} disabled={deleteEntry.isPending} aria-label={`Delete ${entry.document_title}`}>
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          {onClose && (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onClose} aria-label="Close entry editor">
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div>
          <label htmlFor={`knowledge-question-${entry.id}`} className="mb-1.5 block text-[12px] font-medium text-foreground">
            Customer question or reason
          </label>
          <Textarea
            id={`knowledge-question-${entry.id}`}
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              setNotice(null);
            }}
            maxLength={800}
            rows={4}
            className="resize-none text-[13px]"
            aria-invalid={Boolean(questionError)}
            aria-describedby={`knowledge-question-${entry.id}-helper knowledge-question-${entry.id}-error`}
          />
          <p id={`knowledge-question-${entry.id}-helper`} className="mt-1 text-[11px] text-muted-foreground">
            This becomes the saved entry title sent to the backend.
          </p>
          {questionError && <p id={`knowledge-question-${entry.id}-error`} className="mt-1 text-[12px] text-destructive">{questionError}</p>}
        </div>
        <div>
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <label htmlFor={`knowledge-answer-${entry.id}`} className="block text-[12px] font-medium text-foreground">
              {isLongAnswer && !expanded ? 'Approved answer preview' : 'Approved answer'}
            </label>
            {isLongAnswer && (
              <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-[12px]" onClick={() => setExpanded((value) => !value)}>
                {expanded ? <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" /> : <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />}
                {expanded ? 'Collapse answer' : 'Expand full answer'}
              </Button>
            )}
          </div>
          <Textarea
            id={`knowledge-answer-${entry.id}`}
            value={answer}
            onChange={(event) => {
              setAnswer(event.target.value);
              setNotice(null);
            }}
            maxLength={3000}
            rows={answerRows}
            className="text-[13px]"
            aria-invalid={Boolean(answerError)}
            aria-describedby={`knowledge-answer-${entry.id}-helper knowledge-answer-${entry.id}-error`}
          />
          <p id={`knowledge-answer-${entry.id}-helper`} className="mt-1 text-[11px] leading-4 text-muted-foreground">
            AWA uses active answers as reference material for customer replies.
          </p>
          {answerError && <p id={`knowledge-answer-${entry.id}-error`} className="mt-1 text-[12px] text-destructive">{answerError}</p>}
          {isLongAnswer && !expanded && (
            <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
              Long content is collapsed for mobile scanning. Expand before approving, editing, or saving this full answer for AWA.
            </p>
          )}
        </div>
      </div>
      <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[12px] text-muted-foreground" aria-live="polite">
          {notice || (updateEntry.isError ? 'Could not save this answer.' : deleteEntry.isError ? 'Could not delete this answer.' : dirty ? 'Unsaved changes' : '')}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={discard} disabled={!dirty || updateEntry.isPending}>
            Discard
          </Button>
          <Button type="button" size="sm" onClick={save} disabled={!canSave}>
            {updateEntry.isPending ? <RefreshCcw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Save className="h-3.5 w-3.5" aria-hidden="true" />}
            Save
          </Button>
        </div>
      </div>

      <ConfirmActionDialog
        open={deleteConfirmOpen}
        onOpenChange={setDeleteConfirmOpen}
        title={`Delete "${entry.document_title}"?`}
        description="This removes the saved knowledge entry from AWA reference material. This action cannot be undone."
        confirmLabel="Delete permanently"
        variant="destructive"
        isLoading={deleteEntry.isPending}
        onConfirm={remove}
      />
    </article>
  );
}
