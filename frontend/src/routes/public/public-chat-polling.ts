export const PENDING_REPLY_POLL_MS = 2_000;
export const POLLING_FALLBACK_MS = 4_000;
export const SSE_SAFETY_POLL_MS = 15_000;

export function getPublicChatPollingInterval(
  pendingReply: boolean,
  sseActive: boolean,
): number {
  if (pendingReply) return PENDING_REPLY_POLL_MS;
  return sseActive ? SSE_SAFETY_POLL_MS : POLLING_FALLBACK_MS;
}
