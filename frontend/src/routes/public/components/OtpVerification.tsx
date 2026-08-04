import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface OtpVerificationProps {
  otpPhone: string;
  otpCode: string;
  onOtpPhoneChange: (phone: string) => void;
  onOtpCodeChange: (code: string) => void;
  isSendingOtp: boolean;
  isVerifyingOtp: boolean;
  onSendOtp: () => void;
  onVerifyOtp: () => void;
  onCancel: () => void;
  otpSentMessage?: string | null;
}

export function OtpVerification({
  otpPhone,
  otpCode,
  onOtpPhoneChange,
  onOtpCodeChange,
  isSendingOtp,
  isVerifyingOtp,
  onSendOtp,
  onVerifyOtp,
  onCancel,
  otpSentMessage,
}: OtpVerificationProps) {
  return (
    <div className="rounded-md border border-emerald-200 dark:border-emerald-500/20 bg-emerald-50 dark:bg-emerald-500/10 p-3 text-[12px] leading-5 text-emerald-800 dark:text-emerald-100">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-emerald-950 dark:text-emerald-50">Verify your phone once for this chat session</p>
          <p className="mt-1 text-emerald-800/80 dark:text-emerald-100/80">
            Chat input is paused while this verification is open. Phone possession is required before protected actions can continue.
          </p>
        </div>
        <button
          type="button"
          className="shrink-0 rounded-full border border-emerald-300 dark:border-emerald-500/30 px-2.5 py-1 text-[11px] font-medium text-emerald-800 dark:text-emerald-50 transition hover:bg-emerald-100 dark:hover:bg-emerald-500/10"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      <p className="mt-1 text-emerald-800/80 dark:text-emerald-100/80">
        This verifies phone possession for this browser chat; it is not legal identity verification.
      </p>
      <div className="mt-2 grid grid-cols-[1fr_100px] gap-2">
        <Input
          value={otpPhone}
          onChange={(event) => onOtpPhoneChange(event.target.value)}
          type="tel"
          placeholder="Phone number"
          aria-label="Phone number"
          className="h-9 border-input bg-background text-foreground placeholder:text-muted-foreground"
        />
        <Button
          type="button"
          size="sm"
          className="h-9 bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-zinc-950 dark:hover:bg-emerald-400"
          onClick={onSendOtp}
          disabled={isSendingOtp || otpPhone.replace(/\D/g, '').length < 7}
        >
          {isSendingOtp ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Send code'}
        </Button>
      </div>
      <div className="mt-2 grid grid-cols-[1fr_100px] gap-2">
        <Input
          value={otpCode}
          onChange={(event) => onOtpCodeChange(event.target.value)}
          inputMode="numeric"
          placeholder="Code"
          aria-label="Verification code"
          className="h-9 border-input bg-background text-foreground placeholder:text-muted-foreground"
        />
        <Button
          type="button"
          size="sm"
          className="h-9 bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-zinc-950 dark:hover:bg-emerald-400"
          onClick={onVerifyOtp}
          disabled={isVerifyingOtp || otpCode.trim().length < 4}
        >
          {isVerifyingOtp ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Verify'}
        </Button>
      </div>
      {otpSentMessage && (
        <p className="mt-2 text-[12px] text-emerald-600 dark:text-emerald-200" role="status" aria-live="polite" aria-atomic="true">
          {otpSentMessage}
        </p>
      )}
    </div>
  );
}
