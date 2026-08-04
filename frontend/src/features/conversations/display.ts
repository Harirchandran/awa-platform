import type { Conversation, ConversationStatus } from './api';
import { getAiModeLabel } from '@/lib/ai-mode';

export function getConversationChannelLabel(channel?: string | null) {
  const normalized = channel?.toLowerCase();
  if (isConversationAwaChat(channel)) {
    return 'AWA Chat';
  }
  if (normalized === 'whatsapp') return 'WhatsApp';
  if (normalized === 'email') return 'Email';
  if (normalized === 'sms') return 'SMS';
  return 'Unknown';
}

export function isConversationAwaChat(channel?: string | null) {
  const normalized = channel?.toLowerCase();
  return normalized === 'awa_chat' || normalized === 'public_chat' || normalized === 'browser_chat';
}

export function getConversationStatusLabel(status?: ConversationStatus | string | null) {
  if (status === 'escalated') return 'Needs Reply';
  if (status === 'closed') return 'Resolved';
  if (status === 'active') return 'Active';
  return status || 'Unknown';
}

export function getConversationAiModeLabel(aiMode?: Conversation['ai_mode']) {
  return getAiModeLabel(aiMode);
}
