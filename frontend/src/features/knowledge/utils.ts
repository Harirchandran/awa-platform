import { formatDistanceToNow } from 'date-fns';
import type { KnowledgeEntry, KnowledgeSourceSummary } from './api';
import { KNOWLEDGE_SOURCE_LABELS } from './constants';

export function isKnowledgeEnabled(entry: KnowledgeEntry) {
  return entry.metadata?.enabled !== false;
}

export function extractKnowledgeQuestion(entry: KnowledgeEntry) {
  const match = entry.chunk_text.match(/Escalation reason\/question:\s*([\s\S]*?)(?:\nTenant answer:|$)/i);
  return match?.[1]?.trim() || entry.document_title;
}

export function extractKnowledgeAnswer(entry: KnowledgeEntry) {
  const match = entry.chunk_text.match(/Tenant answer:\s*([\s\S]*)/i);
  return match?.[1]?.trim() || entry.chunk_text;
}

export function buildKnowledgeChunk(question: string, answer: string) {
  return `Escalation reason/question: ${question.trim()}\nTenant answer: ${answer.trim()}`;
}

export function sourceLabel(source?: string) {
  if (!source) return 'Knowledge';
  return KNOWLEDGE_SOURCE_LABELS[source] ?? formatLabel(source);
}

export function formatLabel(value: string) {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatKnowledgeAge(value?: string | null) {
  if (!value) return 'No date';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'No date';
  return formatDistanceToNow(date, { addSuffix: true });
}

export function isValidKnowledgeUrl(value: string) {
  return /^https?:\/\//i.test(value.trim());
}

export function getKnowledgeMetrics(entries: KnowledgeEntry[], sourceSummary: KnowledgeSourceSummary[]) {
  const active = entries.filter(isKnowledgeEnabled).length;
  const disabled = Math.max(entries.length - active, 0);
  const sourceTotal = sourceSummary.reduce((total, source) => total + source.total_count, 0);
  const websiteCount = sourceSummary.find((source) => source.source === 'website')?.total_count ?? 0;
  const documentCount = sourceSummary.find((source) => source.source === 'document')?.total_count ?? 0;

  return {
    loaded: entries.length,
    active,
    disabled,
    sourceTotal,
    websiteCount,
    documentCount,
  };
}
