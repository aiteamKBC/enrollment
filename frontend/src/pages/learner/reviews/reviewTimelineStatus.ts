import type { LearnerCalendarEvent } from '@/api/learnerCalendar';
import type { MeetingAttendance } from '@/api/meetingAttendance';

export type TimelineStatus = 'planned' | 'scheduled' | 'in-progress' | 'awaiting-signature' | 'attended' | 'missed';

const normalise = (value?: string | null) => (value || '').trim().toLowerCase().replace(/[ _]+/g, '-');

/** Only the attendance sources can turn a booked meeting green or red. */
export function reviewTimelineStatus(event: LearnerCalendarEvent, attendance?: MeetingAttendance, reviewStatus?: string | null): TimelineStatus {
  if (attendance?.attendanceConfirmed || event.meetingOutcome === 'completed') return 'attended';
  if (attendance?.missed || attendance?.absenceReported || event.meetingOutcome === 'ended') return 'missed';
  const attendanceStatus = normalise(attendance?.status);
  const eventBookingStatus = normalise(event.bookingStatus);
  const eventStatus = normalise(event.status);
  const bookingStatus = attendanceStatus || eventBookingStatus || eventStatus;
  if (['cancelled', 'deleted', 'superseded', 'failed', 'not-scheduled', 'planned', 'unknown'].includes(bookingStatus)) return 'planned';
  const sources = [attendanceStatus, eventBookingStatus, eventStatus, normalise(reviewStatus)];
  // Attendance can lag the calendar row by one refresh. Preserve an explicit
  // in-progress state from either source instead of presenting it as merely
  // scheduled to the learner.
  if (sources.includes('in-progress')) return 'in-progress';
  // A review waiting for its signatures has already taken place. Leaving it in
  // the scheduled tone made it read as a meeting that had not happened yet.
  if (sources.includes('awaiting-signature')) return 'awaiting-signature';
  return event.scheduledDate ? 'scheduled' : 'planned';
}

/** Show a completed review in green even while calendar or attendance copies are stale. */
export function completedReviewTimelineStatus(
  event: LearnerCalendarEvent,
  attendance?: MeetingAttendance,
  reviewStatus?: string | null,
): TimelineStatus {
  if ([event.status, event.bookingStatus, attendance?.status, reviewStatus]
    .some(status => status?.trim().toLowerCase() === 'completed')) return 'attended';
  if ([event.status, event.bookingStatus, attendance?.status, reviewStatus]
    .some(status => normalise(status) === 'in-progress')) return 'in-progress';
  // The signature stage reaches the timeline through the shared reader, so
  // verified attendance evidence still decides the attended and missed tones.
  return reviewTimelineStatus(event, attendance, reviewStatus);
}
