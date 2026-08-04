import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertTriangle, BellRing, Loader2, Plus, Settings } from 'lucide-react';
import {
  type ScheduledMessage,
  type ScheduledMessageAction,
  useActionScheduledMessage,
  useReminderRules,
  useScheduledMessageDetail,
  useScheduledMessageEvents,
  useScheduledMessages,
  useScheduledMessageMetrics,
} from '@/features/reminders/api';
import { useTenantConfig, useUpdateConfig } from '@/features/config/api';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import { useSystemHealth } from '@/features/health/api';
import { getApiErrorMessage } from '@/lib/api-errors';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { TableSkeleton } from '@/components/ui/loading-states';
import { PageHeader } from '@/components/shared/PageHeader';
import type { TenantConfig } from '@/types/config.types';

type ReminderTemplates = {
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

type ReminderModuleWithTemplates = NonNullable<TenantConfig['modules']>[string] & {
  whatsapp_templates?: ReminderTemplates;
};

/* ── feature components ─────────────────────────────────────── */
import { ReminderRulesList } from '@/features/reminders/components/ReminderRulesList';
import { ReminderRuleForm } from '@/features/reminders/components/ReminderRuleForm';
import { ReminderQueueFilters } from '@/features/reminders/components/ReminderQueueFilters';
import { ReminderQueueTable } from '@/features/reminders/components/ReminderQueueTable';
import { ReminderDetailSheet } from '@/features/reminders/components/ReminderDetailSheet';
import { ScheduleMessageDialog } from '@/features/reminders/components/ScheduleMessageDialog';
import {
  MessageActionDialog,
  type PendingAction,
} from '@/features/reminders/components/MessageActionDialog';

/* ── helpers ────────────────────────────────────────────────── */

function toOptionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

function toNumber(value: string, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/* ── sub-component: disabled state ─────────────────────────── */

function RemindersDisabledState({ config, canEnable, lockedReason }: { config: TenantConfig; canEnable: boolean; lockedReason?: string }) {
  const updateConfig = useUpdateConfig();

  const enableReminders = () => {
    const updated: TenantConfig = {
      ...config,
      modules: {
        ...config.modules,
        reminders: {
          ...(config.modules?.reminders || { fields: [] }),
          enabled: true,
          fields: config.modules?.reminders?.fields || [],
        },
      },
    };
    updateConfig.mutate(updated, {
      onSuccess: () => toast.success('Follow-ups enabled'),
      onError: (error: unknown) =>
        toast.error(getApiErrorMessage(error, 'Could not enable follow-ups')),
    });
  };

  return (
    <div className="flex min-h-[520px] items-center justify-center">
      <div className="max-w-xl rounded-md border bg-card p-6 text-center sm:p-8">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
          <BellRing className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
        </div>
        <h2 className="mt-4 text-[18px] font-semibold text-foreground">
          Follow-ups are turned off
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          Enable follow-ups to schedule booking, payment, order, review, or customer nudge
          messages. Delivery depends on the selected channel and worker health.
        </p>
        {canEnable ? (
          <Button
            type="button"
            className="mt-5"
            onClick={enableReminders}
            disabled={updateConfig.isPending}
          >
            {updateConfig.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            Enable Follow-ups
          </Button>
        ) : (
          <p className="mt-5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[13px] text-amber-800 dark:text-amber-100">
            {lockedReason || 'Follow-ups are not available on your current plan.'}
          </p>
        )}
      </div>
    </div>
  );
}

/* ── sub-component: metric card ─────────────────────────────── */

function Metric({
  label,
  value,
  helper,
}: {
  label: string;
  value: number;
  helper?: string;
}) {
  return (
    <div className="rounded-md border bg-card px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-[20px] font-semibold text-foreground">{value}</p>
      {helper && (
        <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{helper}</p>
      )}
    </div>
  );
}

function WhatsAppMetaTemplatesCard({ config }: { config: TenantConfig }) {
  const updateConfig = useUpdateConfig();
  const remindersConfig = config.modules?.reminders as ReminderModuleWithTemplates | undefined;
  const templates = remindersConfig?.whatsapp_templates || {};

  const [bookingTemplate, setBookingTemplate] = useState(templates.after_booking_completed || '');
  const [paymentTemplate, setPaymentTemplate] = useState(templates.after_payment_completed || '');
  const [orderTemplate, setOrderTemplate] = useState(templates.after_order_completed || '');
  const [reviewTemplate, setReviewTemplate] = useState(templates.after_review_completed || '');
  const [nudgeTemplate, setNudgeTemplate] = useState(templates.inactive_conversation || '');
  const [beforeBookingTemplate, setBeforeBookingTemplate] = useState(templates.before_booking || '');
  const [afterBookingTemplate, setAfterBookingTemplate] = useState(templates.after_booking || '');
  const [beforePaymentExpiryTemplate, setBeforePaymentExpiryTemplate] = useState(templates.before_payment_expiry || '');
  const [afterPaymentFailedTemplate, setAfterPaymentFailedTemplate] = useState(templates.after_payment_failed || '');

  const [isSaving, setIsSaving] = useState(false);

  const handleSave = () => {
    setIsSaving(true);
    const updated: TenantConfig = {
      ...config,
      modules: {
        ...config.modules,
        reminders: {
          ...(remindersConfig || { enabled: true, fields: [] }),
          whatsapp_templates: {
            after_booking_completed: bookingTemplate.trim() || undefined,
            after_payment_completed: paymentTemplate.trim() || undefined,
            after_order_completed: orderTemplate.trim() || undefined,
            after_review_completed: reviewTemplate.trim() || undefined,
            inactive_conversation: nudgeTemplate.trim() || undefined,
            before_booking: beforeBookingTemplate.trim() || undefined,
            after_booking: afterBookingTemplate.trim() || undefined,
            before_payment_expiry: beforePaymentExpiryTemplate.trim() || undefined,
            after_payment_failed: afterPaymentFailedTemplate.trim() || undefined,
          },
        },
      },
    };

    updateConfig.mutate(updated, {
      onSuccess: () => {
        toast.success('WhatsApp Meta Templates updated');
        setIsSaving(false);
      },
      onError: (error: unknown) => {
        toast.error(getApiErrorMessage(error, 'Could not save templates'));
        setIsSaving(false);
      },
    });
  };

  return (
    <div className="rounded-md border bg-card">
      <div className="border-b px-4 py-3">
        <h3 className="text-[14px] font-semibold">WhatsApp Meta Templates</h3>
      </div>
      <div className="p-4 space-y-4">
        <div className="flex gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p className="text-[12px] leading-5">
            Make sure each template name exactly matches an approved template in Meta Business
            Manager. Unapproved or mismatched templates may fail to send.
          </p>
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Before booking</label>
          <Input
            value={beforeBookingTemplate}
            onChange={(e) => setBeforeBookingTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">After booking</label>
          <Input
            value={afterBookingTemplate}
            onChange={(e) => setAfterBookingTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Before payment expiry</label>
          <Input
            value={beforePaymentExpiryTemplate}
            onChange={(e) => setBeforePaymentExpiryTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">After payment failed</label>
          <Input
            value={afterPaymentFailedTemplate}
            onChange={(e) => setAfterPaymentFailedTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Booking completed</label>
          <Input
            value={bookingTemplate}
            onChange={(e) => setBookingTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Payment completed</label>
          <Input
            value={paymentTemplate}
            onChange={(e) => setPaymentTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Order completed</label>
          <Input
            value={orderTemplate}
            onChange={(e) => setOrderTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Review requested</label>
          <Input
            value={reviewTemplate}
            onChange={(e) => setReviewTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">Inactive conversation</label>
          <Input
            value={nudgeTemplate}
            onChange={(e) => setNudgeTemplate(e.target.value)}
            placeholder="Meta template ID"
            className="h-8 text-xs"
          />
        </div>
        <Button
          type="button"
          onClick={handleSave}
          disabled={isSaving}
          className="w-full h-8 text-xs mt-2"
        >
          {isSaving && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
          Save Mappings
        </Button>
      </div>
    </div>
  );
}

/* ── main page ──────────────────────────────────────────────── */

/**
 * RemindersPage — orchestrator component for the Follow-ups dashboard section.
 *
 * State ownership:
 *   - Filter state (statusFilter, triggerFilter, channelFilter, search)
 *   - selectedMessageId — drives detail sheet
 *   - pendingAction / actionNote / snoozeMinutes — drive action confirmation dialog
 *   - messageDialogOpen — drives manual schedule dialog
 *
 * All API calls for rules (create/update/delete) are owned by feature sub-components.
 * This page owns: scheduled messages query, detail query, events query, action mutation.
 *
 * Route: /dashboard/reminders (unchanged)
 * Backend: no changes — all endpoints, payloads, query keys preserved.
 */
export function RemindersPage() {
  /* ── config & health ── */
  const { data: configData, isLoading: isConfigLoading } = useTenantConfig();
  const { data: toolsData, isLoading: isToolsLoading } = useTenantTools();
  const healthQuery = useSystemHealth();

  /* ── filter state ── */
  const [statusFilter, setStatusFilter] = useState('all');
  const [triggerFilter, setTriggerFilter] = useState('all');
  const [channelFilter, setChannelFilter] = useState('all');
  const [search, setSearch] = useState('');

  /* ── dialog/sheet state ── */
  const [messageDialogOpen, setMessageDialogOpen] = useState(false);
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);

  /* ── action confirmation state ── */
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [actionNote, setActionNote] = useState('');
  const [snoozeMinutes, setSnoozeMinutes] = useState('60');

  /* ── module gate ── */
  const remindersAccess = getModuleEntitlement(toolsData, 'reminders');
  const remindersEnabled = remindersAccess.enabledByTenant;

  /* ── queries ── */
  const rulesQuery = useReminderRules();
  const scheduledQuery = useScheduledMessages(
    statusFilter,
    triggerFilter,
    channelFilter,
    search.trim() || undefined
  );

  const rules = rulesQuery.data ?? [];
  const messages = useMemo(
    () => scheduledQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [scheduledQuery.data]
  );

  const rulesErrorMessage = rulesQuery.isError
    ? getApiErrorMessage(rulesQuery.error, 'Follow-up rules are unavailable')
    : null;
  const scheduledErrorMessage = scheduledQuery.isError
    ? getApiErrorMessage(scheduledQuery.error, 'Scheduled messages are unavailable')
    : null;

  /* ── detail + events queries (keyed to selectedMessageId) ── */
  const detailQuery = useScheduledMessageDetail(selectedMessageId ?? undefined);
  const eventsQuery = useScheduledMessageEvents(selectedMessageId ?? undefined);
  const selectedFallback = messages.find((m) => m.id === selectedMessageId) ?? null;
  const selectedMessage = detailQuery.data ?? selectedFallback;

  /* ── action mutation ── */
  const actionMessage = useActionScheduledMessage();

  /* ── metrics (global via API) ── */
  const metricsQuery = useScheduledMessageMetrics();
  const metrics = useMemo(() => {
    return {
      scheduled: metricsQuery.data?.scheduled ?? 0,
      due: metricsQuery.data?.due ?? 0,
      failed: metricsQuery.data?.failed ?? 0,
    };
  }, [metricsQuery.data]);

  /* ── worker health ── */
  const workerStatus = healthQuery.data?.services?.worker;
  const isWorkerDegraded =
    healthQuery.isError || (Boolean(workerStatus) && workerStatus !== 'healthy');
  const workerWarningCopy = healthQuery.isError
    ? 'Worker status could not be checked. Scheduled messages are saved, but delivery may be delayed until the worker is confirmed healthy.'
    : 'Scheduled messages are saved, but delivery is delayed until the worker is healthy.';

  /* ── early returns ── */
  if (isConfigLoading || isToolsLoading || !configData) {
    return <TableSkeleton columns={6} rows={5} />;
  }
  if (!remindersAccess.allowedByPlan) {
    return <RemindersDisabledState config={configData} canEnable={false} lockedReason={remindersAccess.lockedReason} />;
  }
  if (!remindersEnabled) {
    return <RemindersDisabledState config={configData} canEnable={remindersAccess.canEnable} lockedReason={remindersAccess.lockedReason} />;
  }

  /* ── action handler ── */
  const openAction = (
    message: ScheduledMessage,
    action: ScheduledMessageAction,
    label: string,
    tone?: 'default' | 'destructive'
  ) => {
    setPendingAction({ message, action, label, tone });
    setActionNote('');
    setSnoozeMinutes('60');
  };

  const confirmAction = () => {
    if (!pendingAction) return;
    actionMessage.mutate(
      {
        id: pendingAction.message.id,
        action: pendingAction.action,
        note: toOptionalText(actionNote),
        snooze_minutes:
          pendingAction.action === 'snooze' ? toNumber(snoozeMinutes, 60) : undefined,
        last_error:
          pendingAction.action === 'mark_failed' ? toOptionalText(actionNote) : undefined,
      },
      {
        onSuccess: () => {
          toast.success('Scheduled message updated');
          setPendingAction(null);
        },
        onError: (error) =>
          toast.error(getApiErrorMessage(error, 'Could not update scheduled message')),
      }
    );
  };

  /* ── render ── */
  return (
    <div className="space-y-5">
      {/* Page header */}
      <PageHeader
        title="Follow-ups"
        description="Set up automated follow-up rules and schedule one-off messages for bookings, payments, orders, reviews, and customer nudges. Delivery depends on the selected channel and worker health."
        actions={
          <Button
            type="button"
            id="schedule-message-btn"
            onClick={() => setMessageDialogOpen(true)}
            disabled={!remindersAccess.canMutateRuntimeData}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Schedule Message
          </Button>
        }
      />

      {/* Metrics row — counts reflect loaded (paginated) messages */}
      <div
        className="grid gap-3 sm:grid-cols-3"
        role="region"
        aria-label="Follow-up message counts (loaded messages only)"
      >
        <Metric label="Scheduled" value={metrics.scheduled} helper="Waiting or snoozed" />
        <Metric
          label="Due Now"
          value={metrics.due}
          helper={isWorkerDegraded ? 'Delivery delayed' : 'Ready for worker/send'}
        />
        <Metric label="Failed" value={metrics.failed} helper="Needs retry or skip" />
      </div>

      {/* Worker degraded alert */}
      {isWorkerDegraded && (
        <div
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-900"
        >
          <div className="flex gap-3">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0"
              aria-hidden="true"
            />
            <div>
              <p className="text-[13px] font-semibold">Follow-up delivery is delayed</p>
              <p className="mt-1 text-[13px] leading-5">{workerWarningCopy}</p>
              {workerStatus && (
                <p className="mt-1 text-[11px] text-amber-800">
                  Worker status: {workerStatus}
                  {typeof healthQuery.data?.worker_lag_seconds === 'number'
                    ? `, last heartbeat ${healthQuery.data.worker_lag_seconds}s ago`
                    : ''}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Two-column: rules panel + scheduled messages queue */}
      <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
        {/* ── Rules aside ── */}
        <aside aria-label="Follow-up rules" className="space-y-3">
          {/* Rules panel header */}
          <div className="rounded-md border bg-card">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <div className="flex items-center gap-2">
                <Settings className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                <h3 className="text-[14px] font-semibold">Rules</h3>
              </div>
              {/* New Rule trigger — sheet managed internally by ReminderRuleForm */}
              <ReminderRuleForm />
            </div>

            <ReminderRulesList
              rules={rules}
              isLoading={rulesQuery.isLoading}
              errorMessage={rulesErrorMessage}
            />
          </div>

          <WhatsAppMetaTemplatesCard config={configData} />
        </aside>

        {/* ── Scheduled messages section ── */}
        <section className="space-y-4" aria-label="Scheduled follow-up messages">
          <ReminderQueueFilters
            search={search}
            onSearchChange={setSearch}
            statusFilter={statusFilter}
            onStatusChange={setStatusFilter}
            triggerFilter={triggerFilter}
            onTriggerChange={setTriggerFilter}
            channelFilter={channelFilter}
            onChannelChange={setChannelFilter}
          />

          <ReminderQueueTable
            messages={messages}
            isLoading={scheduledQuery.isLoading}
            isFetching={scheduledQuery.isFetching}
            errorMessage={scheduledErrorMessage}
            hasNextPage={scheduledQuery.hasNextPage}
            isFetchingNextPage={scheduledQuery.isFetchingNextPage}
            onLoadMore={() => scheduledQuery.fetchNextPage()}
            onSelectMessage={setSelectedMessageId}
            onScheduleMessage={() => setMessageDialogOpen(true)}
          />
        </section>
      </div>

      {/* ── Detail Sheet ── */}
      <ReminderDetailSheet
        open={!!selectedMessageId}
        message={selectedMessage}
        isLoading={detailQuery.isLoading}
        events={eventsQuery.data ?? []}
        eventsLoading={eventsQuery.isLoading}
        onClose={() => setSelectedMessageId(null)}
        onAction={openAction}
      />

      {/* ── Schedule Message Dialog ── */}
      <ScheduleMessageDialog
        open={messageDialogOpen}
        onOpenChange={(isOpen) => setMessageDialogOpen(isOpen)}
        rules={rules}
      />

      {/* ── Action Confirmation Dialog ── */}
      <MessageActionDialog
        pendingAction={pendingAction}
        actionNote={actionNote}
        onNoteChange={setActionNote}
        snoozeMinutes={snoozeMinutes}
        onSnoozeChange={setSnoozeMinutes}
        onConfirm={confirmAction}
        onClose={() => setPendingAction(null)}
        isPending={actionMessage.isPending}
      />
    </div>
  );
}
