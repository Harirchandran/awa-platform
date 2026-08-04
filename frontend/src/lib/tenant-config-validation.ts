import type {
  FAQ,
  ModuleField,
  OrderCategoryConfig,
  OrderModuleConfig,
  Service,
  TenantConfig,
} from '@/types/config.types';
import { normalizeAiMode } from '@/lib/ai-mode';
const PROMPT_INJECTION_PATTERNS = [
  /\b(ignore|forget|disregard|override)\s+(all\s+)?(previous|prior|above|system|developer)\s+instructions\b/i,
  /\b(reveal|show|print|leak|dump)\s+(the\s+)?(system|developer)\s+(prompt|message|instructions)\b/i,
  /(^|\n)\s*(system|developer|assistant|tool)\s*:/i,
  /\b(jailbreak|prompt injection|dan mode|bypass safety|bypass policy)\b/i,
  /\b(exfiltrate|steal)\s+(data|secrets|api keys|tokens)\b/i,
];
function hasPromptInjectionRisk(value?: string | null) {
  const text = value?.trim();
  return Boolean(text && PROMPT_INJECTION_PATTERNS.some((pattern) => pattern.test(text)));
}
function validateText(
  errors: string[],
  label: string,
  value: string | undefined | null,
  options: { required?: boolean; max?: number; aiText?: boolean } = {},
) {
  const text = value?.trim() || '';
  if (options.required && !text) {
    errors.push(`${label} is required.`);
    return;
  }
  if (options.max && text.length > options.max) {
    errors.push(`${label} must be ${options.max} characters or fewer.`);
  }
  if (options.aiText && hasPromptInjectionRisk(text)) {
    errors.push(`${label} looks like a system override or prompt-injection instruction. Keep it to normal business policy, tone, or customer-facing answers.`);
  }
}
function validateServices(errors: string[], services: Service[]) {
  services.forEach((service, index) => {
    const label = `Service ${index + 1}`;
    validateText(errors, `${label} name`, service.name, { required: true, max: 120 });
    if (!Number.isFinite(service.duration_mins) || service.duration_mins < 5 || service.duration_mins > 1440) {
      errors.push(`${label} duration must be between 5 and 1440 minutes.`);
    }
    if (
      service.price !== undefined &&
      service.price !== null &&
      (!Number.isFinite(service.price) || service.price < 0)
    ) {
      errors.push(`${label} price must be zero or higher.`);
    }
  });
}
function validateModuleFields(errors: string[], moduleName: string, fields: ModuleField[]) {
  fields.forEach((field, index) => {
    const label = `${moduleName} question ${index + 1}`;
    validateText(errors, `${label} key`, field.key, { required: true, max: 64 });
    validateText(errors, `${label} label`, field.label, { max: 120 });
    if (field.key && !/^[a-zA-Z0-9_]+$/.test(field.key)) {
      errors.push(`${label} key may contain only letters, numbers, and underscores.`);
    }
    if (!['string', 'number', 'date', 'time', 'email', 'phone', 'select'].includes(field.type)) {
      errors.push(`${label} answer type is not supported.`);
    }
    if (field.type === 'select' && field.options && field.options.length > 60) {
      errors.push(`${label} has too many select options.`);
    }
  });
}
function validateFaqs(errors: string[], faqs: FAQ[]) {
  faqs.forEach((faq, index) => {
    const label = `FAQ ${index + 1}`;
    validateText(errors, `${label} question`, faq.question, { required: true, max: 300, aiText: true });
    validateText(errors, `${label} answer`, faq.answer, { required: true, max: 1200, aiText: true });
  });
}
function validateOrderCatalog(errors: string[], orderConfig?: OrderModuleConfig) {
  if (!orderConfig) return;
  validateText(errors, 'Order currency', orderConfig.currency || 'INR', { required: true, max: 8 });
  if (orderConfig.currency && !/^[A-Z]{3,8}$/i.test(orderConfig.currency.trim())) {
    errors.push('Order currency must be an ISO-style code like INR or USD.');
  }
  const fulfillmentTypes = (orderConfig.fulfillment_types || [])
    .map((type) => type.trim())
    .filter(Boolean);
  if (orderConfig.enabled && fulfillmentTypes.length === 0) {
    errors.push('Order fulfillment options must include at least one option.');
  }
  (orderConfig.categories || []).forEach((category: OrderCategoryConfig, categoryIndex) => {
    const categoryLabel = `Order category ${categoryIndex + 1}`;
    validateText(errors, `${categoryLabel} name`, category.name, { required: true, max: 120, aiText: true });
    (category.items || []).forEach((item, itemIndex) => {
      const itemLabel = `${categoryLabel} item ${itemIndex + 1}`;
      validateText(errors, `${itemLabel} name`, item.name, { required: true, max: 160, aiText: true });
      validateText(errors, `${itemLabel} description`, item.description, { max: 800, aiText: true });
      if (!Number.isFinite(item.price) || item.price < 0) {
        errors.push(`${itemLabel} price cannot be negative.`);
      }
      if (
        item.preparation_minutes !== undefined &&
        (!Number.isFinite(item.preparation_minutes) || item.preparation_minutes < 0 || item.preparation_minutes > 1440)
      ) {
        errors.push(`${itemLabel} prep time must be between 0 and 1440 minutes.`);
      }
    });
  });
}
function validateBookingSettings(errors: string[], config: TenantConfig) {
  const settings = config.booking_settings;
  if (!settings) return;
  if (settings.confirmation_mode && !['auto_confirm', 'approval_required'].includes(settings.confirmation_mode)) {
    errors.push('Booking confirmation mode is not supported.');
  }
  const numericRules: Array<[string, number | undefined, number, number]> = [
    ['Minimum booking notice', settings.minimum_notice_minutes, 0, 43200],
    ['Advance booking days', settings.advance_booking_days, 1, 730],
    ['Slot interval', settings.slot_interval_minutes, 5, 1440],
    ['Buffer before booking', settings.buffer_before_mins, 0, 1440],
    ['Buffer after booking', settings.buffer_after_mins, 0, 1440],
    ['Maximum bookings per day', settings.max_bookings_per_day, 1, 10000],
    ['Cancellation cutoff', settings.cancellation_cutoff_minutes, 0, 43200],
  ];
  numericRules.forEach(([label, value, min, max]) => {
    if (value !== undefined && (!Number.isFinite(value) || value < min || value > max)) {
      errors.push(`${label} must be between ${min} and ${max}.`);
    }
  });
  (settings.approval_required_for || []).forEach((condition, index) => {
    validateText(errors, `Approval condition ${index + 1}`, condition, { max: 120, aiText: true });
  });
}
export function validateTenantConfig(config: TenantConfig) {
  const errors: string[] = [];
  validateText(errors, 'Business name', config.business_name, { required: true, max: 120 });
  validateText(errors, 'Business type', config.business_type, { required: true, max: 80 });
  validateText(errors, 'Operating timezone', config.timezone, { required: true, max: 80 });
  validateText(errors, 'Primary language', config.default_language, { required: true, max: 12 });
  validateText(errors, 'AI tone', config.ai_tone, { required: true, max: 160, aiText: true });
  validateText(errors, 'Greeting message', config.greeting_message, { required: true, max: 500, aiText: true });
  validateText(errors, 'Fallback message', config.fallback_message, { max: 500, aiText: true });
  validateText(errors, 'Escalation message', config.escalation_message, { max: 500, aiText: true });
  validateText(errors, 'Business instructions', config.custom_instructions, { max: 2000, aiText: true });
  validateText(errors, 'Escalation email', config.escalation_email, { max: 160 });
  if (config.escalation_email && config.escalation_email.trim()) {
    const emails = config.escalation_email.split(',').map((e) => e.trim()).filter(Boolean);
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const hasInvalid = emails.some((e) => !emailRegex.test(e));
    if (hasInvalid || emails.length === 0) {
      errors.push('Escalation email must be a valid email address.');
    }
  }
  validateText(errors, 'Escalation phone', config.escalation_phone, { max: 40 });
  (config.ignored_numbers || []).forEach((number, index) => {
    if (!/^\+?[0-9][0-9\s().-]{5,31}$/.test(number.trim())) {
      errors.push(`Do-not-auto-reply number ${index + 1} must be a valid phone-like value.`);
    }
  });
  if (!['standard', 'advanced'].includes(normalizeAiMode(config.ai_mode))) {
    errors.push('AI mode must be Standard AI or Advanced AI.');
  }
  validateServices(errors, config.services || []);
  validateFaqs(errors, config.faqs || []);
  Object.entries(config.modules || {}).forEach(([moduleName, moduleConfig]) => {
    validateModuleFields(errors, moduleName, moduleConfig.fields || []);
  });
  validateOrderCatalog(errors, config.modules?.order as OrderModuleConfig | undefined);
  validateBookingSettings(errors, config);
  return errors;
}
