import { createCachedResource } from './cachedRequest';
import { readLearnerJson, invalidateLearnerReads, subscribeLearnerReadInvalidation, withCallerSignal } from './learnerRead';
import type { LearnerKind } from '@/api/learnerDetail';
import type { CoachMeetingArtifactsResponse } from '@/pages/coach/shared/calendarEvents';
import type { ImportedReview } from '@/api/reviewHistory';
import type { ReviewInstanceFormDefinition } from '@/api/reviewInstances';

const BASE = '/learner_api/calendar';
const calendarResource = createCachedResource<LearnerCalendarResponse>('learner-calendar', key =>
  readLearnerJson(`${BASE}/${key.replace(':', '/')}/`));
// Programme, placement and plan saves expire shared reads too. The outer
// calendar snapshot must not outlive those changes.
subscribeLearnerReadInvalidation(() => calendarResource.invalidate());

export interface LearnerCalendarEvent {
  id: string;
  /** Server-derived once the booked time has passed: learner attended (completed) or not (ended). */
  meetingOutcome?: 'ended' | 'completed' | null;
  /** Live session the learner missed but then watched in full as a recording. */
  watchedRecording?: boolean;
  /** Catch-up only: the absence report it already makes up, if any. */
  linkedReportId?: number | null;
  /** Catch-up only: starts too soon for the learner to reschedule or cancel it. */
  changeClosed?: boolean;
  eventKey: string;
  title: string;
  source: 'mcr' | 'progress-review' | string;
  type: 'coaching' | 'review' | string;
  sequence: number;
  reviewTemplateId?: string | null;
  reviewInstanceId?: string | null;
  migratedForm?: boolean;
  reviewTypeId?: string | null;
  reviewTypeCode?: string | null;
  reviewTypeName?: string | null;
  reviewTypeIsSystem?: boolean;
  occurrenceNumber?: number | null;
  bookingStatus?: string;
  /** Read-only occupancy of the canonically linked booking, independent of form status. */
  bookingReserved?: boolean;
  status: 'not-scheduled' | 'scheduled' | 'in-progress' | 'completed' | 'cancelled' | string;
  date: string | null;
  targetDate: string | null;
  scheduledDate: string | null;
  scheduledTime: string | null;
  durationMinutes: number;
  coachName: string;
  coachEmail: string;
  meetingProvider: string;
  meetingLink: string;
  notes: string;
  /** Editable Aptem booking notes, without the generated calendar description. */
  bookingNotes?: string;
  reviewResponses?: Record<string, string>;
  reviewCompletedAt?: string | null;
  learnerSigned?: boolean;
  learnerSignedAt?: string | null;
  employerSigned?: boolean;
  employerSignatureRequired?: boolean;
  /** False when the Microsoft Graph sync failed: saved locally, but no invite sent. */
  invited?: boolean;
  syncError?: string;
  syncState?: 'pending' | 'syncing' | 'synced' | 'failed' | 'reconciliation' | 'cancelled';
  syncWarning?: string;
  reviewId?: string;
  reviewSource?: 'aptem' | 'curriculum';
  aptemReviewId?: string;
  importedReviewType?: string;
  sourceStatus?: string;
  rawStatus?: string;
  effectiveLmsStatus?: string;
  localStatus?: string;
  calendarEventId?: string;
  calendarEventKey?: string | null;
  formEventKey?: string;
  assignmentMonth?: string;
  programme?: string;
  cohort?: string;
  group?: string;
  module?: string;
  /** Present when this event came from Learner.reviews (Aptem import). */
  importedReview?: ImportedReview;
}

export interface LearnerCalendarResponse {
  learner: { kind: LearnerKind; id: number; email?: string };
  /** Current assignment; event.coachName remains the saved meeting host. */
  currentCoach?: { name: string; email: string };
  events: LearnerCalendarEvent[];
  bookingCalendar?: BookingCalendarRules;
}

export interface BookingCalendarRules {
  division: 'england-and-wales';
  today: string;
  coveredYears: number[];
  bankHolidays: Array<{ date: string; title: string }>;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  if (!init?.method || init.method.toUpperCase() === 'GET') return readLearnerJson<T>(url, init);
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`Unexpected response (${res.status}).`);
  }
  if (!res.ok) {
    const message = (data as { error?: string } | null)?.error || `Request failed with ${res.status}`;
    throw new Error(message);
  }
  return data as T;
}

export function invalidateLearnerCalendarCache(kind?: LearnerKind, id?: string): void {
  calendarResource.invalidate(kind && id ? `${kind}:${id}` : undefined);
  firstSessionResource.invalidate(kind && id ? `${kind}:${id}` : undefined);
  invalidateLearnerReads();
}

export function fetchLearnerCalendarEvents(kind: LearnerKind, id: string, options: { force?: boolean; revalidate?: boolean } = {}): Promise<LearnerCalendarResponse> {
  if (options.force) invalidateLearnerCalendarCache(kind, id);
  return calendarResource.read(`${kind}:${id}`, { revalidate: options.revalidate });
}

/** Fetch the Curriculum-authored review form for an existing calendar occurrence. */
export type LearnerReviewDefinition = Omit<ReviewInstanceFormDefinition, 'instance'> & {
  /** Aptem MCM month comes from the server, not the rescheduled appointment. */
  mcmMonthlyLog?: { month: string; sync?: MonthlyLogSignatureSync };
  instance: ReviewInstanceFormDefinition['instance'] | null;
  occurrenceNumber?: number;
  /** Reusable learner signature from enrolment.Created_users, when present. */
  savedSignature?: string;
  savedSignatureName?: string;
};

const migratedReviewUrl = (eventKey: string) => `/learner_api/migrated-reviews/${encodeURIComponent(eventKey)}`;

export function fetchMigratedReviewForParty(eventKey: string, signal?: AbortSignal): Promise<LearnerReviewDefinition> {
  return request<LearnerReviewDefinition>(`${migratedReviewUrl(eventKey)}/party`, { signal, credentials: 'include' });
}

export async function saveMigratedReviewPartyAnswers(
  eventKey: string, answers: Record<string, unknown>, answerVersion?: string,
): Promise<LearnerReviewDefinition> {
  if (!answerVersion) throw new Error('Reopen the review to load its latest answers before saving.');
  const csrfResponse = await fetch('/learner_api/migrated-reviews/csrf', { credentials: 'include' });
  const csrf = await csrfResponse.json().catch(() => ({})) as { csrfToken?: string };
  if (!csrfResponse.ok || !csrf.csrfToken) throw new Error('Unable to verify this answer request.');
  const response = await fetch(`${migratedReviewUrl(eventKey)}/party-answers`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrfToken },
    body: JSON.stringify({ answers, answerVersion }),
  });
  const data = await response.json() as LearnerReviewDefinition & { detail?: string };
  if (!response.ok) throw new Error(data.detail || `Saving answers failed (${response.status}).`);
  return data;
}

export async function downloadMigratedReviewForParty(eventKey: string): Promise<void> {
  const { saveReviewPdfResponse } = await import('./reviewPdf');
  await saveReviewPdfResponse(await fetch(`${migratedReviewUrl(eventKey)}/party-pdf`, { credentials: 'include' }));
}

export async function downloadMigratedRevisionForParty(eventKey: string, number: number): Promise<void> {
  const { saveReviewPdfResponse } = await import('./reviewPdf');
  await saveReviewPdfResponse(await fetch(`${migratedReviewUrl(eventKey)}/revisions/${number}/pdf`, { credentials: 'include' }));
}

export interface MonthlyLogSignatureSync {
  status: 'pending' | 'failed' | 'synced' | 'already-synced' | 'skipped';
  month?: string;
  message?: string;
  reason?: string;
}

interface MigratedSignResult {
  signed: boolean;
  role: string;
  monthlyLogSync?: MonthlyLogSignatureSync;
}

export function signMigratedReviewAsParty(
  eventKey: string, signature: string, options?: { applyMonthlyLogSignature?: boolean; reviewRevision?: number },
): Promise<MigratedSignResult> {
  return migratedPartySignRequest(eventKey, { signature, ...options });
}

export function retryMigratedMonthlyLogSignature(eventKey: string): Promise<MigratedSignResult> {
  return migratedPartySignRequest(eventKey, { retryMonthlyLogSignature: true });
}

function migratedPartySignRequest(eventKey: string, payload: {
  signature?: string; applyMonthlyLogSignature?: boolean; retryMonthlyLogSignature?: boolean; reviewRevision?: number;
}): Promise<MigratedSignResult> {
  return (async () => {
    const csrfResponse = await fetch('/learner_api/migrated-reviews/csrf', { credentials: 'include' });
    const csrf = await csrfResponse.json().catch(() => ({})) as { csrfToken?: string };
    if (!csrfResponse.ok || !csrf.csrfToken) throw new Error('Unable to verify this signing request.');
    const response = await fetch(`${migratedReviewUrl(eventKey)}/party-sign`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf.csrfToken },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({})) as MigratedSignResult & { detail?: string };
    if (!response.ok) throw new Error(data.detail || `Signing failed (${response.status}).`);
    return data;
  })();
}

export function fetchLearnerEventReviewInstance(
  kind: LearnerKind,
  learnerId: string,
  eventKey: string,
  signal?: AbortSignal,
): Promise<LearnerReviewDefinition | { instance: null }> {
  return request<LearnerReviewDefinition | { instance: null }>(`${BASE}/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/review/`, { signal, credentials: 'include' });
}

/** Save the learner's own answers to whichever fields the Curriculum
 * template opted the Learner into answering -- every other field id is
 * rejected server-side, so only the fields this form actually unlocked
 * should ever be posted here. */
export function saveLearnerEventReviewAnswers(
  kind: LearnerKind,
  learnerId: string,
  eventKey: string,
  answers: Record<string, unknown>,
): Promise<LearnerReviewDefinition> {
  return request<LearnerReviewDefinition>(`${BASE}/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/review/`, {
    method: 'POST',
    credentials: 'include',
    body: JSON.stringify({ answers }),
  });
}

export async function downloadLearnerMcmPdf(kind: LearnerKind, learnerId: string, eventKey: string): Promise<void> {
  const { saveReviewPdfResponse } = await import('./reviewPdf');
  await saveReviewPdfResponse(await fetch(`${BASE}/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/review/pdf/`, { credentials: 'include' }));
}

export function fetchLearnerMeetingArtifacts(kind: LearnerKind, learnerId: string, eventKey: string, signal?: AbortSignal): Promise<CoachMeetingArtifactsResponse> {
  return request<CoachMeetingArtifactsResponse>(`${BASE}/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/artifacts/`, { signal, credentials: 'include' });
}

export function learnerMeetingArtifactContentUrl(kind: LearnerKind, learnerId: string, eventKey: string, artifactType: string, artifactId: string, options: { preview?: boolean } = {}): string {
  const base = `${BASE}/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/artifacts/${encodeURIComponent(artifactType)}/${encodeURIComponent(artifactId)}/content/`;
  return options.preview ? `${base}?preview=1` : base;
}

export async function signLearnerProgressReview(kind: LearnerKind, learnerId: string, eventKey: string, input: { name: string; signature: string; applyMonthlyLogSignature?: boolean }): Promise<{ event: LearnerCalendarEvent; monthlyLogSync?: { status: string; month?: string; message?: string } | null }> {
  const response = await fetch(`${BASE}/${kind}/${learnerId}/events/${encodeURIComponent(eventKey)}/sign/`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  const data = await response.json().catch(() => ({})) as { event?: LearnerCalendarEvent; error?: string; monthlyLogSync?: { status: string; month?: string; message?: string } | null };
  if (!response.ok || !data.event) throw new Error(data.error || `Could not sign the review (${response.status}).`);
  invalidateLearnerCalendarCache(kind, learnerId);
  if (data.monthlyLogSync?.status === 'failed') {
    throw new Error(data.monthlyLogSync.message || 'The MCM was signed, but the monthly log could not be updated. Please try again.');
  }
  return { event: data.event, monthlyLogSync: data.monthlyLogSync };
}

export type BookableSessionType =
  | 'first-session'
  | 'catch-up'
  | 'student-support'
  // Monthly coaching and progress reviews resolve to their official
  // Curriculum occurrence before scheduling; they never create an unlinked
  // standalone review row.
  | 'mcr'
  | 'progress-review'
  | 'review'
  | 'gateway'
  | 'other'
  // The three onboarding reviews, bookable while still Onboarding (they go to
  // the learner's case owner rather than a coach, who doesn't exist yet).
  | OnboardingReviewType;

export type OnboardingReviewType = 'eligibility-review' | 'workspace' | 'training-plan' | 'uln-privacy';

export interface OnboardingReview {
  type: OnboardingReviewType;
  label: string;
  booked: boolean;
  event: LearnerCalendarEvent | null;
  /** True when this review has a form to complete after booking. */
  hasForm?: boolean;
  formStarted?: boolean;
  formCompleted?: boolean;
  learnerSigned?: boolean;
  adminSigned?: boolean;
}

export interface OnboardingReviewsResponse {
  caseOwner: { name: string; email: string } | null;
  reviews: OnboardingReview[];
  allBooked: boolean;
}

/** The three onboarding reviews and whether each has been booked. */
export function fetchOnboardingReviews(kind: LearnerKind, id: string): Promise<OnboardingReviewsResponse> {
  return request<OnboardingReviewsResponse>(`${BASE}/${kind}/${id}/onboarding-reviews/`);
}

export interface BookSessionInput {
  assignmentMonth?: string;
  /** Distinguishes an assignment MCM from an imported review with month context. */
  bookingContext?: 'monthly-assignment';
  /** Imported Aptem review row to mark scheduled after an MCR booking. */
  reviewId?: string;
  sessionType: BookableSessionType;
  /** Required when booking a generated MCM/Progress Review slot. */
  eventKey?: string;
  scheduledDate: string; // YYYY-MM-DD
  scheduledTime: string; // HH:MM
  durationMinutes: number;
  notes?: string;
  timezoneOffsetMinutes?: number;
  /** Catch-up only: the absence report the booking makes up, linked in the same request. */
  absenceReportId?: number;
}

export interface BookSessionResponse {
  event: LearnerCalendarEvent;
  warning?: string;
  approvalRequired?: boolean;
  /** Set when the server linked the catch-up to this absence report. */
  linkedReportId?: number;
}

export async function bookLearnerCalendarSession(
  kind: LearnerKind,
  id: string,
  input: BookSessionInput,
): Promise<BookSessionResponse> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/${kind}/${id}/book/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`Unexpected response (${res.status}).`);
  }
  if (!res.ok) {
    const message = (data as { error?: string } | null)?.error || `Request failed with ${res.status}`;
    throw new Error(message);
  }
  invalidateLearnerCalendarCache(kind, id);
  return data as BookSessionResponse;
}

export type RescheduleSessionInput = Pick<
  BookSessionInput,
  'scheduledDate' | 'scheduledTime' | 'durationMinutes' | 'timezoneOffsetMinutes' | 'notes'
> & { eventKey: string; reviewId?: string; expectedBooking?: ExpectedReviewBooking };

export interface ExpectedReviewBooking {
  scheduledDate: string;
  scheduledTime: string;
  durationMinutes: number;
  notes: string;
}

/** Move an existing booking; the backend updates the same Graph/Teams event. */
export async function rescheduleLearnerCalendarSession(
  kind: LearnerKind,
  id: string,
  input: RescheduleSessionInput,
): Promise<BookSessionResponse> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/${kind}/${id}/reschedule/`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`Unexpected response (${res.status}).`);
  }
  if (!res.ok) {
    const message = (data as { error?: string } | null)?.error || `Request failed with ${res.status}`;
    throw new Error(message);
  }
  invalidateLearnerCalendarCache(kind, id);
  return data as BookSessionResponse;
}

/** Cancel a session the learner booked; Graph emails the cancellation to both. */
export async function cancelLearnerCalendarSession(
  kind: LearnerKind,
  id: string,
  eventKey: string,
): Promise<BookSessionResponse> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/${kind}/${id}/cancel/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ eventKey }),
    });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`Unexpected response (${res.status}).`);
  }
  if (!res.ok) {
    const message = (data as { error?: string } | null)?.error || `Request failed with ${res.status}`;
    throw new Error(message);
  }
  invalidateLearnerCalendarCache(kind, id);
  return data as BookSessionResponse;
}

export function fetchLearnerCoach(id: string): Promise<{ coachName: string; coachEmail: string }> {
  return request<{ coachName: string; coachEmail: string }>(`/learner_api/learners/${id}/coach/`);
}

export type PersonalCalendarProvider = 'google' | 'microsoft' | 'icloud' | 'caldav' | 'ics';

export interface PersonalCalendarConnection {
  provider: PersonalCalendarProvider;
  accountEmail: string;
  status: string;
  connectedAt: string | null;
  lastSyncAt: string | null;
}

export interface CalendarBusySlot {
  start: string;
  end: string;
  provider: PersonalCalendarProvider;
}

async function calendarConnectionRequest<T>(url: string, init?: Parameters<typeof fetch>[1]): Promise<T> {
  if (!init?.method || init.method.toUpperCase() === 'GET') return readLearnerJson<T>(url, init);
  let response: Response;
  try {
    response = await fetch(url, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  } catch {
    throw new Error('Could not reach the calendar connection service.');
  }
  const data = await response.json().catch(() => null) as { error?: string } | null;
  if (!response.ok) throw new Error(data?.error || `Calendar request failed (${response.status}).`);
  return data as T;
}

export function fetchCalendarConnections(kind: LearnerKind, id: string): Promise<{ connections: PersonalCalendarConnection[] }> {
  return calendarConnectionRequest(`/learner_api/calendar-connections/${kind}/${id}/`);
}

export async function startCalendarOAuth(kind: LearnerKind, id: string, provider: 'google' | 'microsoft'): Promise<void> {
  const result = await calendarConnectionRequest<{ authorizationUrl: string }>(`/learner_api/calendar-connections/${kind}/${id}/${provider}/oauth/`);
  window.location.assign(result.authorizationUrl);
}

export function connectCredentialCalendar(
  kind: LearnerKind,
  id: string,
  provider: 'icloud' | 'caldav' | 'ics',
  input: { url: string; username?: string; password?: string },
): Promise<{ connection: PersonalCalendarConnection }> {
  return calendarConnectionRequest(`/learner_api/calendar-connections/${kind}/${id}/${provider}/connect/`, {
    method: 'POST', body: JSON.stringify(input),
  });
}

export function disconnectPersonalCalendar(kind: LearnerKind, id: string, provider: PersonalCalendarProvider): Promise<{ disconnected: boolean }> {
  return calendarConnectionRequest(`/learner_api/calendar-connections/${kind}/${id}/${provider}/disconnect/`, { method: 'POST' });
}

export function fetchPersonalCalendarAvailability(kind: LearnerKind, id: string, start: string, end: string): Promise<{ busy: CalendarBusySlot[]; errors: Array<{ provider: string; message: string }>; connectedProviders: PersonalCalendarProvider[] }> {
  const query = new URLSearchParams({ start, end });
  return calendarConnectionRequest(`/learner_api/calendar-connections/${kind}/${id}/availability/?${query}`);
}

/** One hour of the college day, and whether the case owner is free for it. */
export interface SessionSlot {
  /** 24-hour UK wall clock, "HH:MM" — exactly what the booking is made at. */
  time: string;
  available: boolean;
}

/** The UK offset for a day, in JavaScript's own sign convention (UTC minus
 *  local), which is what the backend reads.
 *
 *  Pinned to Europe/London rather than the browser's zone: the session happens
 *  in Kent whatever the learner's own clock says, and a learner signing in
 *  from abroad must still be offered — and book — the college's hours. */
/** UK times the learner's coach is free for a catch-up of this length on `date`. */
export async function fetchCatchupSlots(kind: LearnerKind, id: string, date: string, durationMinutes: number, signal?: AbortSignal): Promise<string[]> {
  const query = new URLSearchParams({ date, timezoneOffsetMinutes: String(ukOffsetForDate(date)), durationMinutes: String(durationMinutes), purpose: 'catch-up' });
  const response = await fetch(`${BASE}/${kind}/${id}/coach-availability/?${query}`, { credentials: 'include', signal });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'Could not check your coach’s calendar.');
  return Array.isArray(result.times) ? result.times as string[] : [];
}

/** Verified coach availability for a Review, in Europe/London wall time. */
export async function fetchReviewSlots(
  kind: LearnerKind,
  id: string,
  date: string,
  durationMinutes: number,
  options: { excludeEventKey?: string; signal?: AbortSignal } = {},
): Promise<string[]> {
  const query = new URLSearchParams({
    date,
    timezoneOffsetMinutes: String(ukOffsetForDate(date)),
    durationMinutes: String(durationMinutes),
    purpose: 'review',
  });
  if (options.excludeEventKey) query.set('excludeEventKey', options.excludeEventKey);
  const response = await fetch(`${BASE}/${kind}/${id}/coach-availability/?${query}`, {
    credentials: 'include', signal: options.signal,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'Could not check the coach calendar. Please retry.');
  return Array.isArray(result.times) ? result.times as string[] : [];
}

export function ukOffsetForDate(date: string): number {
  return (
    (12 -
      Number(
        new Intl.DateTimeFormat('en-GB', {
          timeZone: 'Europe/London',
          hour: '2-digit',
          hourCycle: 'h23',
        }).format(new Date(`${date}T12:00:00Z`)),
      )) *
    60
  );
}

/** The bookable day, and whether the case owner's calendar was actually read. */
export interface FirstSessionSlots {
  slots: SessionSlot[];
  /** Set when the calendar could not be reached — or is not linked at all.
   *
   *  The hours are then the college's own, offered unchecked: a learner must
   *  not be locked out of starting their programme because Microsoft is
   *  unwell. The form says so rather than implying the hours are confirmed. */
  unconfirmed: string;
}

/**
 * The college's working day for the learner's case owner, each hour flagged.
 *
 * `collegeDay` asks the shared availability endpoint for the first-session
 * shape — 09:00 to 16:00, on the hour — rather than the 15-minute grid of the
 * coach's own Outlook hours that the MCM picker uses. A first session is the
 * meeting that starts the programme: the learner picks from the hours the
 * college works, not from a clock.
 *
 * Every hour comes back rather than only the free ones, so a taken hour can be
 * shown as taken. An hour missing from the list entirely would read as the
 * college not working then, which is a different and wrong message.
 */
export async function fetchFirstSessionSlots(
  kind: LearnerKind,
  id: string,
  date: string,
  signal?: AbortSignal,
): Promise<FirstSessionSlots> {
  const query = new URLSearchParams({
    date,
    timezoneOffsetMinutes: String(ukOffsetForDate(date)),
    collegeDay: '1',
  });
  const response = await fetch(
    `${BASE}/${kind}/${id}/coach-availability/?${query}`,
    { credentials: 'include', signal },
  );
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error || 'Could not check your case owner’s calendar.');
  }
  // `slots` carries the whole day; `times` is the older free-only shape, kept
  // as a fallback so a frontend deployed ahead of the backend still offers
  // times rather than an empty picker.
  const slots = Array.isArray(result.slots)
    ? (result.slots as SessionSlot[])
    : ((result.times as string[]) || []).map((time) => ({ time, available: true }));
  return { slots, unconfirmed: (result.unconfirmed as string) || '' };
}

/** Where a learner stands on their first session — and whether their
 *  programme is open to them yet. */
export interface LearnerFirstSession {
  caseOwner: { name: string; email: string } | null;
  booked: boolean;
  event: LearnerCalendarEvent | null;
  /** The session date, "YYYY-MM-DD", or null when nothing is booked. */
  startsOn: string | null;
  /** 'enrolling' — an apprentice who has not finished enrolment; the first
   *  session comes after it, so nothing is held yet. 'book' — nothing booked
   *  yet. 'waiting' — booked, day not arrived. 'open' — the session day has
   *  come, so the programme runs normally. */
  access: 'enrolling' | 'book' | 'waiting' | 'open';
  /** Whether it can be booked (or already is) from the learner's First Learning
   *  Session tab — for an apprentice in Delivery, once they have signed all four
   *  compliance documents. Older servers omit it. */
  canBook?: boolean;
}

// This permission check is live rather than TTL-cached, but its transport is
// shared while it is in flight. React StrictMode tears down the first effect
// immediately in development; cancelling only that caller keeps the server
// request alive so the remount joins it instead of opening a duplicate read.
const firstSessionResource = createCachedResource<LearnerFirstSession>(
  'learner-first-session',
  async key => {
    const [kind, id] = key.split(':', 2);
    const response = await fetch(`${BASE}/${kind}/${id}/first-session/`, {
      credentials: 'include',
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.error || 'Could not check your first session.');
    }
    return result as LearnerFirstSession;
  },
  0,
);

/**
 * The learner's first-session state.
 *
 * `access` is decided by the server, not here: a learner must not be able to
 * reach their programme early by changing the clock on their own machine.
 */
export async function fetchLearnerFirstSession(
  kind: LearnerKind,
  id: string,
  signal?: AbortSignal,
): Promise<LearnerFirstSession> {
  return withCallerSignal(firstSessionResource.read(`${kind}:${id}`), signal);
}
