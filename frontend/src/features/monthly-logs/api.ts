import { readLearnerJson, invalidateLearnerReads } from '@/api/learnerRead';
import { coachViewAs } from '@/lib/coachViewAs';
import type { ActivityContent, JournalSummary, MonthDetail, MonthState, SignatureCaptureMethod } from '@/features/old-otjh/api';

export type LogMonth = MonthState & { source: 'legacy' | 'lms'; is_open?: boolean; target_warning?: string | null };
export type LogDetail = MonthDetail & { source: 'legacy' | 'lms'; is_open?: boolean; target_warning?: string | null; demo_only?: boolean };
export type LogSummary = Omit<JournalSummary, 'months'> & {
  months: LogMonth[]; total_months: number; completed_months: number; read_only: boolean; csrf_token: string;
  training_plan_totals?: { accepted_hours: number; planned_hours: number | null };
};
/** The per-month hour totals the dashboard reads; a LogSummary satisfies it. */
export type MonthlyLogHours = {
  learner?: { aptem_id?: number | null; planned_end_date?: string | null } | null;
  months: Pick<LogMonth, 'month' | 'source' | 'training_plan_target' | 'not_accepted_hours' | 'actual_hours'>[];
  training_plan_totals?: LogSummary['training_plan_totals'];
};
export type LogLearner = { id: number; name: string; programme: string };
export type LogPerspective = 'learner' | 'coach';

function url(path: string, perspective: LogPerspective, month?: string, workflow?: string) {
  const params = new URLSearchParams();
  params.set('perspective', perspective);
  const queryWorkflow = workflow ?? new URLSearchParams(window.location.search).get('workflow');
  if (queryWorkflow) params.set('workflow', queryWorkflow);
  if (month) params.set('month', month);
  const selected = perspective === 'coach' ? coachViewAs() : null;
  if (selected) params.set('viewAsCoach', selected.email);
  return `/learner_api/monthly-logs/${path}${params.size ? `?${params}` : ''}`;
}
const read = <T,>(path: string, signal?: AbortSignal, perspective: LogPerspective = 'coach', month?: string, workflow?: string) => readLearnerJson<T>(url(path, perspective, month, workflow), { signal, ttlMs: 0 });
export const getLogSummary = (id: string, signal?: AbortSignal, perspective: LogPerspective = 'coach', month?: string, workflow?: string) => read<LogSummary>(`${id}/`, signal, perspective, month, workflow);
export const getLogMonth = (id: string, month: string, signal?: AbortSignal, perspective: LogPerspective = 'coach', demo = false, workflow?: string) =>
  read<LogDetail>(`${id}/${month}/${demo ? '?demo=1' : ''}`, signal, perspective, undefined, workflow);
export async function getLogContent(id: string, month: string, rowId: number, perspective: LogPerspective = 'coach', workflow?: string) {
  const content = await read<ActivityContent>(`${id}/${month}/activities/${rowId}/`, undefined, perspective, undefined, workflow);
  return { ...content, parts: content.parts.map(part => ({ ...part,
    url: part.url?.startsWith('/') && !part.url.startsWith('//') ? new URL(part.url, window.location.origin).href : part.url,
  })) };
}
export const getLogLearners = () => read<{ learners: LogLearner[] }>('learners/');

export async function completeLogMonth(id: string, month: string, csrfToken: string, perspective: LogPerspective = 'learner') {
  const response = await fetch(url(`${id}/${month}/complete/`, perspective), {
    method: 'POST', credentials: 'include', headers: { 'X-CSRFToken': csrfToken },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(data?.error || 'Could not complete this month. Please try again.');
  invalidateLearnerReads();
  return data as LogDetail;
}

export async function signLogMonth(id: string, month: string, digest: string, blob: Blob, capture: SignatureCaptureMethod, csrfToken: string, perspective: LogPerspective = 'coach') {
  const form = new FormData();
  form.set('signature', blob, 'signature.png');
  form.set('snapshot_digest', digest);
  form.set('confirmed', 'true');
  form.set('capture_method', capture);
  const response = await fetch(url(`${id}/${month}/sign/`, perspective), { method: 'POST', credentials: 'include',
    headers: { 'X-CSRFToken': csrfToken }, body: form });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(data?.error || 'Could not save your signature. Please try again.');
  invalidateLearnerReads();
  return data as LogDetail;
}

export async function unlockLogMonth(id: string, month: string, csrfToken: string, perspective: LogPerspective = 'learner') {
  const response = await fetch(url(`${id}/${month}/unlock/`, perspective), {
    method: 'POST', credentials: 'include', headers: { 'X-CSRFToken': csrfToken },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(data?.error || 'Could not unlock this monthly log.');
  invalidateLearnerReads();
  return data as LogDetail;
}
