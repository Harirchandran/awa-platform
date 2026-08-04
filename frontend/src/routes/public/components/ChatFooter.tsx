/* eslint-disable react-hooks/set-state-in-effect */
import { useEffect, useRef, useState } from 'react';
import { Loader2, Mic, RefreshCcw, Send, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
type BrowserSpeechRecognitionAlternative = {
  transcript: string;
};
type BrowserSpeechRecognitionResult = {
  isFinal: boolean;
  [index: number]: BrowserSpeechRecognitionAlternative;
};
type BrowserSpeechRecognitionResultList = {
  length: number;
  [index: number]: BrowserSpeechRecognitionResult;
};
type BrowserSpeechRecognitionEvent = Event & {
  resultIndex: number;
  results: BrowserSpeechRecognitionResultList;
};
type BrowserSpeechRecognitionErrorEvent = Event & {
  error: string;
};
type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
};
type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;
function getSpeechRecognitionConstructor(): BrowserSpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null;
  const speechWindow = window as Window & {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  };
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null;
}
interface ChatFooterProps {
  activeQuickReplies: string[];
  handleQuickReply: (messageId: string, reply: string) => void;
  lastAssistantMessageId: string | undefined;
  lastAssistantMessageStale: boolean;
  pendingQuickReply: string | null;
  inputLocked: boolean;
  isOnline: boolean;
  errorMessage: string | null;
  noticeMessage: string | null;
  canRetryFailedMessage: boolean;
  onRetryFailedMessage: () => void;
  verificationPaused: boolean;
  handoffActive?: boolean;
  messageText: string;
  setMessageText: (text: string) => void;
  handleSend: (text: string) => void;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  MAX_MESSAGE_LENGTH: number;
  canSend: boolean;
  isSending: boolean;
}
export function ChatFooter({
  activeQuickReplies,
  handleQuickReply,
  lastAssistantMessageId,
  lastAssistantMessageStale,
  pendingQuickReply,
  inputLocked,
  isOnline,
  errorMessage,
  noticeMessage,
  canRetryFailedMessage,
  onRetryFailedMessage,
  verificationPaused,
  handoffActive = false,
  messageText,
  setMessageText,
  handleSend,
  textareaRef,
  MAX_MESSAGE_LENGTH,
  canSend,
  isSending,
}: ChatFooterProps) {
  const [speechSupported, setSpeechSupported] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [speechNotice, setSpeechNotice] = useState<string | null>(null);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const transcriptRef = useRef('');
  const shouldSendSpeechRef = useRef(false);
  const canUseSpeech = speechSupported && isOnline && !inputLocked && !verificationPaused && !isSending;
  useEffect(() => {
    setSpeechSupported(Boolean(getSpeechRecognitionConstructor()));
    return () => {
      recognitionRef.current?.abort();
      recognitionRef.current = null;
    };
  }, []);
  const stopSpeech = (sendAfterStop: boolean) => {
    const recognition = recognitionRef.current;
    if (!recognition) return;
    shouldSendSpeechRef.current = sendAfterStop;
    recognition.stop();
  };
  const startSpeech = () => {
    if (!canUseSpeech) return;
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) {
      setSpeechSupported(false);
      return;
    }
    recognitionRef.current?.abort();
    transcriptRef.current = '';
    shouldSendSpeechRef.current = false;
    setSpeechNotice('Listening... Tap mic again to send.');
    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || 'en-US';
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      let transcript = '';
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        transcript += event.results[index][0]?.transcript ?? '';
      }
      const cleanTranscript = transcript.trim();
      if (cleanTranscript) {
        transcriptRef.current = cleanTranscript;
        setMessageText(cleanTranscript.slice(0, MAX_MESSAGE_LENGTH));
      }
    };
    recognition.onerror = (event) => {
      shouldSendSpeechRef.current = false;
      setIsListening(false);
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        setSpeechNotice('Microphone permission is blocked.');
      } else if (event.error === 'no-speech') {
        setSpeechNotice('No speech detected.');
      } else {
        setSpeechNotice('Voice input stopped. Try again.');
      }
    };
    recognition.onend = () => {
      const transcript = transcriptRef.current.trim().slice(0, MAX_MESSAGE_LENGTH);
      const shouldSend = shouldSendSpeechRef.current;
      recognitionRef.current = null;
      shouldSendSpeechRef.current = false;
      setIsListening(false);
      if (shouldSend && transcript) {
        setSpeechNotice(null);
        handleSend(transcript);
        return;
      }
      if (shouldSend) {
        setSpeechNotice('No speech detected.');
      }
    };
    recognitionRef.current = recognition;
    setIsListening(true);
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setIsListening(false);
      setSpeechNotice('Voice input unavailable');
    }
  };
  const handleMicClick = () => {
    if (!canUseSpeech) return;
    if (isListening) {
      stopSpeech(true);
    } else {
      startSpeech();
    }
  };
  const routineStatus = isSending
    ? 'Sending message.'
    : noticeMessage
      ? noticeMessage
      : speechNotice
        ? speechNotice
        : handoffActive
          ? 'AWA is paused. New messages will be added for the human team.'
          : verificationPaused
            ? 'Chat paused for phone verification. Verify or cancel above.'
            : inputLocked
              ? 'Waiting for AWA before the next message.'
              : '';
  const routineStatusHighlighted = Boolean(
    isSending || noticeMessage || isListening || verificationPaused,
  );

  // Automatically restore focus to the textarea after the assistant replies
  useEffect(() => {
    if (!inputLocked && !verificationPaused && isOnline) {
      if (document.activeElement === document.body || document.activeElement === textareaRef.current) {
        textareaRef.current?.focus();
      }
    }
  }, [inputLocked, verificationPaused, isOnline, textareaRef]);

  return (
    <footer className="relative border-t border-white/10 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      {!!activeQuickReplies.length && !lastAssistantMessageStale && (
        <div className="mb-2 flex gap-2 overflow-x-auto pb-1 md:flex-wrap md:overflow-x-visible [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
          {activeQuickReplies.map((reply) => (
            <button
              key={`footer-${reply}`}
              type="button"
              onClick={() => handleQuickReply(lastAssistantMessageId || 'footer', reply)}
              disabled={inputLocked || !isOnline || lastAssistantMessageStale}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-emerald-300 dark:border-emerald-500/30 bg-emerald-50 dark:bg-emerald-500/10 px-3 py-1.5 text-[13px] text-emerald-800 dark:text-emerald-100 transition hover:bg-emerald-100 dark:hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {pendingQuickReply === `${lastAssistantMessageId || 'footer'}:${reply}` && <Loader2 className="h-3 w-3 animate-spin" />}
              {reply}
            </button>
          ))}
        </div>
      )}
      <div className="mb-2 flex min-h-5 items-center justify-between gap-3">
        {errorMessage ? (
          <div
            className="flex min-w-0 flex-1 items-center gap-2"
            role="alert"
            aria-live="assertive"
            aria-atomic="true"
          >
            <p className="min-w-0 flex-1 text-[12px] text-red-600 dark:text-red-400">{errorMessage}</p>
            {canRetryFailedMessage && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 shrink-0 gap-1.5 px-2 text-[12px]"
                onClick={onRetryFailedMessage}
                disabled={isSending || !isOnline}
              >
                <RefreshCcw className="h-3 w-3" />
                Retry
              </Button>
            )}
          </div>
        ) : (
          <p
            className={`min-w-0 flex-1 text-[12px] ${
              routineStatusHighlighted
                ? 'text-emerald-600 dark:text-emerald-200'
                : 'text-muted-foreground'
            }`}
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            {routineStatus || <span aria-hidden="true">&nbsp;</span>}
          </p>
        )}
        {messageText.length >= 600 && (
          <span className={`shrink-0 text-[11px] ${
            messageText.length >= 780 ? 'text-destructive font-bold' :
            messageText.length >= 720 ? 'text-amber-500 font-medium' :
            'text-muted-foreground'
          }`}>
            {messageText.length}/{MAX_MESSAGE_LENGTH}
          </span>
        )}
      </div>
      <form
        className="flex items-end gap-2"
        aria-busy={isSending}
        onSubmit={(event) => {
          event.preventDefault();
          handleSend(messageText);
        }}
      >
        <div className="grid w-full relative">
          <div
            className="invisible whitespace-pre-wrap break-words px-3 py-2.5 text-[14px] leading-6 min-h-[44px] max-h-[120px] overflow-hidden col-start-1 row-start-1"
            aria-hidden="true"
          >
            {messageText + '\n'}
          </div>
          <textarea
            ref={textareaRef}
            autoFocus
            value={messageText}
            onChange={(event) => {
              setMessageText(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                handleSend(messageText);
              }
            }}
            maxLength={MAX_MESSAGE_LENGTH}
            disabled={!isOnline || verificationPaused || inputLocked}
            placeholder={
              verificationPaused
                ? 'Paused for verification'
                : handoffActive
                  ? 'Add context for the team'
                : !isOnline
                  ? 'Waiting for connection'
                  : inputLocked
                    ? 'AWA is typing...'
                    : 'Type your message'
            }
            aria-label="Type your message"
            enterKeyHint="send"
            className="w-full resize-none rounded-md border border-input bg-background px-3 py-2.5 text-[14px] leading-6 text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 col-start-1 row-start-1 min-h-[44px] max-h-[120px] overflow-y-auto"
          />
        </div>
        {messageText && (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-11 w-11 shrink-0 self-end text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            onClick={() => setMessageText('')}
            aria-label="Clear message"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
        {speechSupported && (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={`h-11 w-11 shrink-0 self-end border border-input bg-background ${
              isListening
                ? 'bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-zinc-950 dark:hover:bg-emerald-400'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
            }`}
            disabled={!canUseSpeech}
            aria-label={isListening ? 'Tap to send voice message' : 'Tap to speak'}
            title={isListening ? 'Tap to send' : 'Tap to speak'}
            onClick={handleMicClick}
          >
            <Mic className="h-4 w-4" />
          </Button>
        )}
        <Button type="submit" size="icon" className="motion-button-press h-11 w-11 shrink-0 self-end bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-zinc-950 dark:hover:bg-emerald-400" disabled={!canSend} aria-label="Send message">
          {isSending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </form>
    </footer>
  );
}
