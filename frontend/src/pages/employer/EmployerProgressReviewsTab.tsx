import { useCallback, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Video } from 'lucide-react';
import type { LearnerKind } from '@/api/learnerDetail';
import {
  downloadEmployerProgressReview, employerMeetingArtifactContentUrl, fetchEmployerMeetingArtifacts, fetchEmployerProgressReview,
  fetchEmployerProgressReviews, saveEmployerReviewAnswers, signReviewAsEmployer,
} from '@/api/employerPortal';
import {
  downloadMigratedReviewForParty, fetchMigratedReviewForParty, signMigratedReviewAsParty, saveMigratedReviewPartyAnswers,
  type LearnerReviewDefinition,
} from '@/api/learnerCalendar';
import { CoachMeetingArtifactsPanel } from '@/pages/coach/shared/CoachMeetingArtifactsPanel';
import ReviewsHome from '@/pages/learner/progress-reviews/ReviewsHome';
import { presentReview } from '@/pages/learner/progress-reviews/reviewPresentation';
import { ImportedReviewSections } from '@/pages/learner/progress-reviews/page';
import { LearnerReviewInstanceForm } from '@/pages/learner/reviews/LearnerReviewInstanceForm';
import styles from '@/pages/learner/progress-reviews/reviewsHome.module.css';

type Props = {
  employerId: string; kind: LearnerKind; learnerId: string; employerName: string;
  onReviewChanged: () => void | Promise<void>;
};

export function EmployerProgressReviewsTab({ employerId, kind, learnerId, employerName, onReviewChanged }: Props) {
  const client = useQueryClient();
  const listKey = ['employer-progress-reviews', employerId, kind, learnerId];
  const [params, setParams] = useSearchParams();
  const selectedKey = params.get('reviewId');
  const list = useQuery({
    queryKey: listKey,
    queryFn: ({ signal }) => fetchEmployerProgressReviews(employerId, kind, learnerId, signal),
  });
  const selectedEventIsKnown = Boolean(selectedKey && list.data?.events.some(event => event.eventKey === selectedKey));
  useEffect(() => {
    if (!selectedKey || list.isPending || list.isError || selectedEventIsKnown) return;
    const next = new URLSearchParams(params);
    next.delete('reviewId');
    setParams(next, { replace: true });
  }, [selectedKey, list.isPending, list.isError, selectedEventIsKnown, params, setParams]);
  const detail = useQuery({
    queryKey: [...listKey, selectedKey], enabled: selectedEventIsKnown,
    queryFn: async ({ signal }) => {
      const value = await fetchEmployerProgressReview(employerId, kind, learnerId, selectedKey!, signal);
      return value.event.migratedForm
        ? { ...value, definition: await fetchMigratedReviewForParty(value.event.eventKey, signal) }
        : value;
    },
  });
  const review = detail.data?.event;
  const definition = detail.data?.definition;
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/London' });
  const meeting = review ? presentReview(review, undefined, today, definition, 'employer') : null;
  // APTEM archives belong to the source-qualified Review's linked booking,
  // independently of its current invitation/schedule. The resource API checks
  // ownership and association again and reports whether a saved copy exists.
  const recordingKey = review?.reviewSource === 'aptem'
    ? (review.calendarEventKey ? review.eventKey : '')
    : review && review.invited && review.scheduledDate && review.scheduledDate <= today
      ? review.calendarEventKey || review.eventKey || review.id : '';
  const loadArtifacts = useCallback((key: string, signal?: AbortSignal) =>
    fetchEmployerMeetingArtifacts(employerId, kind, learnerId, key, signal), [employerId, kind, learnerId]);
  const artifactContentUrl = useCallback((key: string, artifactType: string, artifactId: string, options: { preview?: boolean } = {}) =>
    employerMeetingArtifactContentUrl(employerId, kind, learnerId, key, artifactType, artifactId, options), [employerId, kind, learnerId]);
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: listKey }),
      Promise.resolve(onReviewChanged()),
    ]);
  };
  const saveAnswers = async (answers: Record<string, unknown>, answerVersion?: string): Promise<LearnerReviewDefinition> => {
    if (!review || !definition?.instance) throw new Error('This review is not ready for employer answers.');
    if (review.migratedForm) {
      const updated = await saveMigratedReviewPartyAnswers(review.eventKey, answers, answerVersion);
      client.setQueryData([...listKey, selectedKey], { ...detail.data, definition: updated });
      await client.invalidateQueries({ queryKey: listKey, exact: true });
      return updated;
    }
    await saveEmployerReviewAnswers(employerId, kind, learnerId, definition.instance.id, answers);
    const updated = await detail.refetch();
    if (updated.error) throw updated.error;
    if (!updated.data?.definition) throw new Error('Could not refresh the review. Please try again.');
    await client.invalidateQueries({ queryKey: listKey, exact: true });
    return updated.data.definition;
  };
  const sign = async (signature: string) => {
    if (!review || !definition) throw new Error('This review is not ready for signing.');
    if (review.migratedForm) await signMigratedReviewAsParty(review.eventKey, signature, { reviewRevision: definition.reviewRevision });
    else if (definition.instance) await signReviewAsEmployer(employerId, kind, learnerId, definition.instance.id, { name: employerName, signature });
    else throw new Error('The coach must prepare this review before you can sign.');
    await refresh();
  };
  const errorMessage = (error: Error | null) => error?.message || 'Could not load progress reviews. Please try again.';
  const closeReview = () => {
    const next = new URLSearchParams(params);
    next.delete('reviewId');
    setParams(next, { replace: true });
  };
  const openReview = (eventKey: string) => {
    const next = new URLSearchParams(params);
    next.set('tab', 'reviews');
    next.set('reviewId', eventKey);
    setParams(next);
  };

  if (selectedKey && (selectedEventIsKnown || list.isPending)) return <section className={`${styles.home} space-y-4`} aria-label="Employer progress review">
    <button type="button" className={styles.secondaryButton} onClick={closeReview}><ArrowLeft size={19} aria-hidden="true" />Back to reviews</button>
    {!detail.isPending && !detail.isError && meeting?.joinUrl && !meeting.past && <a className={styles.primaryButton} href={meeting.joinUrl} target="_blank" rel="noopener noreferrer"><Video size={19} aria-hidden="true" />Join meeting</a>}
    {!detail.isPending && !detail.isError && recordingKey && review && <CoachMeetingArtifactsPanel
      event={{ ...review, eventKey: recordingKey }} fetchArtifacts={loadArtifacts} contentUrl={artifactContentUrl}
      storedOnly={review.reviewSource === 'aptem'}
      hasTeamsMeeting showAttendance={false} showMeetingSummary={false} canCheck={false}
      visibleArtifactTypes={['recording']} className="border-primary-100 bg-primary-50/30" />}
    {detail.isPending ? <p role="status" className={styles.loading}>Loading review…</p>
      : detail.isError ? <div role="alert" className={styles.empty}><p>{errorMessage(detail.error)}</p><button type="button" className={styles.secondaryButton} onClick={() => void detail.refetch()}>Try again</button></div>
      : definition ? <LearnerReviewInstanceForm key={review?.eventKey} definition={definition} viewerRole="employer" signatoryName={employerName}
        onSaveAnswers={definition.instance ? saveAnswers : undefined}
        onSign={sign} onDownload={review ? () => review.migratedForm
          ? downloadMigratedReviewForParty(review.eventKey)
          : downloadEmployerProgressReview(employerId, kind, learnerId, review.eventKey) : undefined} />
      : review?.importedReview ? <article className="space-y-4"><h2 className="text-lg font-semibold">{review.title}</h2><ImportedReviewSections review={review.importedReview} /></article>
      : <article className={styles.current}><h2>{review?.title || 'Progress review'}</h2><p className={styles.help}>{review?.notes || 'The coach is preparing this review. Its form will appear here when available.'}</p></article>}
  </section>;

  return <div className="space-y-4">
    {list.isError && <div role="alert" className={styles.home}><p>{errorMessage(list.error)}</p><button type="button" className={styles.secondaryButton} onClick={() => void list.refetch()}>Try again</button></div>}
    <ReviewsHome sessions={list.data?.events || []} definitions={list.data?.definitions} attendance={[]}
      learner={{ kind, id: learnerId }} lineManager={employerName} today={today}
      timeZone="Europe/London" loading={list.isPending} error={list.isError ? errorMessage(list.error) : ''}
      busy={false} canAct viewerRole="employer" titleOf={event => `${event.title}${event.occurrenceNumber != null ? ` #${event.occurrenceNumber}` : ''}`}
      onOpenReview={event => openReview(event.eventKey)} onSchedule={() => undefined} onAttend={() => undefined} onReport={() => undefined} />
  </div>;
}
