import type { LearnerCalendarEvent, LearnerReviewDefinition } from '@/api/learnerCalendar';
import type { MeetingAttendance } from '@/api/meetingAttendance';
import { meetingJoinUrl } from '../reviews/featuredSession';

export type CoachingGroup = 'needs-action' | 'upcoming' | 'past';
export type CoachingAction = 'sign' | 'join' | 'prepare' | 'schedule' | 'reschedule' | 'view';
export type CoachingSignature = 'learner' | 'others' | 'coach' | 'unknown' | 'none';

export interface CoachingSessionState {
  /** Keep the original identity for opening its form and booking its occurrence. */
  session: LearnerCalendarEvent;
  attendance: MeetingAttendance | undefined;
  group: CoachingGroup;
  statusLabel: string;
  description: string;
  action: CoachingAction;
  actionLabel: string;
  date: string | null;
  booked: boolean;
  joinUrl: string | null;
  needsAction: boolean;
  isToday: boolean;
  signature: CoachingSignature;
}

export interface CoachingOverview {
  current: CoachingSessionState | null;
  needsAction: CoachingSessionState[];
  upcoming: CoachingSessionState[];
  past: CoachingSessionState[];
  all: CoachingSessionState[];
}

export type CoachingReviewDefinitions = Readonly<Record<string, LearnerReviewDefinition | null>>;

function normalize(value: string | undefined): string {
  return (value || '').trim().toLowerCase().replace(/[ _]+/g, '-');
}

function validDate(value: string | null | undefined): string | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : null;
}

/** Day arithmetic uses the server's calendar date, without browser timezone drift. */
function daysFromToday(date: string | null, today: string): number | null {
  if (!date || !validDate(today)) return null;
  return Math.round((Date.parse(`${date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000);
}

function signatureState(session: LearnerCalendarEvent, review?: LearnerReviewDefinition | null): CoachingSignature {
  // An archive is read-only. A calendar flag alone does not tell us whether
  // Curriculum requires the participant's signature on this particular form.
  if (session.importedReview && !session.migratedForm) return 'none';
  if (!review) return normalize(session.status) === 'awaiting-signature' ? 'unknown' : 'none';
  const status = normalize(review.instance?.status);
  const submitted = ['awaiting-signature', 'completed'].includes(status);
  if (!submitted) return 'none';
  if (review.signatures.participant?.required && !review.signatures.participant.signed) return 'learner';
  const pending = Object.entries(review.signatures).filter(([role, state]) => role !== 'participant' && state.required && !state.signed);
  if (pending.length === 1 && pending[0][0] === 'advisor') return 'coach';
  if (pending.length) return 'others';
  return 'none';
}

/** Translate stored meeting and attendance states into one learner-facing step. */
export function coachingSessionState(
  session: LearnerCalendarEvent,
  attendance: MeetingAttendance | undefined,
  today: string,
  review?: LearnerReviewDefinition | null,
): CoachingSessionState {
  const bookingStatus = normalize(attendance?.status || session.bookingStatus || session.status);
  const reviewStatus = normalize(review?.instance?.status);
  const cancelled = ['cancelled', 'deleted', 'superseded'].includes(bookingStatus)
    || normalize(session.status) === 'cancelled';
  const calendarStatus = normalize(session.status);
  const attendanceStatus = normalize(attendance?.status);
  const eventBookingStatus = normalize(session.bookingStatus);
  const status = ['completed', 'awaiting-signature'].includes(reviewStatus)
    ? reviewStatus : ['completed', 'awaiting-signature'].includes(calendarStatus)
      ? calendarStatus : [attendanceStatus, eventBookingStatus, calendarStatus].includes('in-progress')
        ? 'in-progress' : attendanceStatus || eventBookingStatus || calendarStatus;
  const scheduledDate = attendance ? validDate(attendance.date) : validDate(session.scheduledDate);
  const targetDate = validDate(session.targetDate) || validDate(session.date);
  const date = scheduledDate || targetDate;
  const booked = Boolean(scheduledDate) && !cancelled && bookingStatus !== 'failed'
    && ['scheduled', 'in-progress'].includes(status)
    && !['not-scheduled', 'planned', 'unknown'].includes(bookingStatus);
  const isToday = booked && scheduledDate === today;
  const signature = cancelled ? 'none' : signatureState(session, review);
  // Link availability is separate from the next action. A future booking or
  // an absence report must not hide the URL of the existing appointment.
  const joinUrl = booked && !attendance?.attendanceConfirmed
    ? meetingJoinUrl({ ...session, meetingLink: attendance ? attendance.meetingLink : session.meetingLink })
    : null;
  const base = {
    session, attendance, date, booked, isToday, signature,
    joinUrl,
  };
  const result = (
    group: CoachingGroup, statusLabel: string, description: string,
    action: CoachingAction, actionLabel: string,
  ): CoachingSessionState => ({ ...base, group, statusLabel, description, action, actionLabel, needsAction: group === 'needs-action' });

  if (cancelled) return result('past', 'Cancelled', 'This meeting is no longer scheduled.', 'view', 'View details');
  if (signature === 'learner') {
    return result('needs-action', 'Your signature is needed', 'Your coach has submitted the summary. Read it and add your signature.', 'sign', 'Review & sign');
  }
  if (signature === 'coach') return result('past', 'Waiting for your coach', 'Your coach still needs to sign this summary. No action is needed from you.', 'view', 'View summary');
  if (signature === 'others') return result('past', 'Waiting for other signatures', 'Another participant still needs to sign. No action is needed from you.', 'view', 'View summary');
  if (status === 'completed') return result('past', 'Completed', 'Your meeting record is available to revisit.', 'view', 'View summary');
  if (status === 'awaiting-signature') {
    return signature === 'unknown'
      ? result('past', 'Signature pending', 'Open the summary to check which signatures are still needed.', 'view', 'View summary')
      : result('past', 'Summary available', 'You can read the saved meeting summary.', 'view', 'View summary');
  }
  // Confirmation takes precedence over historical absence/missed flags.
  if (attendance?.attendanceConfirmed) {
    return result('past', 'Attended', 'Your attendance is recorded. Your coach will update the meeting summary.', 'view', 'View meeting');
  }
  if (attendance?.absenceReported || attendance?.missed) {
    if (status === 'in-progress') {
      return result(date && date < today ? 'past' : 'upcoming', attendance.absenceReported ? 'Absence reported' : 'Meeting missed',
        'Contact your coach about the next steps for this meeting.', 'view', 'View meeting');
    }
    return result('needs-action', attendance.absenceReported ? 'Absence reported' : 'Meeting missed',
      attendance.absenceReported ? 'Your absence is recorded. Choose another time with your coach.' : 'Choose another time to catch up with your coach.',
      'reschedule', 'Reschedule meeting');
  }
  if (bookingStatus === 'failed') {
    return result('needs-action', 'Booking needs attention', 'The booking was not completed. Open this meeting to check the next step.', 'view', 'View meeting');
  }
  if (booked && scheduledDate && scheduledDate < today) {
    // A past date cannot prove absence if attendance has not loaded or has no
    // matching row. Only the attendance endpoint can mark a meeting missed.
    return result('past', status === 'in-progress' ? 'In Progress' : 'Awaiting update', 'Open this meeting to check the attendance and next steps.', 'view', 'View meeting');
  }
  if (booked) {
    const canJoin = isToday && Boolean(joinUrl);
    return result('upcoming', status === 'in-progress' ? 'In Progress' : isToday ? 'Today' : 'Scheduled',
      isToday ? 'Have your learning updates and questions ready for your coach.' : 'Make a note of your progress and anything you want to discuss.',
      canJoin ? 'join' : 'prepare', canJoin ? 'Join meeting' : 'Prepare for meeting');
  }
  if (['not-scheduled', 'planned'].includes(status)) {
    const remaining = daysFromToday(targetDate, today);
    const due = remaining !== null && remaining <= 7;
    return result(due ? 'needs-action' : 'upcoming', due ? 'Choose a meeting time' : 'Planned',
      due ? remaining < 0 ? 'This planned meeting still needs a time. Arrange it with your coach.' : 'Your planned meeting is coming up. Choose a date and time with your coach.' : 'This meeting is in your programme plan. You can book a time when you are ready.',
      'schedule', 'Book a time');
  }
  return result(date && date < today ? 'past' : 'upcoming', 'Details to be confirmed',
    'Open the meeting to check its details with your coach.', 'view', 'View meeting');
}

function chronological(a: CoachingSessionState, b: CoachingSessionState): number {
  const first = `${a.date || '9999-12-31'}T${a.attendance?.startTime || a.session.scheduledTime || '23:59'}`;
  const second = `${b.date || '9999-12-31'}T${b.attendance?.startTime || b.session.scheduledTime || '23:59'}`;
  return first.localeCompare(second) || a.session.sequence - b.session.sequence || a.session.id.localeCompare(b.session.id);
}

/** Each occurrence belongs to exactly one history tab; urgency is separate from chronology. */
export function coachingOverview(
  sessions: readonly LearnerCalendarEvent[],
  attendance: readonly MeetingAttendance[],
  today: string,
  reviews: CoachingReviewDefinitions = {},
): CoachingOverview {
  const all = sessions.map(session => {
    // Join only by durable identity. Two meetings on one day may have the same title.
    const matched = attendance.find(item => item.id === session.id || (session.reviewId && item.id === `imported-review:${session.reviewId}`))
      || attendance.find(item => Boolean(item.calendarEventKey) && item.calendarEventKey === (session.calendarEventKey || session.eventKey));
    return coachingSessionState(session, matched, today, reviews[session.eventKey] || reviews[session.id]);
  }).sort(chronological);
  const needsAction = all.filter(item => item.needsAction).sort((a, b) =>
    Number(b.action === 'sign') - Number(a.action === 'sign') || chronological(a, b));
  const upcoming = all.filter(item => item.group === 'upcoming');
  const past = all.filter(item => item.group === 'past').sort((a, b) => chronological(b, a));
  // Keep the next appointment in focus while the separate action list retains
  // unfinished signatures and recovery. A missing booking falls back to that work.
  const appointments = all.filter(item => item.booked && item.date && item.date >= today
    && (item.group === 'upcoming' || item.action === 'reschedule'));
  const current = appointments.find(item => item.isToday)
    || appointments[0]
    || needsAction.find(item => item.action !== 'sign')
    || needsAction[0]
    || upcoming[0]
    || null;
  return { current, needsAction, upcoming, past, all };
}
