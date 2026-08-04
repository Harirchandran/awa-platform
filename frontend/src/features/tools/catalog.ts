import type { ComponentType } from 'react';
import {
  Bell,
  CalendarCheck,
  CircleHelp,
  Headphones,
  Megaphone,
  Star,
  UserRoundCheck,
  Wallet,
  ShoppingBag,
  Wrench,
} from 'lucide-react';
export interface ToolAction {
  label: string;
  href: string;
}
const TOOL_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  bell: Bell,
  booking: CalendarCheck,
  calendar_check: CalendarCheck,
  circle_help: CircleHelp,
  faq: CircleHelp,
  headphones: Headphones,
  human_handoff: UserRoundCheck,
  lead_capture: Megaphone,
  megaphone: Megaphone,
  order: ShoppingBag,
  payments: Wallet,
  reminders: Bell,
  reviews: Star,
  shopping_bag: ShoppingBag,
  star: Star,
  support: Headphones,
  user_round_check: UserRoundCheck,
  wallet: Wallet,
  wrench: Wrench,
};
export const TOOL_ENABLE_PATH = '/dashboard/tools';
export function toolEnablePath(toolKey: string) {
  return `${TOOL_ENABLE_PATH}?tool=${encodeURIComponent(toolKey)}`;
}
export function toolIcon(iconKey?: string | null, toolKey?: string) {
  return TOOL_ICONS[iconKey || ''] || TOOL_ICONS[toolKey || ''] || Wrench;
}
export function safeDashboardHref(href?: string | null) {
  if (!href || !href.startsWith('/dashboard/') || href.startsWith('/dashboard//')) {
    return null;
  }
  return href;
}
