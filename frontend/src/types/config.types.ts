export interface DaySchedule {
  is_open: boolean;
  open_time: string;
  close_time: string;
}
export interface WorkingHours {
  monday?: DaySchedule;
  tuesday?: DaySchedule;
  wednesday?: DaySchedule;
  thursday?: DaySchedule;
  friday?: DaySchedule;
  saturday?: DaySchedule;
  sunday?: DaySchedule;
}
export interface FAQ {
  id?: string;
  question: string;
  answer: string;
}
export interface Service {
  id: string;
  name: string;
  duration_mins: number;
  price?: number;
  required_fields: string[];
}
export interface ModuleField {
  key: string;
  label?: string;
  type: 'string' | 'number' | 'date' | 'time' | 'email' | 'phone' | 'select';
  required: boolean;
  options?: string[];
  options_from?: string;
}
export interface ModuleConfig {
  enabled: boolean;
  fields: ModuleField[];
}
export interface OrderItemConfig {
  id: string;
  name: string;
  description?: string;
  price: number;
  sku?: string;
  enabled?: boolean;
  preparation_minutes?: number;
}
export interface OrderCategoryConfig {
  id: string;
  name: string;
  enabled?: boolean;
  items: OrderItemConfig[];
}
export interface OrderModuleConfig extends ModuleConfig {
  currency?: string;
  fulfillment_types?: string[];
  payment_methods?: string[];
  requires_confirmation?: boolean;
  categories?: OrderCategoryConfig[];
  delivery_fee?: number;
  tax_amount?: number;
}
export interface BookingSettings {
  confirmation_mode?: 'auto_confirm' | 'approval_required';
  allow_customer_reschedule?: boolean;
  allow_customer_cancel?: boolean;
  minimum_notice_minutes?: number;
  advance_booking_days?: number;
  slot_interval_minutes?: number;
  buffer_before_mins?: number;
  buffer_after_mins?: number;
  max_bookings_per_day?: number;
  cancellation_cutoff_minutes?: number;
  reminder_offsets_minutes?: number[];
  approval_required_for?: string[];
}
export interface RemindersModuleConfig extends ModuleConfig {
  whatsapp_templates?: {
    after_booking_completed?: string;
    after_payment_completed?: string;
    after_order_completed?: string;
    after_review_completed?: string;
    inactive_conversation?: string;
    before_booking?: string;
    after_booking?: string;
    before_payment_expiry?: string;
    after_payment_failed?: string;
  };
}

export interface TenantConfig {
  business_name: string;
  business_type: string;
  ai_mode: 'standard' | 'advanced' | 'concierge';
  ai_tone: string;
  timezone: string;
  default_language: string;
  greeting_message: string;
  fallback_message?: string;
  escalation_message?: string;
  working_hours: WorkingHours;
  faqs: FAQ[];
  services: Service[];
  modules: Record<string, ModuleConfig | OrderModuleConfig | RemindersModuleConfig>;
  booking_settings?: BookingSettings;
  escalation_phone?: string;
  escalation_email?: string;
  custom_instructions?: string;
  security?: {
    public_chat_captcha_required?: boolean;
  };
  ignored_numbers?: string[];
  limits?: {
    monthly_credits?: number;
    daily_messages?: number;
    max_ai_tokens_per_month?: number;
  };
}

export type BehaviorField =
  | 'journey_priority'
  | 'greeting_mode'
  | 'clarification_style'
  | 'tone'
  | 'language_policy'
  | 'price_visibility'
  | 'service_detail_policy'
  | 'handoff_policy'
  | 'fallback_policy'
  | 'question_batching'
  | 'menu_policy'
  | 'channel_overrides';

export type ChannelKey = 'public_chat' | 'whatsapp';

export interface BehaviorProfile {
  schema_version: number;
  profile_name: string;
  business_type_key?: string | null;
  journey_priority: string[];
  greeting_mode: string;
  clarification_style: string;
  tone: string;
  language_policy: string;
  price_visibility: string;
  service_detail_policy: string;
  handoff_policy: string;
  fallback_policy: string;
  question_batching: string;
  menu_policy: string;
  channel_overrides?: Partial<Record<ChannelKey, Partial<Record<BehaviorField, string>>>>;
  tenant_editable_fields?: BehaviorField[];
  safe_fallback_mode?: string;
  active_workflow_hint?: string | null;
}

export type BehaviorOverrides = Partial<Record<Exclude<BehaviorField, 'channel_overrides'>, string | string[]>>;

export type ChannelBehaviorOverrides = Partial<Record<ChannelKey, Partial<Record<Exclude<BehaviorField, 'journey_priority' | 'channel_overrides'>, string>>>>;

export interface ReceptionistWorkflowState {
  key: string;
  label: string;
  allowed_by_plan: boolean;
  enabled_by_tenant: boolean;
  runtime_available: boolean;
  selectable_in_journey_priority: boolean;
  reason?: string;
}

export interface ReceptionistBehaviorResponse {
  config_version: number;
  preset: {
    key: string;
    label: string;
    version: number;
    status: string;
  } | null;
  preset_available: boolean;
  inherited_profile: BehaviorProfile;
  overrides: BehaviorOverrides;
  channel_overrides: ChannelBehaviorOverrides;
  effective_profile: BehaviorProfile;
  editable_fields: BehaviorField[];
  locked_fields: Partial<Record<BehaviorField, string>>;
  workflows: ReceptionistWorkflowState[];
  warnings: string[];
}

export interface ReceptionistBehaviorUpdatePayload {
  expected_config_version: number;
  overrides: BehaviorOverrides;
  channel_overrides?: ChannelBehaviorOverrides;
}
