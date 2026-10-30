import { readLearnerJson } from './learnerRead';
// ============================================================================
// Enrolment users API client
// Talks to the Django backend at /learner_api (proxied to :8000 by Vite in dev).
// ============================================================================
import type { UserListRow, EnrolmentBoard } from '@/pages/users/types';
import { invalidateWizardCacheById } from './extendedIlr';
import { invalidateLearnerDetailCache } from './learnerDetail';

const BASE = '/learner_api/enrolment-users';

// ---- Canonical option lists (mirror api/constants.py; validated server-side) ----
export const STATUS_OPTIONS = ['FullUser', 'Invited', 'Prospect', 'Expired', 'Cancelled', 'Archived'];

export const TYPE_OPTIONS = ['User', 'Employer', 'Referrer', 'Admin', 'Caseowner'];

// Mirrors ATTENDANCE_TYPE_CHOICES in backend/learner_api/constants.py.
export const ATTENDANCE_TYPE_OPTIONS = ['Recorded', 'Not recorded'];

// Mirrors PROGRAMME_STATUS_CHOICES in backend/learner_api/constants.py, which
// validates writes — a value missing there is rejected on save.
export const PROGRAMME_STATUS_OPTIONS = [
  // Account exists but the learner hasn't entered the enrolment flow yet. Also
  // what the backend reports when no status has been set.
  'Fresh user',
  // While a learner is at this status their landing page sends them to their own
  // enrolment wizard (/learner/onboarding) rather than the usual overview.
  'Onboarding',
  'Delivery',
  'Ready to enrol',
  'Active',
  'Withdrawn',
  'On break',
  'Completed',
];

/**
 * The Aptem "Add user" field set, shared by both learner tables — the create
 * form is the same for apprenticeship and commercial learners, and the backend
 * accepts these keys on either endpoint (see APTEM_TEXT_FIELDS/APTEM_BOOL_FIELDS
 * in backend/learner_api/mappers.py).
 */
export interface AptemUserFields {
  title?: string;
  preferredName?: string;
  gender?: string;
  legalSex?: string;
  age?: string;
  niNumber?: string;
  referrer?: string;
  referrerAddress?: string;
  referrerContact?: string;
  targetProgramme?: string;
  postcode?: string;
  address?: string;
  addressLine1?: string;
  addressLine2?: string;
  townCity?: string;
  county?: string;
  country?: string;
  caseOwner?: string;
  learningProvider?: string;
  employerAddress?: string;
  mentor?: string;
  referenceNumber?: string;
  extendedBreak?: string;
  /** One of ATTENDANCE_TYPE_OPTIONS; null clears it. */
  attendanceType?: string | null;
  allowCheckpoint?: boolean;
  allowConsole?: boolean;
  allowClassic?: boolean;
}

/**
 * Which kind of learner a row is. Both kinds live in the single
 * enrolment."Enrolment_Users" table, distinguished by this column.
 */
export type LearnerType = 'apprenticeship' | 'commercial';

export const LEARNER_TYPE_OPTIONS: LearnerType[] = ['apprenticeship', 'commercial'];

export interface CreateEnrolmentUserInput extends AptemUserFields {
  username: string;
  email: string;
  /** Defaults to 'apprenticeship' server-side when omitted. */
  learnerType?: LearnerType;
  type?: string;
  status?: string;
  programmeStatus?: string;
  learnerStartDate?: string | null;
  learnerEndDate?: string | null;
  programme?: string;
  cohort?: string;
  group?: string;
  employer?: string;
  /**
   * The employer's record id in enrolment."Employers". `employer` above is the
   * display name; this is the reference that reaches their full details. Null
   * clears it. The API rejects an id naming no employer record.
   */
  employerId?: number | null;
  organization?: string;
  lineManager?: string;
  phone?: string;
  dob?: string;
  /**
   * The first session, booked with the case owner as the learner is created.
   * Date is YYYY-MM-DD and time HH:MM, both UK business time. Create only, and
   * skipped server-side for learners imported from Aptem — they already have a
   * start date. Supplying one sets the learner's start date to that day.
   */
  firstSessionDate?: string;
  firstSessionTime?: string;
}

/** What became of the first-session booking, reported alongside the new row. */
export interface FirstSessionResult {
  /** True only when the booking itself was reserved. */
  booked: boolean;
  /** 'aptem' when the learner was skipped as an Aptem import. */
  skipped?: string;
  /** The booking exists but Microsoft has not confirmed the meeting yet. */
  warning?: string;
  error?: string;
  scheduledDate?: string;
  scheduledTime?: string;
}

/**
 * A learner row as the edit form reads it. Every key is one the PATCH endpoint
 * accepts back, so editing is a round trip rather than a mapping exercise.
 */
export interface EnrolmentUserFields extends AptemUserFields {
  id: string;
  username: string;
  email: string;
  phone: string;
  dob: string;
  type: string;
  /** Subscription status — one of STATUS_OPTIONS. */
  status: string;
  /** Programme status — one of PROGRAMME_STATUS_OPTIONS. */
  programmeStatus: string;
  programme: string;
  cohort: string;
  group: string;
  employer: string;
  employerId: number | null;
  organization: string;
  lineManager: string;
  learnerType: LearnerType;
}

async function request<T>(url: string, init?: Parameters<typeof fetch>[1]): Promise<T> {
  if (!init?.method || init.method.toUpperCase() === 'GET') return readLearnerJson<T>(url, { ...init, headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...init?.headers } });
  let res: Response;
  try {
    res = await fetch(url, {
      // Sends the kbc_session cookie — writes require an authenticated staff
      // session (see login.permissions.staff_only).
      credentials: 'include',
      ...init,
      // Spread last: with `...init` after it, a caller passing any headers at
      // all would silently drop these two, failing the Content-Type parse and
      // the CSRF check.
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        ...(init?.headers || {}),
      },
    });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(`The server returned ${res.headers.get('content-type') || 'a non-JSON response'} for ${url}.`);
    }
  }
  if (!res.ok) {
    const error = data && typeof data === 'object' && 'error' in data ? String(data.error) : '';
    throw new Error(error || `Request failed (${res.status})`);
  }
  return data as T;
}

/**
 * List learners from the single learner table. Both kinds are returned unless
 * `learnerType` narrows it — each row carries its own `learnerType`/`source`.
 */
export async function fetchEnrolmentUsers(learnerType?: LearnerType): Promise<UserListRow[]> {
  const qs = learnerType ? `?learnerType=${encodeURIComponent(learnerType)}` : '';
  const data = await request<{ count: number; results: UserListRow[] }>(`${BASE}/${qs}`);
  return data.results;
}

/** Full read-only board for a single user. */
export function fetchEnrolmentBoard(id: string): Promise<EnrolmentBoard> {
  return request<EnrolmentBoard>(`${BASE}/${id}/`);
}

/**
 * The flat, editable columns of one learner — what the edit form prefills from.
 *
 * Its own endpoint rather than a slice of the board: the board GET is not
 * staff-gated, and these fields include a national insurance number and a home
 * address. The keys match `CreateEnrolmentUserInput`, so the form can hand what
 * it reads straight back to `updateEnrolmentUser`.
 */
export function fetchEnrolmentUserFields(id: string): Promise<EnrolmentUserFields> {
  return request<EnrolmentUserFields>(`${BASE}/${id}/fields/`);
}

/** Create a user; returns the new list row. */
export function createEnrolmentUser(input: CreateEnrolmentUserInput): Promise<UserListRow> {
  return request<UserListRow>(`${BASE}/`, { method: 'POST', body: JSON.stringify(input) });
}

/** Update flat fields on a user (e.g. wizard save-back); returns the board. */
export async function updateEnrolmentUser(id: string, patch: Partial<CreateEnrolmentUserInput> & Record<string, unknown>): Promise<EnrolmentBoard> {
  const board = await request<EnrolmentBoard>(`${BASE}/${id}/`, { method: 'PATCH', body: JSON.stringify(patch) });
  // This row is half of the cached wizard bootstrap, so a stale copy would show
  // the learner their pre-edit details on the next open.
  invalidateWizardCacheById(id);
  invalidateLearnerDetailCache();
  return board;
}

/** Permanently delete the enrolment record and its linked sign-in account. */
export async function deleteEnrolmentUser(id: string): Promise<void> {
  await request<{ deleted: boolean }>(`${BASE}/${id}/`, { method: 'DELETE' });
  invalidateWizardCacheById(id);
  invalidateLearnerDetailCache();
}

/**
 * Check and complete an eligible learner's automatic activation. The server
 * only makes a learner Active after all compliance documents are signed and
 * their programme start date has arrived.
 */
export async function finishEnrolment(id: string): Promise<EnrolmentBoard> {
  const board = await request<EnrolmentBoard>(`${BASE}/${id}/finish/`, { method: 'POST' });
  // Activation changes programme status, which decides the learner's whole
  // navigation — a stale board here would keep them on the onboarding menu.
  invalidateWizardCacheById(id);
  invalidateLearnerDetailCache();
  return board;
}

/** One hour of the college day, and whether the case owner is free for it. */
export interface CaseOwnerSlot {
  /** 24-hour UK wall clock, "HH:MM" — exactly what the booking is made at. */
  time: string;
  available: boolean;
}

/**
 * The college's whole working day, each hour marked free or taken.
 *
 * The first session booked at enrolment is a real meeting on a real person's
 * calendar, so availability is what Microsoft says — their Outlook working
 * hours, minus what is already in the diary and minus sessions the LMS has
 * itself booked. This is the same `free_slots` calculation behind the MCM
 * picker (AssignmentCoachingBooking); it differs only in naming the case owner
 * directly, because at enrolment there is no learner record yet to resolve a
 * coach from.
 *
 * Every hour is returned rather than only the free ones, so the form can show
 * a taken hour as taken. An hour missing from the list entirely reads as the
 * college not working then, which is a different and wrong message.
 *
 * Offset is pinned to Europe/London rather than the browser's own zone: the
 * backend reads the chosen time as UK wall clock, so staff working from another
 * country must still be offered — and book — the college's hours.
 */
export async function fetchCaseOwnerAvailability(
  caseOwner: string,
  date: string,
  signal?: AbortSignal,
): Promise<CaseOwnerSlot[]> {
  const offset =
    (12 -
      Number(
        new Intl.DateTimeFormat('en-GB', {
          timeZone: 'Europe/London',
          hour: '2-digit',
          hourCycle: 'h23',
        }).format(new Date(`${date}T12:00:00Z`)),
      )) *
    60;
  const query = new URLSearchParams({
    caseOwner,
    date,
    timezoneOffsetMinutes: String(offset),
  });
  const response = await fetch(
    `/learner_api/calendar/case-owner-availability/?${query}`,
    { credentials: 'include', signal },
  );
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result.error || 'Could not check the case owner’s calendar.');
  }
  // `slots` carries the whole day; `times` is the older free-only shape, kept
  // as a fallback so a frontend deployed ahead of the backend still works
  // rather than showing an empty picker.
  if (Array.isArray(result.slots)) return result.slots as CaseOwnerSlot[];
  return ((result.times as string[]) || []).map((time) => ({ time, available: true }));
}
