import type { LearnerCalendarEvent, LearnerReviewDefinition } from '@/api/learnerCalendar';
import type { MeetingAttendance } from '@/api/meetingAttendance';
import { featuredSession, meetingJoinUrl, meetingStatus } from '../reviews/featuredSession';

export type ReviewFilter = 'all' | 'upcoming' | 'past';
export type ReviewDefinitions = Readonly<Record<string, LearnerReviewDefinition | null>>;
export interface ReviewPresentation {
  session: LearnerCalendarEvent;
  attendance?: MeetingAttendance;
  status: string;
  date: string | null;
  booked: boolean;
  isToday: boolean;
  past: boolean;
  needsAttention: boolean;
  label: string;
  description: string;
  action: 'schedule' | 'join' | 'sign' | 'view';
  joinUrl: string | null;
  canReschedule: boolean;
}

/**
 * The vocabulary the review detail header already shows for every review
 * (``statusLabel`` in progress-reviews/page.tsx). An imported row stays read-only,
 * but naming where its data came from instead of what state it is in contradicted
 * both that header and the programme timeline.
 */
const importedStatusLabels: Readonly<Record<string, string>> = {
  'not-scheduled': 'Not scheduled', planned: 'Not scheduled', scheduled: 'Scheduled', 'in-progress': 'In progress',
  'awaiting-signature': 'Awaiting signatures', completed: 'Completed', cancelled: 'Cancelled',
};

export const isAptemReview = (session: LearnerCalendarEvent) => session.reviewSource === 'aptem' || Boolean(session.importedReview);

/** Display state only. Keep the original event for all existing action handlers. */
export function presentReview(session: LearnerCalendarEvent, attendance: MeetingAttendance | undefined, today: string, definition?: LearnerReviewDefinition | null, viewerRole: 'participant' | 'employer' = 'participant'): ReviewPresentation {
  const storedStatus = meetingStatus(session);
  const bookingStatus = (attendance?.status || session.bookingStatus || storedStatus).trim().toLowerCase().replace(/[ _]+/g, '-');
  const submittedStatus = definition?.instance?.status;
  // apply_imported_state already resolves source completion, the local form and
  // booking. A separate calendar/attendance copy must not demote that lifecycle.
  const effectiveStatus = isAptemReview(session) ? session.effectiveLmsStatus?.trim().toLowerCase().replace(/[ _]+/g, '-') : undefined;
  const cancelled = effectiveStatus ? effectiveStatus === 'cancelled'
    : storedStatus === 'cancelled' || ['cancelled', 'deleted', 'superseded'].includes(bookingStatus);
  const status = effectiveStatus || (cancelled ? 'cancelled' : ['awaiting-signature', 'completed'].includes(submittedStatus || '') ? submittedStatus!
    : ['awaiting-signature', 'completed'].includes(storedStatus) ? storedStatus : bookingStatus);
  const scheduledDate = attendance ? attendance.date : session.scheduledDate;
  const date = scheduledDate || session.targetDate || session.date || null;
  // Aptem's planned date is not a local appointment. A durable key distinguishes
  // rescheduling from a first booking; completed source history stays read-only.
  const imported = session.importedReview;
  const importedStatus = imported?.status.trim().toLowerCase().replace(/[ _]+/g, '-');
  const importedProgressReview = imported && ['progress review', 'progress review (+ skills radar)'].includes(imported.type.trim().toLowerCase());
  const hasLocalBooking = Boolean(session.calendarEventKey || attendance?.calendarEventKey);
  const canBookImported = importedProgressReview && !hasLocalBooking && !imported.completedDate
    && !attendance?.attendanceConfirmed && ['not-scheduled', 'scheduled'].includes(storedStatus)
    && ![status, storedStatus, bookingStatus, submittedStatus, importedStatus, session.sourceStatus]
      .some(value => ['completed', 'awaiting-signature', 'in-progress', 'cancelled', 'deleted', 'superseded']
        .includes((value || '').trim().toLowerCase().replace(/[ _]+/g, '-')));
  const importedBooking = Boolean(imported && (session.calendarEventKey || attendance?.calendarEventKey)
    && !imported.completedDate && !['completed', 'awaiting-signature', 'cancelled'].includes(importedStatus || '')
    && ['progress review', 'progress review (+ skills radar)', 'personal support plan'].includes(imported.type.trim().toLowerCase()));
  const booked = (!imported || importedBooking) && Boolean(scheduledDate) && ['scheduled', 'in-progress'].includes(status)
    && !['not-scheduled', 'planned', 'failed', 'unknown'].includes(bookingStatus);
  const isToday = booked && scheduledDate === today;
  const joinUrl = booked ? meetingJoinUrl({ ...session, meetingLink: attendance ? attendance.meetingLink : session.meetingLink }) : null;
  const base = { session, attendance, status, date, booked, isToday, joinUrl,
    // A coach-booked PSP can be joined here; learner booking remains limited to PR.
    canReschedule: booked && (!imported || Boolean(importedProgressReview)) && status === 'scheduled' && !attendance?.attendanceConfirmed
      && ![storedStatus, submittedStatus, session.bookingStatus, importedStatus].includes('in-progress') };
  const result = (label: string, description: string, action: ReviewPresentation['action'], past = false, needsAttention = false): ReviewPresentation =>
    ({ ...base, label, description, action, past, needsAttention });
  // importedReview is source data for future occurrences too; its presence is
  // not evidence that the review has happened or that a form is initialized.
  const importedPending = Boolean(imported && !imported.completedDate
    && ['not-scheduled', 'planned', 'scheduled', 'in-progress'].includes(status));

  if (cancelled) return result('Cancelled', 'This review was cancelled. Its record is still available.', 'view', true);
  if (viewerRole === 'employer') {
    const signature = definition?.signatures.employer;
    const required = signature?.required ?? session.employerSignatureRequired;
    const signed = signature?.signed ?? session.employerSigned;
    if (['awaiting-signature', 'completed'].includes(status) && required && !signed) {
      return result('Your signature is needed', 'Read the review and agreed next steps, then sign as employer.', 'sign', true, true);
    }
    if (signed) return result('Your signature is saved', 'Your employer signature is saved. You can read the review and check the other signatures.', 'view', true);
    if (session.importedReview && !session.migratedForm && !booked) return result(importedStatusLabels[status] || 'Review record', 'Read this saved review and its attachments.', 'view', !importedPending);
    if (['awaiting-signature', 'completed'].includes(status)) return result(status === 'completed' ? 'Completed' : 'Waiting for signatures', 'Read the review and check its signature status.', 'view', true);
    if (booked) return result(isToday ? 'Today' : status === 'in-progress' ? 'In progress' : 'Scheduled', 'Review the learner’s progress and agreed next steps.', isToday && joinUrl ? 'join' : 'view', Boolean(date && date < today));
    return result(['not-scheduled', 'planned'].includes(status) ? 'Not scheduled' : 'In preparation', 'The coach is preparing this review. You can read the available details and answer any employer questions.', 'view');
  }
  if (canBookImported) return result('Not scheduled', 'Choose a date and time with your coach. The target date is not a confirmed appointment.', 'schedule', false, Boolean(date && date < today));
  if (session.importedReview && !booked) return result(importedStatusLabels[status] || 'Review record', 'You can read this saved review and its attachments.', 'view', !importedPending);
  if (['awaiting-signature', 'completed'].includes(status)) {
    // Match the existing detail form's signature eligibility. A calendar status
    // alone cannot establish whose signature is required by a Curriculum form.
    const instanceBacked = Boolean(session.reviewTemplateId || session.reviewInstanceId);
    const submitted = ['awaiting-signature', 'completed'].includes(definition?.instance?.status || '');
    const learnerNeedsToSign = instanceBacked
      ? submitted && definition?.signatures.participant?.required && !definition.signatures.participant.signed
      : !session.learnerSigned;
    if (learnerNeedsToSign) return result('Your signature is needed', "Read your coach's notes and the agreed next steps, then sign the review.", 'sign', true, true);
    if (definition && submitted) {
      const pending = Object.values(definition.signatures).some(item => item.required && !item.signed);
      if (pending) return result('Waiting for other signatures', definition.signatures.participant?.signed
        ? 'Your signature is saved. No further signature is needed from you.' : 'Other participants still need to sign. No signature is needed from you.', 'view', true);
    }
    if (status === 'awaiting-signature' && !definition) return result('Check review signatures', 'Open the review to see which signatures are still needed.', 'view', true, true);
    return result(status === 'completed' ? 'Completed' : 'Review available', 'You can read your review and the agreed next steps.', 'view', true);
  }
  if (attendance?.attendanceConfirmed) return result('Attendance recorded', 'Your attendance is saved. Open the review to check the latest notes.', 'view', Boolean(date && date < today));
  if (attendance?.missed || attendance?.absenceReported) return result(attendance.absenceReported ? 'Absence reported' : 'Meeting missed', 'Open the meeting options to check your next step with your coach.', 'view', Boolean(date && date < today), true);
  if (booked && date && date < today) return result('Awaiting update', 'Open this review to check its attendance and next steps.', 'view', true, true);
  if (booked) return result(isToday ? 'Today' : status === 'in-progress' ? 'In progress' : 'Scheduled',
    'Have your learning updates and questions ready for your coach.', isToday && joinUrl ? 'join' : 'view');
  if (['not-scheduled', 'planned'].includes(status)) return result('Not scheduled', 'Choose a date and time with your coach. The target date is not a confirmed appointment.', 'schedule', false, Boolean(date && date < today));
  return result('Check review details', 'Open the review to check its current details.', 'view', Boolean(date && date < today));
}

/**
 * Programme order is the planned date each review carries from its source, which
 * is also the date every row displays and the order the source numbers the plan in
 * (coach_api/review_sources.py number_imported_events). The reader upstream orders
 * by `date` instead, and an imported review's `date` is its completed date once it
 * has been completed, so a review completed away from its plan sorted into a
 * position its own displayed date contradicted. Monthly coaching already orders its
 * timeline this way, and featuredSession already picks the next review by planned
 * date; this keeps the reviews list, its timeline and its filters on that same key.
 */
function byPlannedDate(a: ReviewPresentation, b: ReviewPresentation) {
  const first = a.session.targetDate || '9999-12-31';
  const second = b.session.targetDate || '9999-12-31';
  // Reviews that share a planned date and an occurrence keep the order the reader
  // gave them, which already ends in a stable event-key comparison. An id is an
  // opaque booking key, so ranking a tie by it would order the programme on
  // something the learner cannot see.
  return first.localeCompare(second)
    || (a.session.occurrenceNumber ?? a.session.sequence) - (b.session.occurrenceNumber ?? b.session.sequence);
}

export function reviewOverview(sessions: readonly LearnerCalendarEvent[], attendance: readonly MeetingAttendance[], today: string, definitions: ReviewDefinitions = {}, viewerRole: 'participant' | 'employer' = 'participant') {
  const all = sessions.map(session => presentReview(session,
    attendance.find(item => item.id === session.id || (session.reviewId && item.id === `imported-review:${session.reviewId}`))
      || attendance.find(item => Boolean(item.calendarEventKey) && item.calendarEventKey === (session.calendarEventKey || session.eventKey)),
    today, definitions[session.eventKey] || definitions[session.id], viewerRole)).sort(byPlannedDate);
  // Reuse the existing featured-meeting ordering; never alter the programme
  // dates, persisted statuses, booking rules or the input event objects.
  const featured = featuredSession(all.filter(item => !item.past)
    .map(item => ({ ...item.session, status: item.status, scheduledDate: item.booked ? item.date : null,
      scheduledTime: item.attendance?.startTime ?? item.session.scheduledTime })), today);
  const attention = all.filter(item => item.needsAttention);
  const nextActionableReview = all.find(item => item.session.id === featured?.id) || attention[0] || null;
  // Keep Native's historical-card fallback. Imported history remains selectable
  // in the timeline/list, but it is not a next action when nothing is pending.
  const current = nextActionableReview
    || (viewerRole === 'employer' ? all.filter(item => !isAptemReview(item.session) && item.status !== 'cancelled').at(-1) : null) || null;
  return { all, attention, current, nextActionableReview, upcoming: all.filter(item => !item.past), past: all.filter(item => item.past).reverse() };
}

/** Only carry recognised presentation parameters; identity always comes from the resolved learner. */
export function reviewsListHref(learner: { kind: string; id: string }, params: URLSearchParams, basePath = '/learner/progress-reviews') {
  const next = new URLSearchParams({ kind: learner.kind, learner: learner.id });
  if (params.get('view') === 'all') {
    next.set('view', 'all');
    const filter = params.get('filter');
    if (filter === 'all' || filter === 'upcoming' || filter === 'past') next.set('filter', filter);
    const page = params.get('page');
    if (page && /^\d+$/.test(page) && Number.isSafeInteger(Number(page)) && Number(page) > 0) next.set('page', page);
  }
  return `${basePath}?${next}`;
}
