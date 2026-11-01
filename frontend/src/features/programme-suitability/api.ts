import type { LearnerKind } from '@/api/extendedIlr';
import type { AssessmentChanges, AssessmentState, Configuration, Pathway, ProgrammeId, Responses, Result } from './types';
import { createCachedResource } from '@/api/cachedRequest';
import { invalidateWizardCacheById } from '@/api/extendedIlr';
import { invalidateLearnerDetailCache } from '@/api/learnerDetail';

const BASE = '/enrolment_api/programme-suitability';

const csrf = createCachedResource<{ csrfToken: string }>('suitability-csrf', async () => {
  const response = await fetch('/learner_api/enrollment-csrf/', { credentials: 'include' });
  if (!response.ok) throw new Error('Could not initialise the secure assessment request. Please retry.');
  return response.json();
});

export function prefetchAssessmentSecurity() { return csrf.read('current'); }

export async function request<T>(url: string, data?: unknown, retryCsrf = true): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (data !== undefined) {
    headers['X-CSRFToken'] = (await csrf.read('current')).csrfToken;
  }
  const response = await fetch(url, { credentials: 'include', cache: 'no-store', headers,
    ...(data !== undefined ? { method: url.endsWith('/preview/') ? 'POST' : 'PUT', body: JSON.stringify(data) } : {}) });
  const text = await response.text();
  let payload;
  try { payload = JSON.parse(text); } catch { payload = null; }
  const rejectedCsrf = response.status === 403 && /csrf|origin checking failed/i.test(text);
  if (rejectedCsrf && data !== undefined && retryCsrf) {
    csrf.invalidate();
    return request<T>(url, data, false);
  }
  if (!response.ok) throw Object.assign(new Error(payload?.error || (rejectedCsrf
    ? 'The security token or page origin was rejected. Retry the request; if it continues, check the development CSRF origin configuration.'
    : response.status === 401 ? 'Your session has expired. Sign in again to continue your assessment.'
    : `The assessment request failed (${response.status}). Please retry.`)), { status: response.status });
  if (!payload || typeof payload !== 'object') throw new Error('The server returned an invalid assessment response. Please retry.');
  return payload as T;
}

export const fetchDefaults = () => request<{ configuration: Configuration }>(`${BASE}/defaults/`);
export const previewAssessment = (configuration: Configuration, pathway: Pathway, responses: Responses) =>
  request<Result>(`${BASE}/preview/`, { configuration, pathway, responses });
const assessments = createCachedResource<AssessmentState>('programme-suitability', (key) => {
  const [kind, id] = JSON.parse(key) as string[];
  return request<AssessmentState>(`${BASE}/${kind}/${id}/`);
});
const cacheKey = (kind: LearnerKind, learnerId: string, version?: number | null) => JSON.stringify([kind, learnerId, version ?? 'current']);
export const getAssessment = (kind: LearnerKind, learnerId: string, version?: number | null, force = false) =>
  assessments.read(cacheKey(kind, learnerId, version), { force });
export const peekAssessment = (kind: LearnerKind, learnerId: string, version?: number | null) => assessments.peek(cacheKey(kind, learnerId, version));
export const primeAssessment = (kind: LearnerKind, learnerId: string, version: number | null | undefined, state: AssessmentState) =>
  assessments.prime(cacheKey(kind, learnerId, version), state);
export async function saveAssessment(kind: LearnerKind, learnerId: string, state: AssessmentState,
  changes: AssessmentChanges) {
  const saved = await request<AssessmentState>(`${BASE}/${kind}/${learnerId}/`, { revision: state.revision,
    configurationVersion: state.configurationVersion, pathway: state.pathway, responses: state.responses,
    selectedProgramme: state.selectedProgramme, ...changes });
  assessments.invalidate();
  if ((changes.action === 'select' || changes.action === 'continue') && saved.selectionStatus === 'SELECTION_CONFIRMED') {
    invalidateWizardCacheById(learnerId);
    invalidateLearnerDetailCache();
  }
  return saved;
}
export const saveReview = (kind: LearnerKind, learnerId: string, revision: number, status: string,
  recommendedProgramme: ProgrammeId | '', reason: string) =>
  request<AssessmentState>(`${BASE}/${kind}/${learnerId}/review/`, { revision, status, recommendedProgramme, reason });
