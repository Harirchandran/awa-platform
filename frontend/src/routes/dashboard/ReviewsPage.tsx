import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Loader2, Plus, Star } from 'lucide-react';
import {
  type CreateReviewPayload,
  type ReviewRequest,
  useActionReview,
  useCreateReview,
  useReviewDetail,
  useReviewEvents,
  useReviewSettings,
  useReviewsList,
  useUpdateReviewSettings,
  useDeleteReview,
} from '@/features/reviews/api';
import type { PendingReviewAction, ReviewSettingsForm } from '@/features/reviews/types';
import {
  emptyCreateReviewForm,
  emptyReviewSettingsForm,
  isValidHttpUrl,
  settingsFormPayload,
  settingsSignature,
  settingsToForm,
  toNumber,
  toOptionalText,
} from '@/features/reviews/utils';
import { ReviewMetricsCards } from '@/features/reviews/components/ReviewMetricsCards';
import { ReviewFilters } from '@/features/reviews/components/ReviewFilters';
import { ReviewsList } from '@/features/reviews/components/ReviewsList';
import { ReviewSettingsPanel } from '@/features/reviews/components/ReviewSettingsPanel';
import { LowRatingEscalationPanel } from '@/features/reviews/components/LowRatingEscalationPanel';
import { ReviewRequestDialog } from '@/features/reviews/components/ReviewRequestDialog';
import { ReviewDetailSheet } from '@/features/reviews/components/ReviewDetailSheet';
import { ReviewActionDialog } from '@/features/reviews/components/ReviewActionDialog';
import { ReviewErrorState } from '@/features/reviews/components/ReviewErrorState';
import { useTenantConfig, useUpdateConfig } from '@/features/config/api';
import { useTenantTools } from '@/features/tools/api';
import { getModuleEntitlement } from '@/features/tools/entitlements';
import { getApiErrorMessage } from '@/lib/api-errors';
import type { TenantConfig } from '@/types/config.types';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { TableSkeleton } from '@/components/ui/loading-states';
import { ConfirmActionDialog } from '@/components/shared/ConfirmActionDialog';
import { PageHeader } from '@/components/shared/PageHeader';
import { StickyFormActions } from '@/components/shared/StickyFormActions';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

function ReviewsDisabledState({ config, canEnable, lockedReason }: { config: TenantConfig; canEnable: boolean; lockedReason?: string }) {
  const updateConfig = useUpdateConfig();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const enableReviews = () => {
    const updatedConfig: TenantConfig = {
      ...config,
      modules: {
        ...config.modules,
        reviews: {
          ...(config.modules?.reviews || { fields: [] }),
          enabled: true,
          fields: config.modules?.reviews?.fields || [],
        },
      },
    };

    updateConfig.mutate(updatedConfig, {
      onSuccess: () => toast.success('Review collection enabled'),
      onError: (error: unknown) => toast.error(getApiErrorMessage(error, 'Could not enable reviews')),
    });
  };

  return (
    <div className="flex min-h-[520px] items-center justify-center">
      <Card className="max-w-xl">
        <CardContent className="space-y-5 p-6 text-center sm:p-8">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border bg-background">
            <Star className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-[20px] font-semibold text-foreground">Review collection is turned off</h1>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Enable reviews when staff should ask for private ratings and route happy customers to public review links. Public reviews are requested only after a customer chooses to continue.
            </p>
          </div>
          {canEnable ? (
            <Button type="button" onClick={() => setConfirmOpen(true)} disabled={updateConfig.isPending}>
              {updateConfig.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              Enable Reviews
            </Button>
          ) : (
            <p className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[13px] text-amber-800 dark:text-amber-100">
              {lockedReason || 'Reviews are not available on your current plan.'}
            </p>
          )}
        </CardContent>
      </Card>
      <ConfirmActionDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Enable review collection?"
        description="AWA will be allowed to create and process review requests for this business. Review settings can still be adjusted before using automation."
        confirmLabel="Enable Reviews"
        variant="warning"
        isLoading={updateConfig.isPending}
        onConfirm={() => {
          setConfirmOpen(false);
          enableReviews();
        }}
      />
    </div>
  );
}

export function ReviewsPage() {
  const { data: configData, isLoading: isConfigLoading } = useTenantConfig();
  const { data: toolsData, isLoading: isToolsLoading } = useTenantTools();
  const [statusFilter, setStatusFilter] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [ratingFilter, setRatingFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(emptyCreateReviewForm);
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingReviewAction | null>(null);
  const [actionNote, setActionNote] = useState('');
  const [actionRating, setActionRating] = useState('5');
  const [actionFeedback, setActionFeedback] = useState('');
  const [actionPublicUrl, setActionPublicUrl] = useState('');
  const [settingsDraft, setSettingsDraft] = useState<{ form: ReviewSettingsForm; baseline: string | null } | null>(null);

  const reviewsAccess = getModuleEntitlement(toolsData, 'reviews');
  const reviewsEnabled = reviewsAccess.enabledByTenant;
  const settingsQuery = useReviewSettings();
  const reviewsQuery = useReviewsList(statusFilter, sourceFilter, ratingFilter, search.trim() || undefined);
  const reviews = reviewsQuery.data?.pages.flatMap((page) => page.items) || [];
  const selectedFallback = reviews.find((review) => review.id === selectedReviewId) || null;
  const detailQuery = useReviewDetail(selectedReviewId || undefined);
  const selectedReview = detailQuery.data || selectedFallback;
  const eventsQuery = useReviewEvents(selectedReviewId || undefined);
  const updateSettings = useUpdateReviewSettings();
  const createReview = useCreateReview();
  const actionReview = useActionReview();
  const deleteReview = useDeleteReview();

  const serverSettingsForm = useMemo(
    () => settingsQuery.data ? settingsToForm(settingsQuery.data) : emptyReviewSettingsForm,
    [settingsQuery.data],
  );
  const serverSettingsBaseline = useMemo(
    () => settingsQuery.data ? settingsSignature(serverSettingsForm) : null,
    [serverSettingsForm, settingsQuery.data],
  );
  const settingsForm = settingsDraft?.form ?? serverSettingsForm;
  const settingsBaseline = settingsDraft?.baseline ?? serverSettingsBaseline;
  const isSettingsUrlInvalid = !isValidHttpUrl(settingsForm.public_review_url);
  const isCreateUrlInvalid = !isValidHttpUrl(createForm.public_review_url);
  const isActionUrlInvalid = !isValidHttpUrl(actionPublicUrl);
  const settingsIsDirty = settingsBaseline !== null && settingsSignature(settingsForm) !== settingsBaseline;

  if (isConfigLoading || isToolsLoading || !configData) {
    return <TableSkeleton columns={6} rows={5} />;
  }

  if (!reviewsAccess.allowedByPlan) {
    return <ReviewsDisabledState config={configData} canEnable={false} lockedReason={reviewsAccess.lockedReason} />;
  }

  if (!reviewsEnabled) {
    return <ReviewsDisabledState config={configData} canEnable={reviewsAccess.canEnable} lockedReason={reviewsAccess.lockedReason} />;
  }

  const updateSettingsForm = (patch: Partial<ReviewSettingsForm>) => {
    setSettingsDraft((current) => {
      const form = current?.form ?? serverSettingsForm;
      return {
        form: { ...form, ...patch },
        baseline: current?.baseline ?? serverSettingsBaseline,
      };
    });
  };

  const discardSettingsChanges = () => {
    setSettingsDraft(null);
  };

  const saveSettings = () => {
    if (!settingsIsDirty || isSettingsUrlInvalid) {
      return;
    }
    updateSettings.mutate(
      settingsFormPayload(settingsForm),
      {
        onSuccess: (settings) => {
          const nextForm = settingsToForm(settings);
          setSettingsDraft({ form: nextForm, baseline: settingsSignature(nextForm) });
          toast.success('Review settings saved');
        },
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not save review settings')),
      },
    );
  };

  const saveReview = () => {
    if (!reviewsAccess.canMutateRuntimeData) {
      toast.error('Review requests are unavailable while review collection is disabled.');
      return;
    }
    if (isCreateUrlInvalid) {
      toast.error('Enter a valid URL starting with http:// or https://');
      return;
    }
    if (!createForm.customer_id.trim() && !createForm.conversation_id.trim() && !createForm.booking_id.trim() && !createForm.order_id.trim()) {
      toast.error('Link a customer, conversation, booking, or order');
      return;
    }
    const payload: CreateReviewPayload = {
      customer_id: toOptionalText(createForm.customer_id) || null,
      conversation_id: toOptionalText(createForm.conversation_id) || null,
      booking_id: toOptionalText(createForm.booking_id) || null,
      order_id: toOptionalText(createForm.order_id) || null,
      source: createForm.source,
      status: 'draft',
      public_review_url: toOptionalText(createForm.public_review_url) || null,
      metadata: createForm.note.trim() ? { note: createForm.note.trim() } : {},
    };

    createReview.mutate(payload, {
      onSuccess: () => {
        toast.success('Review request created');
        setCreateForm(emptyCreateReviewForm);
        setCreateOpen(false);
      },
      onError: (error) => toast.error(getApiErrorMessage(error, 'Could not create review request')),
    });
  };

  const openAction = (pending: PendingReviewAction) => {
    if (!reviewsAccess.canMutateRuntimeData) {
      toast.error('Review actions are unavailable while review collection is disabled.');
      return;
    }
    setPendingAction(pending);
    setActionNote('');
    setActionRating(pending.review.rating ? String(pending.review.rating) : '5');
    setActionFeedback(pending.review.feedback_text || '');
    setActionPublicUrl(pending.review.public_review_url || settingsForm.public_review_url || '');
  };

  const confirmAction = () => {
    if (!pendingAction) return;
    const needsPublicUrl = ['submit_rating', 'request_public_review'].includes(pendingAction.action);
    if (needsPublicUrl && isActionUrlInvalid) {
      toast.error('Enter a valid URL starting with http:// or https://');
      return;
    }

    actionReview.mutate(
      {
        id: pendingAction.review.id,
        action: pendingAction.action,
        rating: pendingAction.action === 'submit_rating' ? toNumber(actionRating, 5) : undefined,
        feedback_text: pendingAction.action === 'submit_rating' ? toOptionalText(actionFeedback) : undefined,
        public_review_url: needsPublicUrl ? toOptionalText(actionPublicUrl) : undefined,
        note: toOptionalText(actionNote),
      },
      {
        onSuccess: () => {
          toast.success('Review request updated');
          setPendingAction(null);
        },
        onError: (error) => toast.error(getApiErrorMessage(error, 'Could not update review request')),
      },
    );
  };

  const handleDeleteReview = (id: string) => {
    deleteReview.mutate(id, {
      onSuccess: () => {
        toast.success('Review request deleted');
        setSelectedReviewId(null);
      },
      onError: (error) => toast.error(getApiErrorMessage(error, 'Could not delete review request')),
    });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="Reviews"
        description="Request, collect, and manage customer feedback and public review links."
        actions={(
          <Button type="button" onClick={() => setCreateOpen(true)} disabled={!reviewsAccess.canMutateRuntimeData}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Request Review
          </Button>
        )}
      />

      <Tabs defaultValue="reviews" className="space-y-5">
        <TabsList className="grid h-auto w-full grid-cols-2 gap-2 bg-transparent p-0 md:w-[320px]">
          <TabsTrigger value="reviews" className="justify-start data-[state=active]:bg-primary/10 data-[state=active]:text-primary">Reviews</TabsTrigger>
          <TabsTrigger value="settings" className="justify-start data-[state=active]:bg-primary/10 data-[state=active]:text-primary">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="reviews" className="m-0 space-y-5">
          <ReviewMetricsCards reviews={reviews} />

          <section className="space-y-4" aria-labelledby="review-requests-heading">
            <div className="rounded-md border bg-card p-4">
              <div className="flex flex-col justify-between gap-3 xl:flex-row xl:items-center">
                <div>
                  <h2 id="review-requests-heading" className="text-[15px] font-semibold">Review requests</h2>
                  <p className="mt-1 text-[12px] leading-normal text-muted-foreground">
                    Review private feedback, follow up on low ratings, and route happy customers to a public review link.
                  </p>
                </div>
                <ReviewFilters
                  search={search}
                  onSearchChange={setSearch}
                  statusFilter={statusFilter}
                  onStatusFilterChange={setStatusFilter}
                  sourceFilter={sourceFilter}
                  onSourceFilterChange={setSourceFilter}
                  ratingFilter={ratingFilter}
                  onRatingFilterChange={setRatingFilter}
                  disabled={reviewsQuery.isLoading}
                />
              </div>
            </div>

            {reviewsQuery.isError ? (
              <ReviewErrorState onRetry={() => reviewsQuery.refetch()} />
            ) : (
              <ReviewsList
                reviews={reviews}
                isLoading={reviewsQuery.isLoading}
                isFetching={reviewsQuery.isFetching}
                isError={reviewsQuery.isError}
                onRetry={() => reviewsQuery.refetch()}
                onCreate={() => reviewsAccess.canMutateRuntimeData && setCreateOpen(true)}
                onSelectReview={(review: ReviewRequest) => setSelectedReviewId(review.id)}
                hasNextPage={reviewsQuery.hasNextPage}
                fetchNextPage={reviewsQuery.fetchNextPage}
                isFetchingNextPage={reviewsQuery.isFetchingNextPage}
              />
            )}
          </section>
        </TabsContent>

        <TabsContent value="settings" className="m-0 space-y-5 max-w-3xl">
          <ReviewSettingsPanel
            form={settingsForm}
            onChange={updateSettingsForm}
            isLoading={settingsQuery.isLoading}
            isError={settingsQuery.isError}
            onRetry={() => settingsQuery.refetch()}
            isDirty={settingsIsDirty}
            isSaving={updateSettings.isPending}
            isUrlInvalid={isSettingsUrlInvalid}
            onSave={saveSettings}
          />
          <LowRatingEscalationPanel
            reviews={reviews}
            escalationEnabled={settingsForm.negative_feedback_escalation_enabled}
            positiveThreshold={settingsForm.positive_rating_threshold}
          />
        </TabsContent>
      </Tabs>

      <ReviewRequestDialog
        open={createOpen}
        onOpenChange={(open) => !open && setCreateOpen(false)}
        form={createForm}
        onChange={(patch) => setCreateForm((current) => ({ ...current, ...patch }))}
        onSubmit={saveReview}
        isPending={createReview.isPending}
        isUrlInvalid={isCreateUrlInvalid}
        defaultPublicReviewUrl={settingsForm.public_review_url}
      />

      <ReviewDetailSheet
        open={!!selectedReviewId}
        onOpenChange={(open) => !open && setSelectedReviewId(null)}
        review={selectedReview}
        isLoading={detailQuery.isLoading}
        events={eventsQuery.data}
        eventsLoading={eventsQuery.isLoading}
        onAction={openAction}
        onDelete={reviewsAccess.canMutateRuntimeData ? handleDeleteReview : undefined}
      />

      <ReviewActionDialog
        pendingAction={pendingAction}
        onOpenChange={(open) => !open && setPendingAction(null)}
        actionNote={actionNote}
        onActionNoteChange={setActionNote}
        actionRating={actionRating}
        onActionRatingChange={setActionRating}
        actionFeedback={actionFeedback}
        onActionFeedbackChange={setActionFeedback}
        actionPublicUrl={actionPublicUrl}
        onActionPublicUrlChange={setActionPublicUrl}
        isActionUrlInvalid={isActionUrlInvalid}
        isPending={actionReview.isPending}
        onConfirm={confirmAction}
      />

      <StickyFormActions
        visible={settingsIsDirty}
        isSaving={updateSettings.isPending}
        onSave={saveSettings}
        onDiscard={discardSettingsChanges}
        disabled={isSettingsUrlInvalid}
        message="You have unsaved review setting changes"
        saveLabel="Save Settings"
      />
    </div>
  );
}
