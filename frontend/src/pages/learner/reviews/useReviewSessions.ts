import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchLearnerDetail, type LearnerDetail } from '@/api/learnerDetail';
import { fetchLearnerCalendarEvents, type BookingCalendarRules, type LearnerCalendarEvent } from '@/api/learnerCalendar';
import { fetchLearnerReviewSessions } from '@/api/reviewSessions';
import { LearnerReadError } from '@/api/learnerRead';
import { useLinkedLearner } from '@/hooks/useMyLearner';
import { useLiveRefresh } from '@/hooks/useRefreshOnReturn';
import { reviewCategory } from '@/lib/reviewCategory';

export type ReviewSessionSource = 'mcr' | 'progress-review' | 'review';

export function isReviewSession(event: LearnerCalendarEvent, source: ReviewSessionSource) {
  // Imported ordinary reviews share the legacy Progress Review booking source.
  // Classify their display by the stored type without changing booking identity.
  const importedReviewType = event.importedReviewType ?? event.importedReview?.type;
  if (importedReviewType != null) {
    return reviewCategory({ ...event, importedReviewType }) === source;
  }
  if (source === 'review') {
    // Native ordinary reviews still require their Curriculum template link.
    return event.source === 'review' && Boolean(event.reviewTemplateId);
  }
  const code = source === 'mcr' ? 'mcm' : 'progress_review';
  return event.reviewTypeCode ? event.reviewTypeCode === code : !event.reviewTemplateId && event.source === source;
}

/** Refresh after either source's local Review lifecycle changes. */
export function isReviewSessionWrite(path: string) {
  return path.includes('/reviews/') || path.includes('/migrated-reviews/');
}

/** The server identifies an LMS overlay by its owned calendar event. A source
 * history row can have the same key prefix but must keep its Aptem rendering. */
export function isMigratedContinuationEvent(event: LearnerCalendarEvent | null): boolean {
  return Boolean(event?.migratedForm && event.eventKey.startsWith('imported-review:'));
}

/** Programme sessions remain usable if the learning summary is unavailable. */
export function useReviewSessions(source: ReviewSessionSource, summary = false) {
  const myLearner = useLinkedLearner();
  const [learner, setLearner] = useState<LearnerDetail | null>(null);
  const [currentCoach, setCurrentCoach] = useState<{ name: string; email: string } | null>(null);
  const [events, setEvents] = useState<LearnerCalendarEvent[]>([]);
  const [bookingCalendar, setBookingCalendar] = useState<BookingCalendarRules | null>(null);
  const [loading, setLoading] = useState(true);
  const [calendarError, setCalendarError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [revision, setRevision] = useState(0);
  const loaded = useRef(false);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  useLiveRefresh(refresh, { match: isReviewSessionWrite });

  useEffect(() => {
    loaded.current = false;
    setLearner(null);
    setCurrentCoach(null);
    setEvents([]);
    setBookingCalendar(null);
    setLoading(true);
  }, [myLearner.kind, myLearner.id, source, summary]);

  useEffect(() => {
    let cancelled = false;
    // Keep the current page mounted during focus/remote-write revalidation.
    // Replacing it with the initial skeleton unmounted children such as the
    // Teams artifacts panel, making every background refresh look like a full
    // page reload and restarting its recording request.
    setCalendarError('');
    setDetailError('');
    setLoading(!loaded.current);
    void fetchLearnerDetail(myLearner.kind, myLearner.id, { revalidate: true })
      .then(detail => { if (!cancelled) setLearner(detail); })
      .catch(() => { if (!cancelled) setDetailError('Could not load the learner summary. Please try again.'); });
    // The server owns source selection and attaches local continuation state.
    // Never add a second history stream or infer a Curriculum fallback here.
    const read = source === 'progress-review'
      ? fetchLearnerReviewSessions(myLearner.kind, myLearner.id, summary)
      : fetchLearnerCalendarEvents(myLearner.kind, myLearner.id, { revalidate: true });
    void read
      .then(calendar => {
        if (cancelled) return;
        loaded.current = true;
        setBookingCalendar(calendar.bookingCalendar || null);
        setCurrentCoach(calendar.currentCoach || null);
        setEvents(calendar.events);
        setLoading(false);
      })
      .catch(error => {
        if (!cancelled) setCalendarError(error instanceof LearnerReadError && error.code === 'timeout'
          ? error.message : 'Could not load programme sessions. Please try again.');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [myLearner.kind, myLearner.id, revision, source, summary]);

  const sessions = useMemo(() => events.filter(event => isReviewSession(event, source))
    .sort((a, b) => (a.date || a.targetDate || '').localeCompare(b.date || b.targetDate || '') || a.sequence - b.sequence || a.eventKey.localeCompare(b.eventKey)), [events, source]);
  const usingImportedReviews = sessions.some(event => event.reviewSource === 'aptem' || event.importedReview);
  return { myLearner, learner, currentCoach, sessions, setEvents, bookingCalendar, loading, error: calendarError || detailError, refresh, usingImportedReviews };
}
