import { useFormContext, useWatch } from 'react-hook-form';
import { useState } from 'react';
import type { TenantConfig } from '@/types/config.types';
import { Info } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { normalizeAiMode, normalizeAllowedAiModes } from '@/lib/ai-mode';
type GeneralConfigEditorProps = {
  allowedAiModes?: string[];
  planCode?: string;
  capabilityProfiles?: Array<{
    code: string;
    label: string;
    runtime_engine: string;
    summary: string;
    ui_description: string;
    allowed: boolean;
    locked_reason?: string | null;
  }>;
  activeCapabilityPolicy?: {
    effective_mode: string;
    runtime_engine: string;
    label: string;
    summary: string;
    ui_description: string;
    downgraded: boolean;
    locked_reason?: string | null;
  };
};
const BUSINESS_TYPE_HELP: Record<string, string> = {
  clinic: 'Appointment-first profile with patient contact details, reminders, reviews, support, and careful handoff guidance.',
  salon: 'Booking-first profile with service timing, reminders, reviews, lead capture, FAQ, and handoff.',
  restaurant: 'Order-first profile with catalog, pickup/delivery, payments, reminders, reviews, FAQ, and handoff.',
  retail: 'Catalog/order-first profile with stock-aware orders, payments, lead capture, reminders, reviews, FAQ, and handoff.',
  services: 'Service-booking profile with contact details, service notes, support, reminders, reviews, lead capture, and handoff.',
  education: 'Inquiry and trial-booking profile with lead capture, reminders, reviews, FAQ, and handoff.',
  minimal: 'FAQ and handoff profile for businesses that want a narrow assistant until more workflows are configured.',
  general: 'General profile. Enabled tools and saved configuration decide what the assistant can do.',
};
const COMMON_TIMEZONES = [
  'UTC',
  'Africa/Cairo',
  'Africa/Johannesburg',
  'Africa/Lagos',
  'Africa/Nairobi',
  'America/Anchorage',
  'America/Argentina/Buenos_Aires',
  'America/Bogota',
  'America/Chicago',
  'America/Denver',
  'America/Halifax',
  'America/Los_Angeles',
  'America/Mexico_City',
  'America/New_York',
  'America/Phoenix',
  'America/Sao_Paulo',
  'America/Toronto',
  'America/Vancouver',
  'America/Whitehorse',
  'Asia/Dubai',
  'Asia/Hong_Kong',
  'Asia/Jakarta',
  'Asia/Karachi',
  'Asia/Kolkata',
  'Asia/Manila',
  'Asia/Riyadh',
  'Asia/Seoul',
  'Asia/Shanghai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Adelaide',
  'Australia/Brisbane',
  'Australia/Melbourne',
  'Australia/Perth',
  'Australia/Sydney',
  'Europe/Amsterdam',
  'Europe/Berlin',
  'Europe/Brussels',
  'Europe/London',
  'Europe/Madrid',
  'Europe/Paris',
  'Europe/Rome',
  'Pacific/Auckland',
  'Pacific/Honolulu',
] as const;
export function GeneralConfigEditor({
  allowedAiModes = ['standard'],
  capabilityProfiles = [],
  activeCapabilityPolicy,
}: GeneralConfigEditorProps) {
  const { register, control, setValue } = useFormContext<TenantConfig>();
  const timezone = useWatch({ control, name: 'timezone' });
  const businessType = useWatch({ control, name: 'business_type' }) || 'general';
  const aiMode = useWatch({ control, name: 'ai_mode' });
  const selectedAiMode = normalizeAiMode(aiMode);
  const normalizedAllowedAiModes = normalizeAllowedAiModes(allowedAiModes);
  const advancedAllowed = normalizedAllowedAiModes.includes('advanced');
  const selectedProfile = capabilityProfiles?.find((profile) => normalizeAiMode(profile.code) === selectedAiMode);
  const publicChatCaptchaRequired = useWatch({ control, name: 'security.public_chat_captcha_required' }) === true;
  const [tzSearch, setTzSearch] = useState('');

  const filteredTz: string[] = COMMON_TIMEZONES.filter(tz =>
    tz.toLowerCase().includes(tzSearch.toLowerCase())
  );
  if (timezone && !COMMON_TIMEZONES.includes(timezone as (typeof COMMON_TIMEZONES)[number])) {
    filteredTz.unshift(timezone);
  }
  return (
    <div className="w-full max-w-4xl space-y-6">
      <div>
        <h3 className="text-lg font-medium">General Operating Profile</h3>
        <p className="text-sm text-muted-foreground">
          Start here for business identity, AI behavior, human handoff details, and customer verification before sharing AWA Chat.
        </p>
      </div>
      <div className="space-y-4">
        <div className="rounded-md border bg-muted/30 px-3 py-2">
          <p className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">Business identity</p>
          <p className="mt-1 text-[12px] leading-5 text-muted-foreground">These details shape how AWA introduces and describes the business.</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="business_name">Business Name</Label>
          <Input id="business_name" {...register('business_name')} placeholder="e.g., Downtown Dental" />
          <p className="text-xs text-muted-foreground">This name is injected into the AI system prompt dynamically.</p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="business_type">Business Type</Label>
            <Input id="business_type" {...register('business_type')} placeholder="clinic, salon, restaurant" />
            <p className="text-xs text-muted-foreground">
              {BUSINESS_TYPE_HELP[String(businessType).toLowerCase()] || BUSINESS_TYPE_HELP.general}
            </p>
          </div>
          <div className="space-y-2">
            <Label>AI Capability Profile</Label>
            <input type="hidden" {...register('ai_mode')} />
            <Select value={selectedAiMode} onValueChange={(val) => setValue('ai_mode', normalizeAiMode(val) as TenantConfig['ai_mode'], { shouldDirty: true })}>
              <SelectTrigger>
                <SelectValue placeholder="Select AI capability profile" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="standard">Standard AI</SelectItem>
                <SelectItem value="advanced" disabled={!advancedAllowed}>
                  Advanced AI{advancedAllowed ? '' : ' - upgrade required'}
                </SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {selectedProfile?.ui_description || activeCapabilityPolicy?.ui_description || 'All profiles use the unified AWA agent; plans and enabled tools decide what it can do.'}
            </p>
            {!advancedAllowed && (
              <div className="mt-2 rounded-md border border-blue-500/30 bg-blue-500/10 p-3 text-[13px] text-blue-950 dark:text-blue-100">
                <div className="flex items-start gap-2.5">
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600 dark:text-blue-400" />
                  <div>
                    <p className="font-semibold text-blue-900 dark:text-blue-200">Advanced AI is disabled</p>
                    <p className="mt-1 text-[12px] text-blue-800/90 dark:text-blue-300/90">
                      Upgrade to a plan that includes Advanced AI. Your AWA agent will run in Standard AI mode.
                    </p>
                  </div>
                </div>
              </div>
            )}
            {activeCapabilityPolicy?.downgraded && activeCapabilityPolicy.locked_reason && (
              <p className="text-xs text-amber-600">{activeCapabilityPolicy.locked_reason}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="timezone">Operating Timezone</Label>
            <div className="space-y-1.5">
              <Input
                type="text"
                placeholder="Search your city or timezone..."
                value={tzSearch}
                onChange={(e) => setTzSearch(e.target.value)}
                className="h-8 text-[12px]"
                aria-label="Search operating timezone"
              />
              <input type="hidden" {...register('timezone')} />
              <Select value={timezone || ''} onValueChange={(val) => setValue('timezone', val, { shouldDirty: true })}>
                <SelectTrigger id="timezone">
                  <SelectValue placeholder="Select Timezone" />
                </SelectTrigger>
                <SelectContent className="max-h-[200px]">
                  {filteredTz.map(tz => (
                    <SelectItem key={tz} value={tz}>{tz}</SelectItem>
                  ))}
                  {filteredTz.length === 0 && (
                    <p className="p-2 text-xs text-muted-foreground text-center">No timezones found</p>
                  )}
                </SelectContent>
              </Select>
            </div>
            <p className="text-xs text-muted-foreground">Search by city or zone (e.g. New York, London, Kolkata) to filter the list.</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="default_language">Primary Language</Label>
            <Input id="default_language" {...register('default_language')} placeholder="en" />
          </div>
        </div>
        <div className="space-y-2 pt-4 border-t border-border">
          <div className="mb-3 rounded-md border bg-muted/30 px-3 py-2">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">AI behavior</p>
            <p className="mt-1 text-[12px] leading-5 text-muted-foreground">Tone, greeting, and instructions should be safe for customers to see.</p>
          </div>
          <Label htmlFor="ai_tone">AI Tone</Label>
          <Input id="ai_tone" {...register('ai_tone')} placeholder="professional and friendly" />
        </div>
        <div className="space-y-2 pt-4 border-t border-border">
          <Label htmlFor="greeting_message">Standard Greeting Message</Label>
          <Textarea
            id="greeting_message"
            {...register('greeting_message')}
            rows={3}
            placeholder="Welcome to our clinic! How can we assist you today?"
            className="resize-none"
          />
          <p className="text-xs text-muted-foreground">Used as the opening customer-facing message. Runtime still uses the unified AWA agent.</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="custom_instructions">Business Instructions</Label>
          <Textarea
            id="custom_instructions"
            {...register('custom_instructions')}
            rows={4}
            placeholder="Policies, service rules, handoff notes, and business-specific guidance."
            className="resize-none"
          />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t border-border">
           <div className="md:col-span-2 rounded-md border bg-muted/30 px-3 py-2">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">Human handoff</p>
            <p className="mt-1 text-[12px] leading-5 text-muted-foreground">Use team-owned contact details for customer issues AWA cannot resolve.</p>
           </div>
           <div className="space-y-2">
            <Label>Escalation Phone (Human Handoff)</Label>
            <Input {...register('escalation_phone')} placeholder="+1 234 567 8900" />
           </div>
           <div className="space-y-2">
            <Label>Escalation Email</Label>
            <Input type="email" {...register('escalation_email')} placeholder="support@business.com" />
           </div>
        </div>
        <div className="space-y-3 pt-4 border-t border-border">
          <div>
            <h4 className="text-sm font-semibold">Customer verification</h4>
            <p className="text-xs text-muted-foreground">Reduce spam and require phone ownership before real bookings or orders are confirmed.</p>
          </div>
          <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
            <div>
              <Label>Require CAPTCHA before public chat starts</Label>
              <p className="text-xs text-muted-foreground">Customers complete a bot check before a browser chat session is created.</p>
            </div>
            <Switch
              checked={publicChatCaptchaRequired}
              onCheckedChange={(checked) => setValue('security.public_chat_captcha_required', checked, { shouldDirty: true })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
