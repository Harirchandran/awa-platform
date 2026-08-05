/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router';
import { UserCheck } from 'lucide-react';
import { AwaSplash } from '@/components/brand/AwaSplash';
import {
  createPublicChatSessionWithVerification,
  endPublicChatSession,
  getPublicChatLink,
  getPublicChatMessages,
  savePublicChatContact,
  sendPublicChatMessage,
  sendPublicChatOtp,
  resolvePublicChatProcessingTransition,
  type PublicChatSession,
  type PublicChatMessage,
  verifyPublicChatOtp,
} from '@/features/awa-chat/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { getPublicChatPollingInterval } from './public-chat-polling';
interface StoredChatSession {
  id: string;
  session_token: string;
  tenant_name: string;
  display_name: string;
  conversation_id: string;
}
interface PublicChatSendAttempt {
  text: string;
  clientMessageId: string;
}
const MAX_MESSAGE_LENGTH = 800;
const SLOW_REPLY_MS = 3000;
import { ChatHeader } from './components/ChatHeader';
import { ChatWelcome } from './components/ChatWelcome';
import { ChatEnded } from './components/ChatEnded';
import { ContactCapture } from './components/ContactCapture';
import { ChatFooter } from './components/ChatFooter';
import { MessageList } from './components/MessageList';
import { OtpVerification } from './components/OtpVerification';
import {
  createClientMessageId,
  getMessageQuickReplies,
  getOtpAction,
  parseConfirmationFields,
  isQuickReplyStale,
  type PendingReply,
} from './components/chat-utils';
import { Button } from '@/components/ui/button';
import api from '@/lib/api';
import toast from 'react-hot-toast';
function readStoredSession(storageKey: string): StoredChatSession | null {
  try {
    localStorage.removeItem(storageKey);
    const value = sessionStorage.getItem(storageKey);
    return value ? (JSON.parse(value) as StoredChatSession) : null;
  } catch {
    sessionStorage.removeItem(storageKey);
    return null;
  }
}
function validateOptionalContact(email: string, phone: string): string | null {
  const cleanEmail = email.trim();
  const cleanPhone = phone.trim();
  if (!cleanEmail && !cleanPhone) return 'Add an email or phone number, or continue without saving contact details.';
  if (cleanEmail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(cleanEmail)) {
    return 'Enter a valid email address.';
  }
  if (cleanPhone && cleanPhone.replace(/\D/g, '').length < 7) {
    return 'Enter a valid phone number.';
  }
  return null;
}
export function PublicChatPage() {
  const { slug = '' } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const storageKey = useMemo(() => `awa_public_chat_${slug}`, [slug]);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const lastHandledProcessingAtRef = useRef<string | null>(null);
  const [session, setSession] = useState<StoredChatSession | null>(() => readStoredSession(storageKey));
  const [visitorName, setVisitorName] = useState('');
  const [messageText, setMessageText] = useState('');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [noticeMessage, setNoticeMessage] = useState<string | null>(null);
  const [pendingReply, setPendingReply] = useState<PendingReply | null>(null);
  const [showSlowReply, setShowSlowReply] = useState(false);
  const [processingDelayed, setProcessingDelayed] = useState(false);
  const [processingDelayMessage, setProcessingDelayMessage] = useState<string | null>(null);
  const [isEnded, setIsEnded] = useState(false);
  const [isOnline, setIsOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine);
  const [contactEmail, setContactEmail] = useState('');
  const [contactPhone, setContactPhone] = useState('');
  const [contactSaved, setContactSaved] = useState(false);
  const [whatsappOptIn, setWhatsappOptIn] = useState(false);
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const [otpPhone, setOtpPhone] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [otpAction, setOtpAction] = useState('commitment');
  const [otpPromptVisible, setOtpPromptVisible] = useState(false);
  const [otpSentMessage, setOtpSentMessage] = useState<string | null>(null);
  const [pendingQuickReply, setPendingQuickReply] = useState<string | null>(null);
  const [sseActive, setSseActive] = useState(false);
  const [dismissedVerificationMsgId, setDismissedVerificationMsgId] = useState<string | null>(null);
  const [failedSend, setFailedSend] = useState<PublicChatSendAttempt | null>(null);
  const resetChat = () => {
    if (session?.id) {
      queryClient.removeQueries({ queryKey: ['public-chat-messages', session.id] });
    }
    sessionStorage.removeItem(storageKey);
    setSession(null);
    setVisitorName('');
    setMessageText('');
    setErrorMessage(null);
    setNoticeMessage(null);
    setPendingReply(null);
    setShowSlowReply(false);
    setProcessingDelayed(false);
    setIsEnded(false);
    setContactEmail('');
    setContactPhone('');
    setContactSaved(false);
    setWhatsappOptIn(false);
    setCaptchaToken(null);
    setOtpPhone('');
    setOtpCode('');
    setOtpAction('commitment');
    setOtpPromptVisible(false);
    setOtpSentMessage(null);
    setNoticeMessage(null);
    setPendingQuickReply(null);
    setDismissedVerificationMsgId(null);
    setFailedSend(null);
  };
  const [showScrollButton, setShowScrollButton] = useState(false);
  const linkQuery = useQuery({
    queryKey: ['public-chat-link', slug],
    queryFn: () => getPublicChatLink(slug),
    enabled: !!slug,
    retry: false,
  });
  const captchaMode = linkQuery.data?.captcha_mode || 'disabled';
  const turnstileSiteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY || '';
  const sessionCaptchaToken = !linkQuery.data?.captcha_required
    ? undefined
    : captchaMode === 'mock'
      ? captchaToken === 'local-captcha-ok' ? captchaToken : undefined
      : captchaMode === 'turnstile'
        ? captchaToken || undefined
        : undefined;
  const messagesQuery = useQuery({
    queryKey: ['public-chat-messages', session?.id],
    queryFn: () => getPublicChatMessages(session?.id ?? '', session?.session_token ?? ''),
    enabled: !!session?.id && !!session.session_token && !isEnded,
    refetchInterval: getPublicChatPollingInterval(Boolean(pendingReply), sseActive),
  });
  const createSession = useMutation({
    mutationFn: () => createPublicChatSessionWithVerification({
      slug,
      visitorName: visitorName.trim() || undefined,
      captchaToken: sessionCaptchaToken,
    }),
    onSuccess: (created: PublicChatSession) => {
      const stored = {
        id: created.id,
        session_token: created.session_token,
        tenant_name: created.tenant_name,
        display_name: created.display_name,
        conversation_id: created.conversation_id,
      };
      sessionStorage.setItem(storageKey, JSON.stringify(stored));
      setSession(stored);
      setIsEnded(false);
      setErrorMessage(null);
      setNoticeMessage(null);
    },
    onError: (error) => {
      setErrorMessage(getApiErrorMessage(error, 'Could not start chat. Please try again.'));
    },
  });
  const sendMessage = useMutation({
    mutationFn: (attempt: PublicChatSendAttempt) => sendPublicChatMessage({
      sessionId: session?.id ?? '',
      sessionToken: session?.session_token ?? '',
      messageText: attempt.text,
      clientMessageId: attempt.clientMessageId,
    }),
    onSuccess: (result, attempt) => {
      setMessageText((currentText) => {
        if (currentText.trim() !== attempt.text) return currentText;
        if (textareaRef.current) textareaRef.current.style.height = 'auto';
        return '';
      });
      setFailedSend((current) => (
        current?.clientMessageId === attempt.clientMessageId ? null : current
      ));
      setPendingQuickReply(null);
      queryClient.invalidateQueries({ queryKey: ['public-chat-messages', session?.id] });
      window.setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['public-chat-messages', session?.id] });
      }, 700);
      if (result.duplicate || result.processing_status === 'ignored') {
        setPendingReply(null);
        setShowSlowReply(false);
        setProcessingDelayed(false);
        setProcessingDelayMessage(null);
        setErrorMessage(null);
        setNoticeMessage(result.duplicate ? 'Message already received. No duplicate was sent.' : null);
        return;
      }
      if (result.processing_status === 'delayed' || result.status !== 'queued') {
        setProcessingDelayed(true);
        setProcessingDelayMessage(result.safe_error?.safe_message || result.processing_message || 'Message saved, but AWA processing is delayed. Replies may take longer than usual.');
        setShowSlowReply(true);
      } else {
        setProcessingDelayed(false);
        setProcessingDelayMessage(null);
      }
      setErrorMessage(null);
      setNoticeMessage(null);
    },
    onError: (error: unknown, attempt) => {
      setPendingQuickReply(null);
      setPendingReply(null);
      setShowSlowReply(false);
      const message = getApiErrorMessage(error, 'Could not send message. Please retry in a moment.');
      if (/verify your phone number/i.test(message)) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const payload = lastAssistantMessage?.raw_payload as Record<string, any> | undefined;
        const actionFromPayload = payload?.verification_action;
        const lastFields = lastAssistantMessage ? parseConfirmationFields(lastAssistantMessage.message_text) : [];
        setOtpAction(actionFromPayload || getOtpAction(lastFields));
        setOtpPhone((current) => current || contactPhone);
        setOtpPromptVisible(true);
        setFailedSend(null);
        setNoticeMessage(null);
        setErrorMessage(message);
        return;
      }
      const status = (error as { response?: { status: number } })?.response?.status;
      if (status === 403 || status === 404) {
        setFailedSend(null);
        resetChat();
        setErrorMessage('This chat session expired. Please start again.');
        return;
      }
      setFailedSend(attempt);
      setNoticeMessage(null);
      setErrorMessage(message);
    },
  });
  const sendOtp = useMutation({
    mutationFn: () => sendPublicChatOtp({
      sessionId: session?.id ?? '',
      sessionToken: session?.session_token ?? '',
      phone: otpPhone,
      action: otpAction,
    }),
    onSuccess: (result) => {
      setOtpSentMessage(result.test_code ? `Test code: ${result.test_code}` : 'Verification code sent.');
      setErrorMessage(null);
      setNoticeMessage(null);
    },
    onError: (error) => setErrorMessage(getApiErrorMessage(error, 'Could not send verification code. Please retry in a moment.')),
  });
  const verifyOtp = useMutation({
    mutationFn: () => verifyPublicChatOtp({
      sessionId: session?.id ?? '',
      sessionToken: session?.session_token ?? '',
      phone: otpPhone,
      action: otpAction,
      code: otpCode,
    }),
    onSuccess: () => {
      if (lastAssistantMessage?.id) {
        setDismissedVerificationMsgId(lastAssistantMessage.id);
      }
      setOtpPromptVisible(false);
      setOtpSentMessage(null);
      setErrorMessage(null);
      setNoticeMessage(null);
      setPendingReply(null);
      setShowSlowReply(false);
      setProcessingDelayed(false);
      queryClient.invalidateQueries({ queryKey: ['public-chat-messages', session?.id] });
      window.setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['public-chat-messages', session?.id] });
      }, 700);
    },
    onError: (error) => setErrorMessage(getApiErrorMessage(error, 'Could not verify code. Please check the code and retry.')),
  });
  const saveContact = useMutation({
    mutationFn: () =>
      savePublicChatContact({
        sessionId: session?.id ?? '',
        sessionToken: session?.session_token ?? '',
        email: contactEmail.trim() || undefined,
        phone: contactPhone.trim() || undefined,
        name: visitorName.trim() || undefined,
        whatsappOptIn,
      }),
    onSuccess: () => {
      setContactSaved(true);
      setErrorMessage(null);
      setNoticeMessage(null);
    },
    onError: (error) => {
      setErrorMessage(getApiErrorMessage(error, 'Could not save contact details'));
    },
  });
  const endChat = useMutation({
    mutationFn: () => endPublicChatSession({
      sessionId: session?.id ?? '',
      sessionToken: session?.session_token ?? '',
    }),
    onSuccess: () => {
      setIsEnded(true);
      setPendingReply(null);
      setShowSlowReply(false);
      setProcessingDelayed(false);
      setProcessingDelayMessage(null);
      setErrorMessage(null);
      setNoticeMessage(null);
    },
    onError: (error) => {
      setErrorMessage(getApiErrorMessage(error, 'Could not end this chat. Please try again.'));
    },
  });
  const businessName = session?.display_name || linkQuery.data?.display_name || 'AWA Chat';
  const tenantName = session?.tenant_name || linkQuery.data?.tenant_name || businessName;
  const privacyNote = linkQuery.data?.privacy_note || 'This browser chat is handled by AWA for this business. Share contact details only when needed.';
  const messages = useMemo(() => {
    return messagesQuery.data ?? [];
  }, [messagesQuery.data]);
  const lastAssistantMessage = [...messages].reverse().find((message) => message.direction === 'outbound');
  const conversationStatus = useMemo(() => {
    const latestReportedStatus = [...messages]
      .reverse()
      .find((message) => Boolean(message.conversation_status))
      ?.conversation_status;
    if (latestReportedStatus === 'escalated') {
      return 'escalated';
    }
    if (latestReportedStatus === 'active') {
      return 'active';
    }
    const lastAssistantText = lastAssistantMessage?.message_text || '';
    if (
      /asked the team to step in/i.test(lastAssistantText) ||
      /paused for human support/i.test(lastAssistantText)
    ) {
      return 'escalated';
    }
    return 'active';
  }, [messages, lastAssistantMessage]);
  const isEscalated = conversationStatus === 'escalated';
  const activeQuickReplies = lastAssistantMessage ? getMessageQuickReplies(lastAssistantMessage) : [];
  const escalationWaiting = isEscalated || Boolean(
    lastAssistantMessage &&
    (/awa is paused/i.test(lastAssistantMessage.message_text) || activeQuickReplies.includes('Wait for Team'))
  );
  const verificationPaused = otpPromptVisible && !isEscalated;
  const inputLocked = sendMessage.isPending || (!!pendingReply && !showSlowReply) || verificationPaused;
  const canSend = !!messageText.trim() && !!session && isOnline && !inputLocked;
  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);
  useEffect(() => {
    setCaptchaToken(null);
  }, [slug, captchaMode]);
  useEffect(() => {
    if (isEscalated) {
      setPendingReply(null);
      setShowSlowReply(false);
      setProcessingDelayed(false);
    }
  }, [isEscalated]);
  useEffect(() => {
    const transition = resolvePublicChatProcessingTransition(
      lastAssistantMessage,
      lastHandledProcessingAtRef.current,
    );
    if (!transition) return;
    lastHandledProcessingAtRef.current = transition.createdAt;

    setOtpPromptVisible(false);
    setOtpSentMessage(null);
    setOtpCode('');
    setPendingReply(null);
    setPendingQuickReply(null);
    setShowSlowReply(false);
    setProcessingDelayed(false);
    setProcessingDelayMessage(null);
    if (transition.kind === 'recoverable_failure') {
      setNoticeMessage('That request was not completed. You can confirm again, change a detail, or cancel.');
    } else if (transition.kind === 'terminal_failure') {
      setNoticeMessage('That verification or request can no longer be used. Please change or cancel it, or start a fresh confirmation.');
    }
  }, [lastAssistantMessage]);
  useEffect(() => {
    if (!lastAssistantMessage) return;
    if (lastAssistantMessage.id === dismissedVerificationMsgId) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const payload = lastAssistantMessage.raw_payload as Record<string, any> | undefined;
    if (payload && payload.required_next_step === 'customer_verification') {
      setOtpAction(payload.verification_action || 'commitment');
      setOtpPhone((current) => current || contactPhone);
      setOtpPromptVisible(true);
    }
  }, [lastAssistantMessage, contactPhone, dismissedVerificationMsgId]);
  useEffect(() => {
    if (!pendingReply) return undefined;
    const timeoutId = window.setTimeout(() => setShowSlowReply(true), SLOW_REPLY_MS);
    return () => window.clearTimeout(timeoutId);
  }, [pendingReply]);
  useEffect(() => {
    if (!pendingReply || messages.length <= pendingReply.messageCount) return;
    const latestMessage = messages[messages.length - 1];
    if (latestMessage?.direction === 'outbound') {
      window.setTimeout(() => {
        setPendingReply(null);
        setShowSlowReply(false);
        setProcessingDelayed(false);
      }, 0);
    }
  }, [messages, pendingReply]);
  useEffect(() => {
    requestAnimationFrame(() => {
      scrollAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
  }, [messages.length, pendingReply, sendMessage.isPending, isEnded]);
  // Handle session expiration (403/404 errors)
  useEffect(() => {
    const error = messagesQuery.error as { response?: { status: number } } | null;
    if (error && error.response) {
      const status = error.response.status;
      if (status === 403 || status === 404) {
        resetChat();
        setErrorMessage('This chat session expired. Please start again.');
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messagesQuery.error]);
  // SSE Realtime Integration
  useEffect(() => {
    if (!session?.id || !session?.session_token || isEnded) {
      return undefined;
    }
    let isMounted = true;
    let eventSource: EventSource | null = null;
    let reconnectTimeoutId: number | null = null;
    const cleanupEventSource = () => {
      if (!eventSource) return;
      eventSource.onopen = null;
      eventSource.onmessage = null;
      eventSource.onerror = null;
      eventSource.close();
      eventSource = null;
    };
    const scheduleReconnect = () => {
      if (!isMounted || isEnded || reconnectTimeoutId !== null) return;
      queryClient.invalidateQueries({ queryKey: ['public-chat-messages', session.id] });
      reconnectTimeoutId = window.setTimeout(() => {
        reconnectTimeoutId = null;
        console.log('Attempting to reconnect SSE...');
        if (isMounted) {
          connectSSE();
        }
      }, 5000);
    };
    const connectSSE = () => {
      if (!isMounted || isEnded) return;
      cleanupEventSource();
      const baseUrl = import.meta.env.VITE_API_URL || window.location.origin;
      const url = `${baseUrl.replace(/\/$/, '')}/api/v1/public-chat/sessions/${session.id}/events?token=${encodeURIComponent(session.session_token)}`;
      eventSource = new EventSource(url);
      eventSource.onopen = () => {
        console.log('SSE connection established');
        setSseActive(true);
        queryClient.invalidateQueries({ queryKey: ['public-chat-messages', session.id] });
      };
      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event_type === 'message') {
            const messageId = payload.message_id;
            const content = payload.content;
            const createdAt = payload.created_at;
            const role = payload.role;
            const newMessage: PublicChatMessage = {
              id: messageId,
              direction: role === 'assistant' ? 'outbound' : 'inbound',
              message_text: typeof content === 'string' ? content : '',
              created_at: createdAt,
              quick_replies: payload.quick_replies || [],
              quick_replies_stale: false,
              quick_replies_state: payload.quick_replies_state || 'none',
              cards: payload.cards || [],
              raw_payload: payload.raw_payload || {},
              processing_status: 'received',
              channel_type: 'awa_chat',
              conversation_status: typeof payload.conversation_status === 'string'
                ? payload.conversation_status
                : undefined,
            };
            queryClient.setQueryData<PublicChatMessage[]>(
              ['public-chat-messages', session.id],
              (oldMessages = []) => {
                const exists = oldMessages.some((msg) => msg.id === messageId);
                if (exists) {
                  return oldMessages;
                }
                const updated = [...oldMessages, newMessage];
                const latestOutboundId = [...updated].reverse().find((msg) => msg.direction === 'outbound')?.id;
                return updated.map((msg) => {
                  const quick_replies = getMessageQuickReplies(msg);
                  const isLatestOutbound = msg.id === latestOutboundId;
                  return {
                    ...msg,
                    quick_replies,
                    quick_replies_stale: quick_replies.length > 0 && !isLatestOutbound,
                    quick_replies_state: quick_replies.length > 0 ? (isLatestOutbound ? 'active' : 'expired') : 'none',
                  };
                });
              }
            );
          }
        } catch (err) {
          console.error('Failed to parse SSE event:', err);
        }
      };
      eventSource.onerror = (err) => {
        console.error('SSE connection error:', err);
        setSseActive(false);
        cleanupEventSource();
        scheduleReconnect();
      };
    };
    connectSSE();
    return () => {
      isMounted = false;
      cleanupEventSource();
      if (reconnectTimeoutId !== null) {
        window.clearTimeout(reconnectTimeoutId);
        reconnectTimeoutId = null;
      }
      setSseActive(false);
    };
  }, [session?.id, session?.session_token, isEnded, queryClient]);
  // Track whether the scroll anchor is visible to show/hide scroll-to-bottom button
  useEffect(() => {
    const anchor = scrollAnchorRef.current;
    if (!anchor) return;
    const observer = new IntersectionObserver(
      ([entry]) => setShowScrollButton(!entry.isIntersecting),
      { threshold: 0.1 }
    );
    observer.observe(anchor);
    return () => observer.disconnect();
  }, [session?.id]);
  const submitSendAttempt = (attempt: PublicChatSendAttempt) => {
    setPendingReply(isEscalated ? null : { messageCount: messages.length });
    setShowSlowReply(false);
    setProcessingDelayed(false);
    setProcessingDelayMessage(null);
    setErrorMessage(null);
    setNoticeMessage(null);
    sendMessage.mutate(attempt);
  };
  const handleSend = (text: string) => {
    const cleanText = text.trim();
    if (!cleanText || !session) return;
    if (!isOnline) {
      setErrorMessage('You are offline. Reconnect and send again.');
      return;
    }
    if (cleanText.length > MAX_MESSAGE_LENGTH) {
      setErrorMessage(`Keep messages under ${MAX_MESSAGE_LENGTH} characters.`);
      return;
    }
    if (verificationPaused) {
      setErrorMessage('Chat is paused for phone verification. Verify, cancel, or start a new chat.');
      return;
    }
    if (inputLocked) return;
    const attempt = {
      text: cleanText,
      clientMessageId: createClientMessageId(),
    };
    setFailedSend(null);
    submitSendAttempt(attempt);
  };
  const handleRetrySend = () => {
    if (!failedSend || !session || inputLocked) return;
    if (!isOnline) {
      setErrorMessage('You are offline. Reconnect before retrying this message.');
      return;
    }
    submitSendAttempt(failedSend);
  };
  const handleSaveContact = () => {
    const validationError = validateOptionalContact(contactEmail, contactPhone);
    if (validationError) {
      setErrorMessage(validationError);
      return;
    }
    saveContact.mutate();
  };
  const handleQuickReply = (messageId: string, reply: string) => {
    setPendingQuickReply(`${messageId}:${reply}`);
    handleSend(reply);
  };
  if (linkQuery.isLoading) {
    return <AwaSplash />;
  }
  if (linkQuery.isError) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-background p-4">
        <div className="max-w-sm rounded-md border bg-card p-5 text-center">
          <p className="text-[15px] font-semibold text-foreground">Chat link unavailable</p>
          <p className="mt-1 text-[13px] text-muted-foreground">This business may have paused or replaced the link. Please ask them for the latest chat link.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="public-chat-themed flex h-[100dvh] bg-background text-foreground" style={{ height: '100dvh' }}>
      <main className="mx-auto flex h-full w-full max-w-[760px] flex-col bg-background">
        <ChatHeader
          tenantName={tenantName}
          businessName={businessName}
          isOnline={!session ? isOnline : (isOnline && sseActive)}
          sessionActive={Boolean(session && !isEnded)}
          isEnding={endChat.isPending}
          onEndChat={() => endChat.mutate()}
          onResetChat={resetChat}
        />
        {!session ? (
          <ChatWelcome
            businessName={businessName}
            privacyNote={privacyNote}
            isOnline={isOnline}
            visitorName={visitorName}
            onVisitorNameChange={setVisitorName}
            captchaRequired={linkQuery.data?.captcha_required}
            captchaMode={captchaMode}
            captchaToken={captchaToken}
            onCaptchaTokenChange={setCaptchaToken}
            turnstileSiteKey={turnstileSiteKey}
            errorMessage={errorMessage}
            isStarting={createSession.isPending}
            onStart={() => createSession.mutate()}
          />
        ) : isEnded ? (
          <ChatEnded onResetChat={resetChat} />
        ) : (
          <>
            <MessageList
              messages={messages}
              lastAssistantMessage={lastAssistantMessage}
              privacyNote={privacyNote}
              isOnline={isOnline}
              isError={messagesQuery.isError}
              isFetching={messagesQuery.isFetching}
              onRetry={() => messagesQuery.refetch()}
              processingDelayed={processingDelayed}
              processingDelayMessage={processingDelayMessage}
              businessName={businessName}
              suggestedReplies={linkQuery.data?.suggested_replies}
              inputLocked={inputLocked}
              onQuickReply={handleQuickReply}
              pendingQuickReply={pendingQuickReply}
              sendMessagePending={sendMessage.isPending}
              pendingReply={pendingReply}
              showSlowReply={showSlowReply}
              scrollAnchorRef={scrollAnchorRef}
              showScrollButton={showScrollButton}
              onScrollToBottom={() => scrollAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })}
              conversationStatus={conversationStatus}
            >
              {escalationWaiting && (
                <ContactCapture
                  contactEmail={contactEmail}
                  contactPhone={contactPhone}
                  contactSaved={contactSaved}
                  onContactEmailChange={(val) => { setContactEmail(val); setContactSaved(false); }}
                  onContactPhoneChange={(val) => { setContactPhone(val); setContactSaved(false); }}
                  isSaving={saveContact.isPending}
                  onSaveContact={handleSaveContact}
                  onWhatsAppOptInChange={setWhatsappOptIn}
                />
              )}
              {otpPromptVisible && !isEscalated && (
                <div className="flex justify-start">
                  <div className="max-w-[400px] w-full">
                    <OtpVerification
                      otpPhone={otpPhone}
                      otpCode={otpCode}
                      onOtpPhoneChange={setOtpPhone}
                      onOtpCodeChange={setOtpCode}
                      isSendingOtp={sendOtp.isPending}
                      isVerifyingOtp={verifyOtp.isPending}
                      onSendOtp={() => sendOtp.mutate()}
                      onVerifyOtp={() => verifyOtp.mutate()}
                      onCancel={() => {
                        if (lastAssistantMessage?.id) {
                          setDismissedVerificationMsgId(lastAssistantMessage.id);
                        }
                        setOtpPromptVisible(false);
                        setOtpSentMessage(null);
                        setOtpCode('');
                        setErrorMessage(null);
                        setNoticeMessage('Verification cancelled. You can keep chatting, but protected bookings and orders still need verification before confirmation.');
                      }}
                      otpSentMessage={otpSentMessage}
                    />
                  </div>
                </div>
              )}
            </MessageList>
            {isEscalated && (
              <div className="border-t border-border bg-muted p-6 text-center">
                <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-amber-500/20 text-amber-500 dark:text-amber-400">
                  <UserCheck className="h-5 w-5" />
                </div>
                <h3 className="mt-3 text-sm font-medium text-foreground">Assigned to Human Team</h3>
                <p className="mt-1 text-xs text-muted-foreground mb-4">
                  Our team has been notified and a person will respond to you shortly.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={async () => {
                    if (!session) return;
                    try {
                      await api.post(`/api/v1/public-chat/sessions/${session.id}/cancel-handoff`, {
                        session_token: session.session_token
                      });
                      toast.success('Handoff cancelled. AWA is back online.');
                      queryClient.invalidateQueries({ queryKey: ['public-chat-messages', session.id] });
                    } catch (err) {
                      toast.error(getApiErrorMessage(err, 'Failed to cancel handoff. Please try again.'));
                    }
                  }}
                >
                  Cancel Handoff
                </Button>
              </div>
            )}
            <ChatFooter
              activeQuickReplies={[]}
              handleQuickReply={handleQuickReply}
              lastAssistantMessageId={lastAssistantMessage?.id}
              lastAssistantMessageStale={lastAssistantMessage ? isQuickReplyStale(lastAssistantMessage, lastAssistantMessage, messages) : false}
              pendingQuickReply={pendingQuickReply}
              inputLocked={inputLocked}
              isOnline={isOnline}
              errorMessage={errorMessage}
              noticeMessage={noticeMessage}
              canRetryFailedMessage={Boolean(failedSend)}
              onRetryFailedMessage={handleRetrySend}
              verificationPaused={verificationPaused}
              handoffActive={isEscalated}
              messageText={messageText}
              setMessageText={setMessageText}
              handleSend={handleSend}
              textareaRef={textareaRef}
              MAX_MESSAGE_LENGTH={MAX_MESSAGE_LENGTH}
              canSend={canSend}
              isSending={sendMessage.isPending}
            />
          </>
        )}
      </main>
    </div>
  );
}
