import { readLearnerJson } from './learnerRead';
// ============================================================================
// delivery API client
// Talks to the Django backend at /learner_api (proxied to :8000 by Vite in dev).
// Backs enrolment."Commercial_users".
// ============================================================================

import type { TrainingPlan } from './trainingPlan';
import type { EnrolmentBoard } from '@/pages/users/types';
import type { AptemUserFields } from './enrolmentUsers';
import { invalidateWizardCacheById } from './extendedIlr';
import { invalidateLearnerDetailCache } from './learnerDetail';

const BASE = '/learner_api/commercial-users';
const UNIFIED_ENROLMENT_BASE = '/learner_api/enrolment-users';

export interface CommercialUserRow extends AptemUserFields {
  id: string;
  username: string;
  /** Type/Status columns this table gained with the shared Aptem create form. */
  type?: string;
  status?: string;
  dob?: string;
  email: string;
  phone: string;
  employer: string;
  lineManager: string;
  organization: string;
  programmeStatus: string;
  programme: string;
  cohort: string;
  group: string;
  /** @deprecated legacy comma-joined summaries — use trainingPlan instead */
  modules: string;
  /** @deprecated legacy comma-joined summaries — use trainingPlan instead */
  weeks: string;
  /** @deprecated legacy comma-joined summaries — use trainingPlan instead */
  components: string;
  trainingPlan: TrainingPlan;
}

export interface CreateCommercialUserInput extends AptemUserFields {
  username: string;
  email: string;
  phone?: string;
  employer?: string;
  lineManager?: string;
  organization?: string;
  programmeStatus?: string;
  /** Commercial_users gained these columns so both tables share one create form. */
  type?: string;
  status?: string;
  dob?: string;
  programme?: string;
  cohort?: string;
  group?: string;
}

export interface CommercialProgrammeInput {
  programme?: string;
  cohort?: string;
  group?: string;
  trainingPlan?: TrainingPlan;
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

/** List all commercial users (mapped to CommercialUserRow). */
export async function fetchCommercialUsers(): Promise<CommercialUserRow[]> {
  const data = await request<{ count: number; results: CommercialUserRow[] }>(`${BASE}/`);
  return data.results;
}

/** Step 1: create the commercial user's details. */
export function createCommercialUser(input: CreateCommercialUserInput): Promise<CommercialUserRow> {
  return request<CommercialUserRow>(`${BASE}/`, { method: 'POST', body: JSON.stringify(input) });
}

/** Fetch a single commercial user. */
export function fetchCommercialUser(id: string): Promise<CommercialUserRow> {
  return request<CommercialUserRow>(`${BASE}/${id}/`);
}

/** Step 2: attach programme details to the previously created commercial user. */
export function updateCommercialProgramme(id: string, patch: CommercialProgrammeInput): Promise<CommercialUserRow> {
  return request<CommercialUserRow>(`${BASE}/${id}/`, { method: 'PATCH', body: JSON.stringify(patch) });
}

/**
 * Wizard board for a commercial learner, projected onto the EnrolmentBoard
 * shape by enrolment_api. Sections the commercial table has no columns for come
 * back empty, so the wizard renders those steps blank instead of erroring.
 */
export function fetchCommercialBoard(id: string): Promise<EnrolmentBoard> {
  return request<EnrolmentBoard>(`${UNIFIED_ENROLMENT_BASE}/${id}/`);
}

/** Save wizard edits for a commercial learner (flat columns + training plan). */
export async function updateCommercialBoard(id: string, patch: Record<string, unknown>): Promise<EnrolmentBoard> {
  const board = await request<EnrolmentBoard>(`${UNIFIED_ENROLMENT_BASE}/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
  // Same row the cached wizard bootstrap carries — see updateEnrolmentUser.
  invalidateWizardCacheById(id);
  invalidateLearnerDetailCache();
  return board;
}

/** Patch any writable commercial-user fields (e.g. status). */
export function updateCommercialUser(
  id: string,
  patch: Partial<CreateCommercialUserInput> & CommercialProgrammeInput,
): Promise<CommercialUserRow> {
  return request<CommercialUserRow>(`${BASE}/${id}/`, { method: 'PATCH', body: JSON.stringify(patch) });
}
