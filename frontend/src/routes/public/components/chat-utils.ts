import { type PublicChatMessage } from '@/features/awa-chat/api';
const inlineQuickReplyPattern = /\[([^\]]{1,80})\]/g;
export function createClientMessageId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}`;
}
export interface PendingReply {
  messageCount: number;
}
export interface ConfirmationField {
  label: string;
  value: string;
}
type InteractiveButton = {
  reply?: {
    title?: string;
  };
};
type InteractiveRow = {
  title?: string;
};
type InteractivePayload = {
  type?: 'button' | 'list';
  action?: {
    buttons?: InteractiveButton[];
    sections?: Array<{
      rows?: InteractiveRow[];
    }>;
  };
};
export function getInlineQuickReplies(text: string | null | undefined): string[] {
  const safeText = typeof text === 'string' ? text : '';
  const matches = Array.from(safeText.matchAll(inlineQuickReplyPattern), (match) => match[1].trim()).filter(Boolean);
  if (matches.length) return Array.from(new Set(matches));
  if (/shall i proceed\?/i.test(safeText)) return ['Yes', 'No'];
  return [];
}
export function getPayloadQuickReplies(payload: unknown): string[] {
  if (!payload || typeof payload !== 'object') return [];
  const data = payload as InteractivePayload;
  const buttons = data.action?.buttons?.map((button) => button.reply?.title).filter(Boolean) ?? [];
  const rows = data.action?.sections?.flatMap((section) => section.rows ?? []).map((row) => row.title).filter(Boolean) ?? [];
  return Array.from(new Set([...(buttons as string[]), ...(rows as string[])]));
}
export function getMessageQuickReplies(message: Pick<PublicChatMessage, 'message_text' | 'raw_payload' | 'quick_replies'>): string[] {
  if (Array.isArray(message.quick_replies) && message.quick_replies.length) {
    return Array.from(new Set(message.quick_replies.map((reply) => reply.trim()).filter(Boolean)));
  }
  const inline = getInlineQuickReplies(message.message_text);
  if (inline.length) return inline;
  return getPayloadQuickReplies(message.raw_payload);
}
export function isQuickReplyStale(
  message: PublicChatMessage,
  lastAssistantMessage?: PublicChatMessage,
  messages: PublicChatMessage[] = []
): boolean {
  if (message.quick_replies_stale) return true;
  if (message.id !== lastAssistantMessage?.id) return true;
  if (messages.length > 0) {
    const currentIndex = messages.findIndex((m) => m.id === message.id);
    if (currentIndex !== -1 && currentIndex < messages.length - 1) {
      return true;
    }
  }
  return false;
}
export function getMessageMetaLabel(message: PublicChatMessage, quickReplyCount: number, stale: boolean): string | null {
  if (message.processing_status === 'delayed') return 'Delayed';
  if (quickReplyCount && stale) return null;
  if (quickReplyCount && message.quick_replies_state === 'active') return null;
  if (message.message_type === 'interactive') return 'Interactive';
  return null;
}
export function stripInlineQuickReplies(text: string): string {
  return text.replace(/\s*\[[^\]]{1,80}\]/g, '').trim();
}
export function formatFieldLabel(key: string): string {
  const normalized = key.replace(/_/g, ' ').trim();
  const aliases: Record<string, string> = {
    customer_name: 'Name',
    patient_name: 'Name',
    service_id: 'Service',
    item_id: 'Item',
    quantity: 'Quantity',
    fulfillment_type: 'Fulfillment',
    delivery_address: 'Delivery address',
    preferred_date: 'Date',
    preferred_time: 'Time',
    phone: 'Phone',
    phone_number: 'Phone',
    interest: 'Interested in',
  };
  return aliases[key] || normalized.replace(/\b\w/g, (letter) => letter.toUpperCase());
}
export function sanitizeConfirmationValue(label: string, value: string): string {
  const cleanValue = value
    .replace(/\s+(Before we finalize|To recap|Here are|Would you|Do you|Shall I|I'll|I will)\b.*$/i, '')
    .trim();
  if (/^phone(?: number)?$/i.test(label)) {
    const phoneMatch = cleanValue.match(/^(\+?\d[\d\s().-]{5,}\d)/);
    return (phoneMatch?.[1] ?? cleanValue).trim();
  }
  return cleanValue;
}
export function parseConfirmationFields(text: string): ConfirmationField[] {
  const matchBlock = text.match(/<confirmation>([\s\S]*?)<\/confirmation>/);
  if (!matchBlock) return [];
  const innerContent = matchBlock[1];
  const linePattern = /-\s+([^:\n]+):\s*(.*)/g;
  const fields: ConfirmationField[] = [];
  let match: RegExpExecArray | null;
  while ((match = linePattern.exec(innerContent)) !== null) {
    const label = formatFieldLabel(match[1].trim());
    const rawVal = match[2].trim();
    const value = sanitizeConfirmationValue(label, rawVal);
    if (label && value) {
      fields.push({ label, value });
    }
  }
  return fields;
}
export function stripConfirmationFields(text: string): string {
  return text.replace(/<confirmation>[\s\S]*?<\/confirmation>/g, '').replace(/\n{3,}/g, '\n\n').trim();
}
export function getOtpAction(fields: ConfirmationField[]): string {
  if (fields.some((field) => field.label.toLowerCase() === 'item')) return 'order_commitment';
  if (fields.some((field) => ['service', 'date', 'time'].includes(field.label.toLowerCase()))) return 'booking_commitment';
  if (fields.some((field) => {
    const label = field.label.toLowerCase();
    return label.includes('reminder') || ['trigger', 'offset', 'template', 'template text', 'trigger type', 'offset minutes'].includes(label);
  })) {
    return 'reminders_commitment';
  }
  if (fields.some((field) => ['payment', 'amount', 'price', 'charge'].includes(field.label.toLowerCase()))) {
    return 'payments_commitment';
  }
  return 'commitment';
}
export function safeStripAllInternalMarkup(text: string): string {
  if (!text) return '';
  const lowered = text.toLowerCase();
  const highRiskSubstrings = [
    '(function=',
    '<function=',
    'booking_execute',
    'order_execute',
    'payment_execute',
    'reminder_execute',
    'review_execute',
    'tool_call',
    'toolregistry',
    'raw_payload',
    'begin_system',
    'system prompt',
    'system prompts',
    'developer message',
    'developer messages',
    'internal instruction',
    'internal instructions',
    'tool detail',
    'tool details',
    'tool schema',
    'tool schemas',
  ];
  const hasHighRisk = highRiskSubstrings.some((sub) => lowered.includes(sub));
  const hasStackTrace = lowered.includes('traceback (most recent call last)');
  const hasPythonFile = /file\s+["'][^"']+\.py["'],\s+line\s+\d+/i.test(text);
  const hasExceptionClass = /\b(valueerror|keyerror|typeerror|runtimeerror|attributeerror|indexerror|zerodivisionerror|exception|dispatcherror|dbclienterror|toolerror)\b:/i.test(text);
  const jsonKeys = [
    'patient_name', 'phone', 'service_id', 'preferred_date', 'preferred_time', 'customer_name',
    'item_id', 'quantity', 'fulfillment_type', 'consent_status', 'email', 'reason', 'time', 'date',
  ];
  const noSpaces = lowered.replace(/\s+/g, '');
  const hasJsonArgs = jsonKeys.some((key) => noSpaces.includes(`"${key}":`) || noSpaces.includes(`'${key}':`));
  const hasSchema = lowered.includes('properties') && lowered.includes('required') && lowered.includes('type');
  if (hasHighRisk || hasStackTrace || hasPythonFile || hasExceptionClass || hasJsonArgs || hasSchema) {
    return 'I can help with that, but I need to handle the details safely. Please send the details again or choose one of the available options.';
  }
  return text;
}
