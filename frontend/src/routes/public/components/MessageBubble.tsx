import { Check, CheckCheck, Clock3, Loader2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { resolvePublicMediaUrl, type PublicChatMessage, type PublicChatCard } from '@/features/awa-chat/api';
import { motion } from 'framer-motion';
import {
  getMessageQuickReplies,
  isQuickReplyStale,
  getMessageMetaLabel,
  parseConfirmationFields,
  stripConfirmationFields,
  stripInlineQuickReplies,
  safeStripAllInternalMarkup,
} from './chat-utils';

interface MessageBubbleProps {
  message: PublicChatMessage;
  messages?: PublicChatMessage[];
  lastAssistantMessage?: PublicChatMessage;
  isFirstInGroup?: boolean;
  isLastInGroup?: boolean;
  businessName?: string;
  inputLocked?: boolean;
  pendingQuickReply?: string | null;
  onQuickReply?: (messageId: string, reply: string) => void;
}

function MessageCard({ card }: { card: PublicChatCard }) {
  const imageUrl = resolvePublicMediaUrl(card.thumbnail_url || card.image_url);
  const actions = card.actions || [];
  return (
    <div className="mt-2 w-full min-w-0 overflow-hidden rounded-lg border border-border bg-card text-foreground shadow-lg sm:min-w-[260px]">
      <div className="aspect-[4/3] w-full bg-muted">
        {imageUrl ? (
          <img
            src={imageUrl}
            alt={card.image_alt || card.title}
            className="h-full w-full object-cover"
            loading="lazy"
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
          />
        ) : null}
      </div>
      <div className="space-y-1 p-3">
        <div className="flex items-start justify-between gap-3">
          <p className="min-w-0 text-[14px] font-semibold leading-5 text-foreground">{card.title}</p>
          {card.subtitle && <p className="shrink-0 text-[12px] font-medium text-emerald-600 dark:text-emerald-300">{card.subtitle}</p>}
        </div>
        {card.description && <p className="max-h-[60px] overflow-hidden text-[12px] leading-5 text-muted-foreground">{card.description}</p>}
        {actions.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {actions.slice(0, 3).map((action) => (
              <span key={action} className="rounded-full border border-emerald-300 dark:border-emerald-500/30 px-2 py-1 text-[11px] text-emerald-800 dark:text-emerald-100">
                {action}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function MessageBubble({
  message,
  messages = [],
  lastAssistantMessage,
  isFirstInGroup = true,
  isLastInGroup = true,
  businessName = 'A',
  inputLocked = false,
  pendingQuickReply = null,
  onQuickReply,
}: MessageBubbleProps) {
  const isCustomer = message.direction === 'inbound';
  const quickReplies = !isCustomer ? getMessageQuickReplies(message) : [];
  const cards = !isCustomer && Array.isArray(message.cards) ? message.cards : [];
  const quickRepliesStale = !isCustomer && quickReplies.length ? isQuickReplyStale(message, lastAssistantMessage, messages) : false;
  const metaLabel = !isCustomer ? getMessageMetaLabel(message, quickReplies.length, quickRepliesStale) : null;
  const rawText = message.message_text || '';
  const sanitizedText = safeStripAllInternalMarkup(rawText);
  const isUnsafe = sanitizedText !== rawText;
  const confirmationFields = !isCustomer && !isUnsafe ? parseConfirmationFields(rawText) : [];
  let messageText = sanitizedText;
  if (!isUnsafe && quickReplies.length) {
    messageText = stripInlineQuickReplies(messageText);
  }
  if (!isUnsafe && confirmationFields.length) {
    messageText = stripConfirmationFields(messageText);
  }

  // Determine rounded corners for grouping
  let roundedClasses = 'rounded-2xl';
  if (isCustomer) {
    if (isFirstInGroup && isLastInGroup) {
      roundedClasses += ' rounded-tr-sm';
    } else if (isFirstInGroup && !isLastInGroup) {
      roundedClasses += ' rounded-br-sm rounded-tr-2xl';
    } else if (!isFirstInGroup && !isLastInGroup) {
      roundedClasses += ' rounded-tr-sm rounded-br-sm';
    } else if (!isFirstInGroup && isLastInGroup) {
      roundedClasses += ' rounded-tr-sm rounded-br-2xl';
    }
  } else {
    if (isFirstInGroup && isLastInGroup) {
      roundedClasses += ' rounded-tl-sm';
    } else if (isFirstInGroup && !isLastInGroup) {
      roundedClasses += ' rounded-bl-sm rounded-tl-2xl';
    } else if (!isFirstInGroup && !isLastInGroup) {
      roundedClasses += ' rounded-tl-sm rounded-bl-sm';
    } else if (!isFirstInGroup && isLastInGroup) {
      roundedClasses += ' rounded-tl-sm rounded-bl-2xl';
    }
  }

  // Determine dynamic confirmation card title (B-13)
  let cardTitle = 'Summary';
  const hasField = (labelTerms: string[]) => {
    return confirmationFields.some(f => {
      const label = f.label.toLowerCase();
      const val = f.value.toLowerCase();
      return labelTerms.some(term => label.includes(term) || val.includes(term));
    });
  };
  if (hasField(['item', 'product', 'quantity'])) {
    cardTitle = 'Order Summary';
  } else if (hasField(['payment', 'amount', 'price', 'invoice', 'charge'])) {
    cardTitle = 'Payment Summary';
  } else if (hasField(['reminder', 'remind'])) {
    cardTitle = 'Reminder Summary';
  } else if (hasField(['review', 'rating', 'feedback'])) {
    cardTitle = 'Review Summary';
  } else if (hasField(['service', 'date', 'time', 'appointment', 'booking'])) {
    cardTitle = 'Booking Summary';
  } else if (confirmationFields.length > 0) {
    cardTitle = 'Summary Details';
  }

  // Render markdown-lite: bold, links, and bullet lists (B-07 / formatting)
  const renderMarkdown = (text: string, isCust: boolean) => {
    const linkColor = isCust
      ? 'text-emerald-950 dark:text-emerald-50 hover:text-emerald-900 dark:hover:text-emerald-200 underline font-semibold break-all'
      : 'text-emerald-600 dark:text-emerald-400 hover:text-emerald-500 dark:hover:text-emerald-300 underline break-all';

    // Split into lines to detect bullet lists
    const lines = text.split('\n');
    const result: React.ReactNode[] = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const bulletMatch = line.match(/^([*-]|\d+\.)\s+(.*)$/);

      if (bulletMatch) {
        // Collect consecutive bullet lines into a list
        const listItems: string[] = [];
        while (i < lines.length) {
          const lm = lines[i].match(/^([*-]|\d+\.)\s+(.*)$/);
          if (lm) { listItems.push(lm[2]); i++; }
          else break;
        }
        result.push(
          <ul key={`list-${i}`} className="my-1 space-y-0.5 pl-0">
            {listItems.map((item, idx) => (
              <li key={idx} className="flex gap-2 text-[14px] leading-6">
                <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${isCust ? 'bg-emerald-950/60 dark:bg-zinc-950/60' : 'bg-emerald-600 dark:bg-emerald-400/70'}`} />
                <span>{renderInline(item, linkColor)}</span>
              </li>
            ))}
          </ul>
        );
      } else {
        const trimmed = line;
        if (trimmed === '') {
          if (result.length > 0) result.push(<br key={`br-${i}`} />);
        } else {
          result.push(<span key={`line-${i}`} className="block">{renderInline(trimmed, linkColor)}</span>);
        }
        i++;
      }
    }
    return result;
  };

  const renderInline = (text: string, linkColor: string): React.ReactNode[] => {
    // Split on URLs first, then handle **bold**
    const urlRegex = /(https?:\/\/[^\s]+)/g;
    const parts = text.split(urlRegex);
    return parts.flatMap((part, pi) => {
      if (urlRegex.test(part)) {
        urlRegex.lastIndex = 0;
        return [<a key={`url-${pi}`} href={part} target="_blank" rel="noopener noreferrer" className={linkColor}>{part}</a>];
      }
      // Handle **bold**
      const boldParts = part.split(/(\*\*[^*]+\*\*)/g);
      return boldParts.map((bp, bi) => {
        if (bp.startsWith('**') && bp.endsWith('**')) {
          return <strong key={`b-${pi}-${bi}`}>{bp.slice(2, -2)}</strong>;
        }
        return bp as React.ReactNode;
      });
    });
  };

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95, y: 10 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 400, damping: 25 }}
      className={`flex min-w-0 ${isCustomer ? 'justify-end' : 'justify-start'} ${!isFirstInGroup ? '-mt-2' : ''}`}
    >
      {!isCustomer && (
        <div className="mr-2 w-7 shrink-0">
          {isFirstInGroup && (
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-500/20 text-[12px] font-bold text-emerald-600 dark:text-emerald-400">
              {businessName.charAt(0).toUpperCase()}
            </div>
          )}
        </div>
      )}
      <div className={`flex min-w-0 max-w-[82%] flex-col ${isCustomer ? 'items-end' : 'items-start'}`}>
        {confirmationFields.length > 0 && (
          <div className={`w-full min-w-0 overflow-hidden border border-border bg-card text-foreground shadow-lg sm:min-w-[260px] ${roundedClasses} ${messageText ? 'mt-2' : ''}`}>
            <div className="bg-gradient-to-r from-emerald-500/20 to-emerald-500/5 px-3 py-2 border-b border-border">
              <p className="text-[13px] font-semibold text-emerald-800 dark:text-emerald-100">{cardTitle}</p>
            </div>
            <div className="p-3">
              <div className="divide-y divide-border">
                {confirmationFields.map((field) => (
                  <div key={`${message.id}-${field.label}`} className="grid grid-cols-[minmax(72px,96px)_minmax(0,1fr)] gap-3 py-2 text-[13px]">
                    <span className="text-muted-foreground">{field.label}</span>
                    <span className="break-words text-foreground">{field.value}</span>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-[12px] text-muted-foreground">Confirm only if everything looks correct.</p>
            </div>
          </div>
        )}
        {messageText && (
          <div className={`break-words px-3 py-2 text-[14px] leading-6 ${roundedClasses} ${
            isCustomer
              ? 'bg-emerald-600 dark:bg-emerald-500 text-white dark:text-zinc-950'
              : 'border border-border bg-card text-foreground'
          }`}>
            {isCustomer ? (
              renderMarkdown(messageText, isCustomer)
            ) : (
              <div className="prose prose-sm dark:prose-invert max-w-none break-words text-[14px] leading-6 text-foreground prose-p:my-1 prose-ul:my-1 prose-li:my-0.5 prose-strong:text-foreground prose-a:text-emerald-600 dark:prose-a:text-emerald-400 prose-a:hover:text-emerald-500 dark:prose-a:hover:text-emerald-300 prose-headings:text-foreground prose-headings:my-2 prose-headings:font-bold prose-hr:border-border prose-hr:my-2">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                  {messageText}
                </ReactMarkdown>
              </div>
            )}
          </div>
        )}
        {cards.map((card, index) => (
          <MessageCard key={`${message.id}-card-${card.id || index}`} card={card} />
        ))}
        <div className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground">
          <time>
            {new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </time>
          {isCustomer && (
            <span className="ml-1">
              {message.delivery_status === 'delivered' ? (
                <CheckCheck className="h-3 w-3 text-emerald-500" />
              ) : (
                <Check className="h-3 w-3" />
              )}
            </span>
          )}
        </div>
        {metaLabel && (
          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
            {message.processing_status === 'delayed' ? <Clock3 className="h-3 w-3 text-amber-500 dark:text-amber-300" /> : null}
            <span>{metaLabel}</span>
          </div>
        )}
        {!!quickReplies.length && (
          <div className="mt-2 flex flex-wrap gap-2">
            {quickReplies.map((reply) => (
              <button
                key={reply}
                type="button"
                disabled={quickRepliesStale || inputLocked}
                onClick={() => onQuickReply?.(message.id, reply)}
                className={
                  quickRepliesStale
                    ? 'inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-3 py-1.5 text-[13px] text-muted-foreground transition active:scale-95 transition-transform disabled:cursor-not-allowed disabled:opacity-60'
                    : 'inline-flex items-center gap-1.5 rounded-full border border-emerald-300 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 px-3 py-1.5 text-[13px] text-emerald-800 dark:text-emerald-100 transition hover:bg-emerald-100 dark:hover:bg-emerald-500/20 active:scale-95 transition-transform disabled:cursor-not-allowed disabled:opacity-60'
                }
                title={quickRepliesStale ? 'This reply has expired.' : undefined}
              >
                {pendingQuickReply === `${message.id}:${reply}` && <Loader2 className="h-3 w-3 animate-spin" />}
                {reply}
              </button>
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}
