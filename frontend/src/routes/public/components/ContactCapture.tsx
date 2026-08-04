import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface ContactCaptureProps {
  contactEmail: string;
  contactPhone: string;
  contactSaved: boolean;
  onContactEmailChange: (email: string) => void;
  onContactPhoneChange: (phone: string) => void;
  isSaving: boolean;
  onSaveContact: () => void;
  /** Called whenever the WhatsApp opt-in checkbox changes so the parent can
   *  include the latest value in the contact-save API call. */
  onWhatsAppOptInChange?: (optedIn: boolean) => void;
}

export function ContactCapture({
  contactEmail,
  contactPhone,
  contactSaved,
  onContactEmailChange,
  onContactPhoneChange,
  isSaving,
  onSaveContact,
  onWhatsAppOptInChange,
}: ContactCaptureProps) {
  const [whatsappOptIn, setWhatsappOptIn] = useState(false);

  const handleOptInChange = (checked: boolean) => {
    setWhatsappOptIn(checked);
    onWhatsAppOptInChange?.(checked);
  };

  return (
    <div className="rounded-md border border-amber-200 dark:border-amber-500/20 bg-amber-50 dark:bg-amber-500/10 p-3 text-[12px] leading-5 text-amber-800 dark:text-amber-100">
      <p className="font-semibold text-amber-950 dark:text-amber-50">Optional follow-up contact</p>
      <p className="mt-1 text-amber-800/80 dark:text-amber-100/80">
        Share this only if you want the team to reply after you leave this browser chat.
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <Input
          value={contactEmail}
          onChange={(event) => onContactEmailChange(event.target.value)}
          type="email"
          placeholder="Email"
          aria-label="Email"
          className="h-9 border-input bg-background text-foreground placeholder:text-muted-foreground"
        />
        <Input
          value={contactPhone}
          onChange={(event) => onContactPhoneChange(event.target.value)}
          type="tel"
          placeholder="Phone"
          aria-label="Phone"
          className="h-9 border-input bg-background text-foreground placeholder:text-muted-foreground"
        />
      </div>
      <label className="mt-2 flex cursor-pointer items-start gap-2 text-[11px] leading-4 text-amber-800/80 dark:text-amber-100/80">
        <input
          id="whatsapp-opt-in"
          type="checkbox"
          checked={whatsappOptIn}
          onChange={(e) => handleOptInChange(e.target.checked)}
          className="mt-0.5 h-3.5 w-3.5 cursor-pointer accent-amber-500"
        />
        <span>
          I agree to receive WhatsApp updates and reminders regarding my request.
        </span>
      </label>
      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="text-amber-800/75 dark:text-amber-100/75" role="status" aria-live="polite" aria-atomic="true">
          {contactSaved ? 'Saved' : 'Email helps if you close the chat. Phone is optional.'}
        </span>
        <Button
          type="button"
          size="sm"
          className="h-8 bg-amber-600 text-white hover:bg-amber-500 dark:bg-amber-100 dark:text-zinc-950 dark:hover:bg-amber-50"
          onClick={onSaveContact}
          disabled={isSaving || (!contactEmail.trim() && !contactPhone.trim())}
        >
          {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Save contact'}
        </Button>
      </div>
    </div>
  );
}
