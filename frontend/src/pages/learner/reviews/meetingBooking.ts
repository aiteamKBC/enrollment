import type { ExpectedReviewBooking, LearnerCalendarEvent } from '@/api/learnerCalendar';
import type { MeetingAttendance } from '@/api/meetingAttendance';

export function isAptemReview(session: Partial<LearnerCalendarEvent>) {
  return session.reviewSource === 'aptem' || Boolean(session.importedReview || session.reviewId);
}

/** Prefer the server's canonical booking state; retain compatibility with old payloads. */
export function occupiesBookingSlot(session: Partial<LearnerCalendarEvent>): boolean {
  if (!session.scheduledDate || !session.scheduledTime) return false;
  if (typeof session.bookingReserved === 'boolean') return session.bookingReserved;
  if ((session.bookingStatus || session.status) === 'cancelled' || session.syncState === 'cancelled') return false;
  return ['scheduled', 'in-progress', 'awaiting-signature', 'completed'].includes(session.bookingStatus || session.status || '')
    || ['syncing', 'reconciliation'].includes(session.syncState || '')
    || Boolean(session.calendarEventKey && session.meetingLink);
}

export function aptemBookingSnapshot(session: Partial<LearnerCalendarEvent>): ExpectedReviewBooking | undefined {
  if (!isAptemReview(session) || !session.scheduledDate || !session.scheduledTime) return undefined;
  return {
    scheduledDate: session.scheduledDate,
    scheduledTime: session.scheduledTime,
    durationMinutes: session.durationMinutes || 60,
    notes: session.bookingNotes || '',
  };
}

export function editableBookingNotes(session: Partial<LearnerCalendarEvent>) {
  return isAptemReview(session) ? session.bookingNotes || '' : session.notes || '';
}

/** Preserve the exact meeting identity when explicitly opening its Calendar. */
export function meetingCalendarHref(session: LearnerCalendarEvent, learner: { kind: string; id: string }, action?: 'schedule' | 'reschedule', attendance?: MeetingAttendance): string {
  const params = new URLSearchParams({ kind: learner.kind, learner: learner.id });
  params.set('event', attendance?.calendarEventKey || session.calendarEventKey || session.eventKey || session.id);
  const date = session.scheduledDate || session.targetDate || session.date;
  if (date) params.set('date', date);
  // Viewing an existing appointment uses its durable calendar key. Review/source
  // parameters invoke the separate imported-review scheduling lookup.
  if (action && session.importedReview) {
    params.set('reviewId', session.importedReview.id);
    params.set('source', session.source);
  }
  if (action) params.set('action', action);
  return `/learner/calendar?${params}`;
}

export function meetingBookingWarning(event: LearnerCalendarEvent, responseWarning?: string): string {
  if (responseWarning?.trim()) return responseWarning.trim();
  if (event.syncWarning?.trim()) return event.syncWarning.trim();
  if (event.syncError || (event.invited === false && ['scheduled', 'in-progress'].includes(event.status))) {
    return 'Your meeting time is saved in the LMS calendar, but the Microsoft calendar invitation has not been sent. Contact your programme team to complete calendar sync.';
  }
  return '';
}
