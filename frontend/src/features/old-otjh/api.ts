import type { QuizAttempt } from '@/features/audit/learner-log-pro-manual/lib/api';
import { LearnerReadError, readLearnerJson, invalidateLearnerReads } from '@/api/learnerRead';

export type Signature = { signed_at: string; signer_name: string; url: string };
export type SignatureCaptureMethod = 'draw' | 'upload' | 'import';
export type MonthState = {
  is_required?: boolean;
  provisional?: boolean;
  month: string;
  status: 'awaiting_signature' | 'needs_review' | 'student_signed' | 'awaiting_coach' | 'ready_to_complete' | 'complete' | 'no_data';
  row_count: number;
  planned_hours: number | string;
  actual_hours: number | string;
  estimated_hours?: number | string;
  not_accepted_hours: number | string;
  total_actual_hours?: number | string;
  training_plan_target?: number | string | null;
  student_signature: Signature | null;
  coach_signature: Signature | null;
  locked?: boolean;
  locked_at?: string | null;
  can_unlock?: boolean;
  pending_revisions: number;
  can_complete: boolean;
  source_finalization: { event_type: string; created_at: string } | null;
};
export type Summary = {
  is_legacy: boolean;
  state: string;
  can_access_lms: boolean;
  needs_start?: boolean;
  read_only?: boolean;
  cutoff_date?: string;
  learner?: { id: number; aptem_id: number; name: string; programme: string; coach_name: string; coach_email?: string | null; coach_booking_url?: string | null };
  total_months?: number;
  completed_months?: number;
  completed_at?: string | null;
  months: MonthState[];
  additional_source_months?: string[];
  message?: string;
};
export type Activity = {
  id: number; category: string; title: string; activity_date: string | null;
  activity_time: string | null; planned_hours: string | number; actual_hours: string | number;
  actual_pending?: boolean;
  actual_estimated?: boolean;
  actual_status_label?: string;
  provisional?: boolean;
  provisional_fields?: string[];
  provisional_source?: string | null;
  timestamp_label: string; completion_note: string | null; accepted: boolean;
  source_ref?: string | null;
  group_name?: string | null;
  component_name?: string | null;
  duration_minutes?: number | string | null;
  page_count?: number | null;
  ksb_codes?: string[];
  documents: { id: number; display_name: string; content_type: string | null; url: string }[];
  results: { activity_id: number; group_id: number; status: string | null; quiz_score: string | null;
    quiz_maximum_score: string | null; quiz_passed: boolean | null; quiz_attempt_number: number | null }[];
};
export type MonthDetail = MonthState & { rows: Activity[]; snapshot_digest: string;
  profile?: { start_date: string | null; planned_end_date: string | null; first_evidence_date: string | null };
  demo_only?: boolean; provisional?: boolean };
/** Presentation data shared by retained and current monthly journals. */
export type JournalSummary = {
  learner?: { id: number; aptem_id?: number | null; name: string; programme: string; coach_name: string;
    planned_end_date?: string | null };
  months: MonthState[];
};
export type ActivityContent = { id: number; parts: { id: number; title: string; category: string;
  content_type?: string | null;
  url: string | null; html: string | null; quiz: { state: string; attempt: QuizAttempt | null; answers_available?: boolean;
    definition?: { description: string | null; questions: { question_id: number; question_order: number;
      question_text: string; answer_options: { option_text: string }[] }[] } | null } | null;
  document?: Activity['documents'][number] | null;
  available?: boolean; issue?: string | null }[] };
export type ContentReview = { ready: boolean; snapshot_digest: string; issues: { id: number; title: string; category: string; reason: string }[] };
export type LearnerList = { learners: { id: number; name: string; programme: string }[]; total: number; page: number; page_size: number };
export type SigningReview = { month: string; ready: boolean; reason?: string | null; snapshot_digest?: string; issues?: ContentReview['issues'] };
export type SignedMonths = { signed_months: string[]; skipped_months: string[]; completed_months: string[]; summary: Summary };

import { coachViewAs } from '@/lib/coachViewAs';

const BASE = '/audit_api';
export const CONTACT_COACH = 'Please contact your coach to complete the review and signing of your previous learning record before entering the LMS.';

export class RecordError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function request<T>(path: string, init: globalThis.RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== 'GET') {
    const token = await request<{ csrfToken: string }>('/old-otjh/csrf/');
    headers.set('X-CSRFToken', token.csrfToken);
  }
  headers.set('X-Requested-With', 'XMLHttpRequest');
  if (!init.method || init.method === 'GET') {
    try {
      return await readLearnerJson<T>(`${BASE}${path}`, { ...init, headers });
    } catch (error) {
      if (error instanceof LearnerReadError) throw new RecordError(error.message, error.status, error.code);
      throw error;
    }
  }
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...init, headers, credentials: 'include', cache: 'no-store' });
  } catch {
    throw new RecordError('Could not load your previous record. Check your connection and try again.', 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok || body === null) {
    throw new RecordError(body?.error || 'Previous learning records are temporarily unavailable.', response.status, body?.code);
  }
  invalidateLearnerReads();
  // This only requests a new server check; the event never grants access.
  window.dispatchEvent(new Event('previous-record-updated'));
  return body as T;
}

/** The coach whose workspace an admin currently has open, if any.
 *
 * These routes scope on the signed-in account, so an administrator reached them
 * as an administrator and saw the whole cohort -- under a banner naming one
 * coach. Passing the selection lets the server narrow the caseload to that
 * coach. It can only ever narrow: the server pins a coach to their own email
 * and ignores the parameter (see old_otjh.views._coach_cohort). */
const viewAsParams = (params: URLSearchParams) => {
  const selection = coachViewAs();
  if (selection) params.set('viewAsCoach', selection.email);
  return params;
};

const query = (aptemId?: number, month?: string) => {
  const params = new URLSearchParams({ transition: '1' });
  if (aptemId !== undefined) params.set('aptem_id', String(aptemId));
  if (month) params.set('month', month);
  return `?${viewAsParams(params)}`;
};
const post = <T>(path: string, body: object) => request<T>(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const getSummary = (aptemId?: number) => request<Summary>(aptemId === undefined
  ? '/old-otjh/me/summary/' : `/last-audit/manual/summary${query(aptemId)}`);
export const startReview = (aptemId?: number) => post<Summary>(`/old-otjh/start/${query(aptemId)}`, {});
export const getWorkspaceRecordLink = (learnerId?: string) => request<{ href: string | null }>(
  `/old-otjh/workspace-link/${learnerId === undefined ? '' : `?learner_id=${encodeURIComponent(learnerId)}`}`,
);
export const getMonth = (month: string, aptemId?: number, signal?: AbortSignal) => request<MonthDetail>(`/last-audit/manual/rows${query(aptemId, month)}`, { signal });
export const getActivityContent = (month: string, rowId: number, aptemId?: number) =>
  request<ActivityContent>(`/last-audit/manual/rows${query(aptemId, month)}&activity_id=${rowId}`);
export const getContentReview = (month: string, aptemId?: number) => request<ContentReview>(`/old-otjh/content-check/${query(aptemId, month)}`);
export const getSignoffs = (aptemId: number, month: string) => request<{ month: string; signoffs: { learner: Signature | null; coach: Signature | null } }>(`/learners/${aptemId}/signoff/${query(aptemId, month)}`);
export const getSigningReview = (month: string, aptemId?: number, signal?: AbortSignal) => request<SigningReview>(`/old-otjh/sign-months/${query(aptemId, month)}`, { signal });
export const getLearners = (page: number, search = '') => {
  // The endpoint already filters on name and email (see
  // audit_api.last_audit_ledger_views.cohort); the client simply never passed
  // it, so a coach with 42 records had to page through them to find somebody.
  const params = new URLSearchParams({ transition: '1', page: String(page) });
  if (search.trim()) params.set('search', search.trim());
  return request<LearnerList>(`/last-audit/cohort/?${viewAsParams(params)}`);
};
export const completeMonth = (month: string) => post<MonthDetail>(`/last-audit/manual/finalization${query()}`, { month, action: 'complete' });
export const reopenMonth = (month: string, aptemId: number, reason: string) => post<MonthDetail>(`/last-audit/manual/finalization${query(aptemId)}`, { month, action: 'reopen', reason });
export const refreshMonths = (aptemId: number, reason: string) => post<Summary>(`/old-otjh/refresh-months/${query(aptemId)}`, { reason });

export function saveSignature(aptemId: number, month: string, digest: string, blob: Blob, capture: SignatureCaptureMethod) {
  const form = new FormData();
  form.set('signature', blob, blob.type === 'image/jpeg' ? 'signature.jpg' : 'signature.png');
  form.set('month', month);
  form.set('snapshot_digest', digest);
  form.set('confirmed', 'true');
  form.set('capture_method', capture);
  return request<MonthDetail>(`/learners/${aptemId}/signoff/${query()}`, { method: 'POST', body: form });
}

export function saveMonthSignatures(months: string[], blob: Blob, capture: SignatureCaptureMethod, aptemId?: number) {
  const form = new FormData();
  form.set('signature', blob, blob.type === 'image/jpeg' ? 'signature.jpg' : 'signature.png');
  form.set('months', JSON.stringify(months));
  form.set('confirmed', 'true');
  form.set('capture_method', capture);
  return request<SignedMonths>(`/old-otjh/sign-months/${query(aptemId)}`, { method: 'POST', body: form });
}
