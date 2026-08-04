import { useEffect, useState } from 'react';
import { FormProvider, useForm } from 'react-hook-form';
import toast from 'react-hot-toast';
import { AlertTriangle, CalendarCheck, Loader2, Settings } from 'lucide-react';
import { useBookingsList } from '@/features/bookings/api';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import { BookingsTable } from '@/features/bookings/components/BookingsTable';
import { BookingQuestionsEditor } from '@/features/bookings/components/BookingQuestionsEditor';
import { BookingRulesEditor } from '@/features/bookings/components/BookingRulesEditor';
import { BookingAvailabilityEditor } from '@/features/bookings/components/BookingAvailabilityEditor';
import { BookingAdvancedEditor } from '@/features/bookings/components/BookingAdvancedEditor';
import { BookingFilters } from '@/features/bookings/components/BookingFilters';
import { BookingMetricsCards } from '@/features/bookings/components/BookingMetricsCards';
import { BookingSectionNav } from '@/features/bookings/components/BookingSectionNav';
import type { BookingSection } from '@/features/bookings/constants';
import { ServicesEditor } from '@/features/config/components/ServicesEditor';
import { useTenantConfig, useUpdateConfig } from '@/features/config/api';
import type { TenantConfig } from '@/types/config.types';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { PageHeader } from '@/components/shared/PageHeader';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StickyFormActions } from '@/components/shared/StickyFormActions';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ConfigSkeleton } from '@/components/ui/loading-states';
import { getApiErrorMessage } from '@/lib/api-errors';
import { validateTenantConfig } from '@/lib/tenant-config-validation';

function BookingDisabledState({ config, canEnable, lockedReason }: { config: TenantConfig; canEnable: boolean; lockedReason?: string }) {
  const updateConfig = useUpdateConfig();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const enableBookings = () => {
    const updatedConfig: TenantConfig = {
      ...config,
      modules: {
        ...config.modules,
        booking: {
          enabled: true,
          fields: config.modules?.booking?.fields || [],
        },
      },
    };

    updateConfig.mutate(updatedConfig, {
      onSuccess: () => toast.success('Bookings enabled'),
      onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not enable bookings')),
    });
  };

  return (
    <div className="flex min-h-[520px] items-center justify-center">
      <Card className="max-w-xl">
        <CardContent className="space-y-5 p-6 text-center sm:p-8">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
            <CalendarCheck className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-[20px] font-semibold text-foreground">Bookings are turned off</h1>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Enable bookings when you want customers to book appointments through AWA Chat or WhatsApp. AWA can ask configured questions, collect details, and save booking records for your team.
            </p>
          </div>
          {canEnable ? (
            <Button type="button" onClick={() => setConfirmOpen(true)} disabled={updateConfig.isPending}>
              {updateConfig.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              Enable Bookings
            </Button>
          ) : (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[13px] text-amber-800 dark:text-amber-100">
              {lockedReason || 'Bookings are not available on your current plan.'}
            </p>
          )}
        </CardContent>
      </Card>
      <ConfirmActionDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Enable bookings?"
        description="AWA will be allowed to collect booking details and create appointment records for this business after this change is saved. Review services, questions, availability, and rules before sharing the flow with customers."
        confirmLabel="Enable Bookings"
        variant="warning"
        isLoading={updateConfig.isPending}
        onConfirm={() => {
          setConfirmOpen(false);
          enableBookings();
        }}
      />
    </div>
  );
}

function BookingValidationAlert({ errors }: { errors: string[] }) {
  if (errors.length === 0) return null;

  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div>
          <p className="font-medium">Fix these before saving booking setup</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {errors.slice(0, 6).map((error) => (
              <li key={error}>{error}</li>
            ))}
            {errors.length > 6 && <li>{errors.length - 6} more validation issues.</li>}
          </ul>
        </div>
      </div>
    </div>
  );
}

function BookingOperationsPanel({ bookingEnabled }: { bookingEnabled: boolean }) {
  const [statusFilter, setStatusFilter] = useState('all');
  const [search, setSearch] = useState('');
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    refetch,
    isFetching,
  } = useBookingsList(search || undefined, statusFilter, undefined, undefined, bookingEnabled);
  const bookings = data?.pages.flatMap(page => page.items) || [];

  return (
    <section className="space-y-5" aria-labelledby="booking-operations-heading">
      <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
        <div>
          <h2 id="booking-operations-heading" className="text-[16px] font-semibold text-foreground">Booking operations</h2>
          <p className="mt-1 max-w-2xl text-[13px] leading-normal text-muted-foreground">
            Review appointment records created from AWA Chat or WhatsApp and update booking status after checking the appointment details.
          </p>
        </div>
        <BookingFilters
          search={search}
          onSearchChange={setSearch}
          statusFilter={statusFilter}
          onStatusFilterChange={setStatusFilter}
          disabled={isLoading}
        />
      </div>

      <BookingMetricsCards bookings={bookings} />

      <BookingsTable
        bookings={bookings}
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        refetch={() => refetch()}
        hasNextPage={hasNextPage}
        fetchNextPage={fetchNextPage}
        isFetchingNextPage={isFetchingNextPage}
      />
    </section>
  );
}

export function BookingsPage() {
  const { data: configData, isLoading: isConfigLoading } = useTenantConfig();
  const { data: toolsData, isLoading: isToolsLoading } = useTenantTools();
  const updateConfig = useUpdateConfig();
  const [hasUserEdited, setHasUserEdited] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [pendingConfig, setPendingConfig] = useState<TenantConfig | null>(null);
  const [activeSection, setActiveSection] = useState<BookingSection>('operations');
  const methods = useForm<TenantConfig>({
    defaultValues: configData || {},
  });
  const { reset, handleSubmit, formState: { isDirty, isSubmitting } } = methods;

  useEffect(() => {
    if (configData) {
      reset(configData);
    }
  }, [configData, reset]);

  if (isConfigLoading || isToolsLoading || !configData) {
    return <ConfigSkeleton tabs={6} rows={6} label="Loading booking setup" />;
  }

  const bookingAccess = getModuleEntitlement(toolsData, 'booking');
  const bookingEnabled = bookingAccess.enabledByTenant;

  if (!bookingAccess.allowedByPlan) {
    return <BookingDisabledState config={configData} canEnable={false} lockedReason={bookingAccess.lockedReason} />;
  }

  if (!bookingEnabled) {
    return <BookingDisabledState config={configData} canEnable={bookingAccess.canEnable} lockedReason={bookingAccess.lockedReason} />;
  }

  const saveBookingSetup = (data: TenantConfig) => {
    updateConfig.mutate(data, {
      onSuccess: () => {
        toast.success('Booking setup saved');
        reset(data);
        setHasUserEdited(false);
        setPendingConfig(null);
        setValidationErrors([]);
      },
      onError: (error: unknown) => {
        toast.error(getApiErrorMessage(error, 'Failed to save booking setup'));
      },
    });
  };

  const onSubmit = (data: TenantConfig) => {
    const errors = validateTenantConfig(data);
    if (errors.length > 0) {
      setValidationErrors(errors);
      setPendingConfig(null);
      return;
    }
    setValidationErrors([]);
    setPendingConfig(data);
  };

  const discardBookingChanges = () => {
    reset(configData);
    setHasUserEdited(false);
    setValidationErrors([]);
    setPendingConfig(null);
  };

  return (
    <FormProvider {...methods}>
      <form
        onPointerDownCapture={() => setHasUserEdited(true)}
        onKeyDownCapture={() => setHasUserEdited(true)}
        onSubmit={handleSubmit(onSubmit)}
        className="flex h-full flex-1 flex-col space-y-5"
      >
        <PageHeader
          title="Bookings"
          description="Manage appointment requests, booking settings, availability, and service schedules."
          badge={<StatusBadge label={bookingAccess.runtimeAvailable ? 'Booking tool enabled' : 'Booking setup only'} tone={bookingAccess.runtimeAvailable ? 'success' : 'warning'} icon={Settings} />}
          actions={(
            <>
              <Button type="button" variant="outline" size="sm" onClick={() => setActiveSection('availability')}>
                Configure Availability
              </Button>
              {isDirty && hasUserEdited && (
                <Button type="submit" size="sm" disabled={isSubmitting || updateConfig.isPending}>
                  Review & Save
                </Button>
              )}
            </>
          )}
        />

        <BookingValidationAlert errors={validationErrors} />

        <BookingSectionNav activeSection={activeSection} onSectionChange={setActiveSection} />

        <div className="rounded-md border bg-card/40 p-4 sm:p-5">
          {activeSection === 'operations' && <BookingOperationsPanel bookingEnabled={bookingAccess.canMutateRuntimeData} />}
          {activeSection === 'services' && <ServicesEditor />}
          {activeSection === 'availability' && <BookingAvailabilityEditor />}
          {activeSection === 'questions' && <BookingQuestionsEditor />}
          {activeSection === 'rules' && <BookingRulesEditor />}
          {activeSection === 'advanced' && <BookingAdvancedEditor />}
        </div>

        <StickyFormActions
          visible={isDirty && hasUserEdited}
          isSaving={updateConfig.isPending}
          onSave={handleSubmit(onSubmit)}
          onDiscard={discardBookingChanges}
          disabled={isSubmitting}
          message="You have unsaved booking setup changes"
          saveLabel="Review & Save"
        />
      </form>

      <ConfirmActionDialog
        open={!!pendingConfig}
        onOpenChange={(open) => !open && setPendingConfig(null)}
        title="Save booking setup?"
        description="This updates the questions, services, availability, and booking rules AWA uses for customer appointments."
        confirmLabel="Save Booking Setup"
        isLoading={updateConfig.isPending}
        onConfirm={() => pendingConfig && saveBookingSetup(pendingConfig)}
      />
    </FormProvider>
  );
}
