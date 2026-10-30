import { fetchSharedJsonGet } from '@/lib/sharedGetJson';
import { coachFetch } from '@/lib/coachFetch';
import { reviewCategory } from '@/lib/reviewCategory';
import { withCoachViewAs } from '@/lib/coachViewAs';

interface CoachCalendarFetchOptions {
  start?: string;
  end?: string;
  includeLiveSessions?: boolean;
  includeSchedulerQueues?: boolean;
}

function coachTimetableEndpoint(options: CoachCalendarFetchOptions = {}) {
  const params = new URLSearchParams();
  if (options.start) params.set('start', options.start);
  if (options.end) params.set('end', options.end);
  if (options.includeLiveSessions === false) params.set('include_live_sessions', '0');
  if (options.includeSchedulerQueues === false) params.set('include_scheduler_queues', '0');
  const query = params.toString();
  return `/coach_api/coach/timetable${query ? `?${query}` : ''}`;
}
const SCHEDULE_ENDPOINT = '/coach_api/coach/timetable/events/schedule';
const ACTION_ENDPOINT = '/coach_api/coach/timetable/events/action';
const BOOK_ENDPOINT = '/coach_api/coach/timetable/events/book';

export type CoachCalendarStatus =
  | 'completed'
  | 'scheduled'
  | 'in-progress'
  | 'awaiting-signature'
  | 'confirmed'
  | 'pending'
  | 'cancelled'
  | 'not-scheduled';

export interface CoachCalendarEvent {
  id: string;
  eventKey?: string;
  title: string;
  type: 'coaching' | 'review' | string;
  date?: string;
  year?: number;
  month?: number;
  dayOfMonth?: number;
  startHour?: number;
  endHour?: number;
  timeLabel?: string;
  /** True when startHour is a generated placeholder (e.g. an unbooked MCR
   *  slot), not a real scheduled time -- see sortEvents. */
  isTimeEstimated?: boolean;
  learner?: string;
  email?: string;
  programme?: string;
  cohort?: string;
  group?: string;
  module?: string;
  tutor?: string;
  schedule?: string;
  priority?: 'normal' | 'urgent' | 'high';
  status: CoachCalendarStatus;
  source?: 'mcr' | 'progress-review' | 'catch-up' | string;
  sequence?: number;
  notes?: string;
  learnerId?: string;
  learnerType?: 'commercial' | 'apprenticeship' | null;
  enrolmentId?: string | null;
  ownerEmail?: string;
  ownerName?: string;
  targetDate?: string;
  scheduledDate?: string | null;
  scheduledTime?: string | null;
  durationMinutes?: number;
  meetingProvider?: string;
  meetingLink?: string;
  graphWebLink?: string;
  platform?: string;
  location?: string;
  syncWarning?: string;
  operationId?: string;
  syncState?: 'pending' | 'syncing' | 'synced' | 'failed' | 'reconciliation' | 'cancelled';
  syncAttemptCount?: number;
  reviewResponses?: Record<string, string>;
  reviewCompletedAt?: string | null;
  reviewSource?: 'aptem' | 'curriculum' | string;
  aptemReviewId?: string | null;
  reviewId?: string;
  importedReviewType?: string;
  rawStatus?: string;
  sourceStatus?: string;
  effectiveLmsStatus?: string;
  calendarEventId?: string;
  calendarEventKey?: string | null;
  reconciledReview?: boolean;
  formEventKey?: string;
  reviewerName?: string | null;
  hasReviewForm?: boolean;
  hasTranscript?: boolean;
  hasAttendance?: boolean;
  managerSignedAt?: string | null;
  managerSignedBy?: string;
  /** The Curriculum review_templates.id this occurrence was generated from. */
  reviewTemplateId?: string | null;
  reviewTypeId?: string | null;
  reviewTypeCode?: string | null;
  reviewTypeName?: string | null;
  reviewTypeIsSystem?: boolean;
  /** Set once this occurrence has been scheduled at least once -- its
   *  presence is what routes "open review" to the dynamic Curriculum-driven
   *  form (see reviewInstances.ts) instead of any hard-coded one. */
  reviewInstanceId?: string | null;
  occurrenceNumber?: number | null;
}

export interface CoachReviewGenerationIssue {
  learnerId: string;
  code:
    | 'missing_curriculum_programme'
    | 'no_enabled_review_templates'
    | 'missing_learner_enrolment'
    | 'missing_learner_start_date'
    | 'invalid_learner_start_date'
    | 'review_schedule_unavailable'
    | string;
}

interface CoachTimetableResponse {
  owner?: {
    name?: string;
    email?: string;
  };
  events?: CoachCalendarEvent[];
  reviewGenerationIssues?: CoachReviewGenerationIssue[];
}

export interface ScheduleFormState {
  date: string;
  time: string;
  durationMinutes: number;
}

export interface CoachCalendarBookingInput {
  learnerId: string;
  sessionType: string;
  scheduledDate: string;
  scheduledTime: string;
  durationMinutes: number;
  timezoneOffsetMinutes: number;
  notes: string;
  /** The missed-lecture report a new coach catch-up must make up. */
  absenceReportId?: string;
}

export type CalendarAction = 'start' | 'complete' | 'sign' | 'cancel';

export interface CoachMeetingArtifact {
  id: string;
  artifact_type: 'transcript' | 'recording' | string;
  graph_artifact_id?: string;
  call_id?: string;
  content_correlation_id?: string;
  created_datetime?: string;
  end_datetime?: string;
  metadata?: Record<string, unknown>;
  /** False when an imported recording has no archived copy. */
  content_available?: boolean;
  /** Saved, verified APTEM metadata permits an on-demand Teams playback attempt. */
  graph_playback_candidate?: boolean;
}

export interface CoachMeetingAttendanceRecord {
  id: string;
  reportId?: string;
  graphRecordId?: string;
  email?: string;
  displayName: string;
  role?: string;
  totalAttendanceSeconds: number;
  attended: boolean;
  intervals?: Array<{
    joinDateTime?: string;
    leaveDateTime?: string;
  }>;
}

export interface CoachMeetingExpectedAttendee {
  id: string;
  role: 'coach' | 'learner' | 'employer' | 'attendee' | string;
  name: string;
  displayName?: string;
  email?: string;
  required?: boolean;
}

export interface CoachMeetingAttendanceTrackerRow {
  coachVerification?: { id: number; confirmedBy: string; confirmedAt: string; reason: string };
  linkedEmail?: string;
  id: string;
  role: 'coach' | 'learner' | 'employer' | 'attendee' | string;
  name: string;
  displayName?: string;
  email?: string;
  expected: boolean;
  required?: boolean;
  attended: boolean;
  status: 'attended' | 'absent' | 'pending' | 'extra' | string;
  totalAttendanceSeconds: number;
  actualDisplayName?: string;
  actualRecordIds?: string[];
  reportIds?: string[];
}

export interface CoachMeetingAttendanceReport {
  id: string;
  meetingStartDateTime?: string;
  meetingEndDateTime?: string;
  totalParticipantCount: number;
  records: CoachMeetingAttendanceRecord[];
  attendedCount: number;
  absentCount: number;
}

export interface CoachMeetingAttendanceSummary {
  linkWarning?: string;
  reports: CoachMeetingAttendanceReport[];
  records: CoachMeetingAttendanceRecord[];
  expectedAttendees?: CoachMeetingExpectedAttendee[];
  tracker?: CoachMeetingAttendanceTrackerRow[];
  reportCount: number;
  attendedCount: number;
  absentCount: number;
  participantCount: number;
  expectedCount?: number;
  expectedAttendedCount?: number;
  expectedAbsentCount?: number;
  expectedPendingCount?: number;
  extraAttendees?: CoachMeetingAttendanceTrackerRow[];
  extraCount?: number;
}

export interface CoachMeetingSummaryAction {
  title: string;
  owner?: string;
  dueDate?: string;
  status?: string;
}

export interface CoachMeetingSummaryPayload {
  title: string;
  overview: string;
  keyPoints: string[];
  actions: CoachMeetingSummaryAction[];
  nextSteps: string[];
  support: string[];
}

export interface CoachMeetingSummary {
  summary: CoachMeetingSummaryPayload;
  status: 'ready' | 'edited' | 'failed' | string;
  generatedAt?: string | null;
  editedAt?: string | null;
  editedBy?: string;
  model?: string;
  error?: string;
}

export interface MigratedSummaryGenerationError {
  code: string;
  message: string;
  requestId: string;
}

export interface MigratedSummaryBinding {
  fieldKey?: string;
  state?: 'NEVER_POPULATED' | 'AI_POPULATED_UNEDITED' | 'COACH_EDITED' | 'COACH_CLEARED';
  status?: string;
  message?: string;
  suggestionText?: string;
  answerPresent?: boolean;
  answer?: unknown;
  suggestionSource?: 'teams' | 'uploaded_transcript';
  generatedAt?: string;
  generationStatus?: 'ready' | 'failed' | 'unavailable';
  generationAttemptedAt?: string | null;
  generationError?: MigratedSummaryGenerationError;
  replacementAvailable?: boolean;
  transcriptTruncated?: boolean;
  provisional?: boolean;
  summaryTooLong?: boolean;
}

export interface CoachMeetingArtifactsResponse {
  progressVersion?: string;
  summaryBinding?: MigratedSummaryBinding;
  answerVersion?: string;
  reviewAnswers?: Record<string, unknown>;
  intelligence?: {
    lastCheckedAt?: string | null;
    attendanceStatus: string;
    recordingStatus: string;
    transcriptStatus: string;
    summaryStatus: string;
    summaryProvisional?: boolean;
    generationError?: MigratedSummaryGenerationError | null;
    transcriptArtifactId?: string | null;
    errorCodes: string[];
    meetingEndStatus?: 'pending' | 'attendance-confirmed' | 'scheduled-end-reached' | null;
    meetingEndCode?: 'MEETING_END_PENDING' | null;
    resourceErrors?: Record<string, string>;
  };
  event?: {
    eventKey: string;
    source: string;
    status: string;
    learner: string;
    scheduledDate: string | null;
    scheduledTime: string | null;
  };
  attendance?: CoachMeetingAttendanceSummary;
  artifacts: CoachMeetingArtifact[];
  meetingSummary?: CoachMeetingSummary | null;
  errors?: string[];
  partial?: boolean;
  storage?: {
    stored: boolean;
    reason?: string;
    syncedAt?: string;
  };
  /** Imported party portals read saved evidence; only a Coach can refresh Teams. */
  resourceMode?: 'stored';
}

async function readJsonResponse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof data.detail === 'string'
      ? data.detail
      : typeof data.error === 'string'
        ? data.error
        : `Request failed with ${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

async function sha256Hex(value: string) {
  if (globalThis.crypto?.subtle) {
    const bytes = new TextEncoder().encode(value);
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export async function calendarBookingIdempotencyKey(input: CoachCalendarBookingInput) {
  const fingerprint = JSON.stringify({
    learnerId: String(input.learnerId),
    sessionType: input.sessionType.trim().toLowerCase(),
    scheduledDate: input.scheduledDate,
    scheduledTime: input.scheduledTime.slice(0, 5),
    durationMinutes: input.durationMinutes,
    notes: input.notes.trim(),
    absenceReportId: input.absenceReportId || '',
  });
  return `coach-book:${await sha256Hex(fingerprint)}`;
}

export async function bookCoachCalendarEvent(input: CoachCalendarBookingInput) {
  const response = await coachFetch(BOOK_ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': await calendarBookingIdempotencyKey(input),
    },
    body: JSON.stringify(input),
  });
  return readJsonResponse<{
    event: CoachCalendarEvent;
    warning?: string;
    operation?: { id: string; syncState: string; attemptCount: number; replayed: boolean };
  }>(response);
}

export async function fetchCoachCalendarEvents(
  signal: AbortSignal | undefined,
  options: CoachCalendarFetchOptions = {},
) {
  // Not via `coachFetch`, so the admin's selected coach has to be added here
  // too — otherwise the calendar would load the caller's own (empty) timetable.
  return fetchSharedJsonGet<CoachTimetableResponse>(withCoachViewAs(coachTimetableEndpoint(options)), {
    signal,
    credentials: 'include',
  });
}

/**
 * Read one named coach's timetable without changing the administrator's active
 * workspace selection. This is used by the super-admin overview, where several
 * coach calendars are displayed together. The server validates the supplied
 * address against the Coach staff directory and keeps the request read-only.
 */
export async function fetchCoachCalendarEventsForCoach(
  coachEmail: string,
  signal: AbortSignal | undefined,
  options: CoachCalendarFetchOptions = {},
) {
  const endpoint = coachTimetableEndpoint(options);
  const separator = endpoint.includes('?') ? '&' : '?';
  const url = `${endpoint}${separator}viewAsCoach=${encodeURIComponent(coachEmail.trim().toLowerCase())}`;
  return fetchSharedJsonGet<CoachTimetableResponse>(url, {
    signal,
    credentials: 'include',
  });
}

export interface AllCoachesCalendarResponse {
  calendars: Array<{
    email: string;
    events: CoachCalendarEvent[];
    refreshedAt?: string | null;
  }>;
  missingCoaches?: string[];
}

/** One snapshot-backed request for the super-admin all-coaches week view. */
export async function fetchAllCoachesCalendarEvents(
  signal: AbortSignal | undefined,
  options: Pick<CoachCalendarFetchOptions, 'start' | 'end'>,
) {
  const params = new URLSearchParams();
  if (options.start) params.set('start', options.start);
  if (options.end) params.set('end', options.end);
  return fetchSharedJsonGet<AllCoachesCalendarResponse>(
    `/coach_api/coaches/calendar?${params.toString()}`,
    { signal, credentials: 'include' },
  );
}

export async function fetchCoachMeetingArtifacts(
  eventKey: string,
  signal?: AbortSignal,
  options: { refresh?: boolean } = {},
): Promise<CoachMeetingArtifactsResponse> {
  const response = await coachFetch(
    `/coach_api/coach/timetable/events/${encodeURIComponent(eventKey)}/artifacts`,
    { signal, ...(options.refresh ? { method: 'POST' } : {}) },
  );
  return readJsonResponse<CoachMeetingArtifactsResponse>(response);
}

export function coachMeetingArtifactContentUrl(
  eventKey: string,
  artifactType: string,
  artifactId: string,
  options: { preview?: boolean } = {},
): string {
  const base = `/coach_api/coach/timetable/events/${encodeURIComponent(eventKey)}/artifacts/${encodeURIComponent(artifactType)}/${encodeURIComponent(artifactId)}/content`;
  return withCoachViewAs(options.preview ? `${base}?preview=1` : base);
}

export async function updateCoachMeetingSummary(
  eventKey: string,
  summary: CoachMeetingSummaryPayload,
): Promise<{ meetingSummary: CoachMeetingSummary | null }> {
  const response = await coachFetch(
    `/coach_api/coach/timetable/events/${encodeURIComponent(eventKey)}/summary`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ summary }),
    },
  );
  return readJsonResponse<{ meetingSummary: CoachMeetingSummary | null }>(response);
}

export async function scheduleCoachCalendarEvent(event: CoachCalendarEvent, form: ScheduleFormState) {
  if (!event.eventKey) {
    throw new Error('This event is missing its calendar key.');
  }

  const response = await coachFetch(SCHEDULE_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      eventKey: event.eventKey,
      scheduledDate: form.date,
      scheduledTime: form.time,
      durationMinutes: form.durationMinutes,
    }),
  });
  const data = await readJsonResponse<{ event: CoachCalendarEvent; warning?: string }>(response);
  return data;
}

export async function runCoachCalendarAction(
  event: CoachCalendarEvent,
  action: CalendarAction,
  options: { reviewResponses?: Record<string, string>; managerName?: string } = {},
) {
  if (!event.eventKey) {
    throw new Error('This event is missing its calendar key.');
  }

  const response = await coachFetch(ACTION_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      eventKey: event.eventKey,
      action,
      ...options,
    }),
  });
  const data = await readJsonResponse<{ event: CoachCalendarEvent; warning?: string }>(response);
  return data;
}

export function eventIdentity(event: CoachCalendarEvent) {
  return event.eventKey || event.id;
}

export function parseLocalDate(value?: string | null) {
  if (!value) return null;
  const datePart = value.split('T')[0];
  const parts = datePart.split('-').map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

export function startOfDay(value = new Date()) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

export function getCurrentWorkWeekRange(referenceDate = new Date()) {
  const today = startOfDay(referenceDate);
  const day = today.getDay();
  const mondayOffset = day === 0 ? -6 : 1 - day;
  const start = new Date(today);
  start.setDate(today.getDate() + mondayOffset);

  const end = new Date(start);
  end.setDate(start.getDate() + 4);
  end.setHours(23, 59, 59, 999);
  return { start, end };
}

export function getNextWorkWeekRange(referenceDate = new Date()) {
  const { start: currentMonday } = getCurrentWorkWeekRange(referenceDate);
  const start = new Date(currentMonday);
  start.setDate(currentMonday.getDate() + 7);

  const end = new Date(start);
  end.setDate(start.getDate() + 4);
  end.setHours(23, 59, 59, 999);
  return { start, end };
}

// Retained for existing calendar consumers; a dashboard work week is the same
// established Monday-to-Friday range.
export const currentWeekRange = getCurrentWorkWeekRange;

export function eventDisplayDate(event: CoachCalendarEvent) {
  return event.scheduledDate || event.date || event.targetDate || '';
}

export function eventTargetDate(event: CoachCalendarEvent) {
  return event.targetDate || event.date || event.scheduledDate || '';
}

export function formatDateLabel(value?: string | null) {
  const date = parseLocalDate(value);
  if (!date) return '--';
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date);
}

export function formatTimeLabel(event: CoachCalendarEvent) {
  if (event.scheduledTime) {
    return `${event.scheduledTime.slice(0, 5)} - ${event.durationMinutes || 60} min`;
  }
  return event.timeLabel && event.timeLabel !== 'Time TBC' ? event.timeLabel : 'Time TBC';
}

/** Display a booked session as a start/end range instead of a duration label. */
export function formatTimeRangeLabel(event: CoachCalendarEvent) {
  if (!event.scheduledTime) return event.timeLabel && event.timeLabel !== 'Time TBC' ? event.timeLabel : 'Time TBC';
  const startText = event.scheduledTime.slice(0, 5);
  const match = /^(\d{1,2}):(\d{2})$/.exec(startText);
  if (!match) return startText;
  const start = new Date(2000, 0, 1, Number(match[1]), Number(match[2]));
  start.setMinutes(start.getMinutes() + (event.durationMinutes || 60));
  const endText = `${String(start.getHours()).padStart(2, '0')}:${String(start.getMinutes()).padStart(2, '0')}`;
  return `${startText} - ${endText}`;
}

export function scheduleDefaults(event: CoachCalendarEvent): ScheduleFormState {
  return {
    date: event.scheduledDate || event.targetDate || event.date || '',
    time: event.scheduledTime ? event.scheduledTime.slice(0, 5) : '09:00',
    durationMinutes: event.durationMinutes || 60,
  };
}

export function sortEvents(events: CoachCalendarEvent[]) {
  return [...events].sort((a, b) => {
    const aDate = parseLocalDate(eventDisplayDate(a))?.getTime() || 0;
    const bDate = parseLocalDate(eventDisplayDate(b))?.getTime() || 0;
    if (aDate !== bDate) return aDate - bDate;
    // A generated event's startHour is a placeholder (isTimeEstimated), not a
    // real booking, and defaults to the same hour (9) most confirmed sessions
    // start at. Without this, an unscheduled "Time TBC" coaching slot ties
    // with a real 09:00 live session and can win the tiebreak on array order
    // alone -- ranking as "Next" ahead of something that actually has a set
    // time today.
    const aEstimated = Boolean(a.isTimeEstimated);
    const bEstimated = Boolean(b.isTimeEstimated);
    if (aEstimated !== bEstimated) return aEstimated ? 1 : -1;
    return (a.startHour || 0) - (b.startHour || 0);
  });
}

export function initialsFor(name?: string) {
  const parts = (name || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return '--';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

export function statusLabel(status: CoachCalendarStatus) {
  const labels: Record<CoachCalendarStatus, string> = {
    completed: 'Completed',
    scheduled: 'Scheduled',
    'in-progress': 'In Progress',
    'awaiting-signature': 'Awaiting Signature',
    confirmed: 'Confirmed',
    pending: 'Pending',
    cancelled: 'Cancelled',
    'not-scheduled': 'Not Scheduled',
  };
  return labels[status] || status;
}

export function statusPillClass(status: CoachCalendarStatus) {
  if (status === 'completed' || status === 'confirmed') return 'bg-emerald-100 text-emerald-700';
  if (status === 'scheduled') return 'bg-primary-100 text-primary-700';
  if (status === 'in-progress') return 'bg-primary-100 text-primary-700';
  if (status === 'awaiting-signature') return 'bg-violet-100 text-violet-700';
  if (status === 'cancelled') return 'bg-red-100 text-red-700';
  if (status === 'not-scheduled') return 'bg-red-100 text-red-700';
  return 'bg-rose-100 text-rose-700';
}

export function avatarClass(event: CoachCalendarEvent) {
  if (event.status === 'cancelled') return 'bg-red-100 text-red-700 ring-red-200';
  if (event.priority === 'urgent' || event.priority === 'high') return 'bg-rose-100 text-rose-700 ring-rose-200';
  if (event.status === 'completed' || event.status === 'confirmed') return 'bg-emerald-100 text-emerald-700 ring-emerald-200';
  return 'bg-primary-100 text-primary-700 ring-primary-200';
}

export function isConfirmedEvent(event: CoachCalendarEvent) {
  return ['scheduled', 'in-progress', 'completed', 'confirmed'].includes(event.status);
}

export function needsScheduling(event: CoachCalendarEvent) {
  return ['not-scheduled', 'pending'].includes(event.status);
}

export function isScheduledEvent(event: CoachCalendarEvent) {
  return event.status === 'scheduled';
}

export function hasScheduledSlot(event: CoachCalendarEvent) {
  return isScheduledEvent(event) || Boolean(event.scheduledDate && event.scheduledTime);
}

export function isInProgressEvent(event: CoachCalendarEvent) {
  return event.status === 'in-progress';
}

export function isAwaitingSignatureEvent(event: CoachCalendarEvent) {
  return event.status === 'awaiting-signature';
}

export function isCancelledEvent(event: CoachCalendarEvent) {
  return event.status === 'cancelled';
}

export function isUrgentEvent(event: CoachCalendarEvent) {
  return event.priority === 'urgent' || event.priority === 'high';
}

export function isCompletedEvent(event: CoachCalendarEvent) {
  return event.status === 'completed';
}

export function reviewScheduleAvailable(
  summary?: {
    progressReviewRows?: number | null;
    mcrRows?: number | null;
    learnersWithDates?: number | null;
  },
  events: CoachCalendarEvent[] = [],
  issues: readonly unknown[] = [],
) {
  const hasResolvedReviews = events.some(event => (
    event.source === 'progress-review' || event.source === 'mcr'
  ));
  const hasResolvedSummary = (
    (summary?.progressReviewRows ?? 0) > 0
    || (summary?.mcrRows ?? 0) > 0
    || (summary?.learnersWithDates ?? 0) > 0
  );

  return hasResolvedReviews || hasResolvedSummary || issues.length === 0;
}

export function latestCompletedReviewDate(
  events: CoachCalendarEvent[],
  learnerId: string,
  source: 'mcr' | 'progress-review',
) {
  const latest = events
    .filter(event => (
      isCompletedEvent(event)
      && reviewCategory(event) === source
      && String(event.learnerId || '') === String(learnerId)
    ))
    .map(event => ({
      value: event.reviewCompletedAt,
      date: parseLocalDate(event.reviewCompletedAt),
    }))
    .filter((entry): entry is { value: string; date: Date } => Boolean(entry.value && entry.date))
    .sort((left, right) => right.date.getTime() - left.date.getTime())[0];

  return latest?.value ?? null;
}

export function canJoinMeeting(event: CoachCalendarEvent, referenceDate = new Date()) {
  if (!['scheduled', 'in-progress'].includes(event.status) || !meetingUrl(event)) return false;
  const scheduledDate = parseLocalDate(event.scheduledDate);
  if (!scheduledDate) return false;
  return scheduledDate.getTime() <= startOfDay(referenceDate).getTime();
}

export function isUpcomingEvent(event: CoachCalendarEvent) {
  return !['completed', 'cancelled'].includes(event.status);
}

export function isEventInMonth(event: CoachCalendarEvent, referenceDate = new Date()) {
  const displayDate = parseLocalDate(eventDisplayDate(event));
  if (!displayDate) return false;

  return (
    displayDate.getFullYear() === referenceDate.getFullYear()
    && displayDate.getMonth() === referenceDate.getMonth()
  );
}

export function isAtRiskEvent(event: CoachCalendarEvent, referenceDate = new Date()) {
  if (!needsScheduling(event)) return false;
  const targetDate = parseLocalDate(eventTargetDate(event));
  if (!targetDate) return false;

  const today = startOfDay(referenceDate);
  return targetDate.getTime() < today.getTime();
}

export function isDueSoonEvent(event: CoachCalendarEvent, referenceDate = new Date(), daysAhead = 14) {
  if (!needsScheduling(event) || isAtRiskEvent(event, referenceDate)) return false;
  const targetDate = parseLocalDate(eventTargetDate(event));
  if (!targetDate) return false;

  const today = startOfDay(referenceDate);
  const riskCutoff = new Date(today);
  riskCutoff.setDate(today.getDate() + daysAhead);

  return targetDate.getTime() >= today.getTime() && targetDate.getTime() <= riskCutoff.getTime();
}

export function isEventThisWeek(event: CoachCalendarEvent, referenceDate = new Date()) {
  const displayDate = parseLocalDate(eventDisplayDate(event));
  if (!displayDate || isCompletedEvent(event)) return false;

  const { start, end } = currentWeekRange(referenceDate);
  return displayDate.getTime() >= start.getTime() && displayDate.getTime() <= end.getTime();
}

export function isEventThisMonth(event: CoachCalendarEvent, referenceDate = new Date()) {
  return !isCompletedEvent(event) && isEventInMonth(event, referenceDate);
}

export function isAtRiskProgressReview(event: CoachCalendarEvent, referenceDate = new Date()) {
  return event.source === 'progress-review' && isAtRiskEvent(event, referenceDate);
}

export function meetingUrl(event: CoachCalendarEvent) {
  return event.meetingLink || event.graphWebLink || '';
}

export function openPendingMeetingWindow() {
  const meetingWindow = window.open('about:blank', '_blank');
  if (meetingWindow) meetingWindow.opener = null;
  return meetingWindow;
}

export function navigateMeetingWindow(meetingWindow: Window | null, url: string) {
  if (meetingWindow && !meetingWindow.closed) {
    meetingWindow.location.href = url;
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

export function eventPeriodLabel(event: CoachCalendarEvent) {
  if (!event.sequence) return 'Review';
  return `Review ${event.sequence}`;
}
