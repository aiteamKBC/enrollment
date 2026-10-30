import { readLearnerJson, peekLearnerJson, invalidateLearnerReads } from './learnerRead';
import type { LearnerKind } from './learnerDetail';
import { invalidateWeeklyProgress } from './weeklyLearning';

export interface StudentActivityItem {
  activity_id: string;
  source_activity_id: number;
  catalogue_kind?: string;
  can_open_material?: boolean;
  /** Exact owned parent material for an imported companion quiz. */
  source_material_activity_id?: string;
  /** Original course section. Upload dates must not split or merge its contents. */
  source_section?: { id: string; title: string; order: number | null; component_order: number | null };
  /** Canonical evidence links retained when several results share a component. */
  record_ids?: string[];
  group_id: number;
  group_name: string | null;
  date: string | null;
  category: string;
  activity: string;
  status: string | null;
  completed: boolean;
  actual: number;
  planned: number;
  planned_hours_mapped: boolean;
  hours_mapped: boolean;
  quiz_score: number | null;
  quiz_maximum_score: number | null;
  source_date?: string | null;
  date_source?: string;
  date_needs_review?: boolean;
  section_title?: string;
  month?: string;
  week_start?: string | null;
  week_end?: string | null;
  due_timing?: string | null;
  position?: number;
  has_result?: boolean;
  video_started?: boolean | null;
  reading_viewed?: boolean | null;
  quiz_attempted?: boolean | null;
  best_score_percent?: number | null;
  historical_completed?: boolean;
  new_attempt_count?: number;
}

export interface DirectOtjhActivity {
  kind: string;
  componentId?: string | null;
  quizId?: string | number | null;
  componentTitle?: string | null;
  componentType?: string | null;
  moduleTitle?: string | null;
  weekTitle?: string | null;
  reportedTime?: string | null;
  claimedSeconds?: number | null;
  verifiedSeconds?: number | null;
  timeTrackingSource?: string | null;
  expectedOtjh?: number | null;
  submittedAt?: string | null;
  passed?: boolean | null;
}

export interface CanonicalOtjhActivity {
  id: string;
  kind: string;
  componentId?: string | null;
  quizId?: string | number | null;
  componentTitle: string;
  componentType: string;
  moduleTitle?: string | null;
  actualSeconds: number;
  expectedOtjh?: number | null;
  submittedAt?: string | null;
  passed?: boolean | null;
  ksbs: string[];
}

export interface StudentActivityResponse {
  progress_basis?: 'recorded_activities' | 'catalogue_activities';
  learner_name: string;
  count: number;
  unique_activity_count: number;
  module_count: number;
  completed_count: number;
  actual_total: number | null;
  /** Historical subject time plus later direct-platform OTJH, across every
   * subject represented in the learner's combined workspace. */
  recorded_otjh_total?: number | null;
  /** Whole-programme figures from the same sources as Audit learner search. */
  audit_tp_planned?: number | null;
  audit_lms_actual?: number | null;
  /** Distinct KSB codes evidenced in the audit mapping. A count, not a
   *  percentage: the mapping spans several standards, so it carries no
   *  per-learner denominator to divide by. */
  audit_ksb_evidenced?: number | null;
  direct_otjh_activities?: DirectOtjhActivity[];
  /** Complete accepted ledger from Learner.learner_progress_entries. When
   * present it replaces mixed legacy/direct activity-log projections. */
  canonical_otjh_activities?: CanonicalOtjhActivity[];
  /** Accepted old/current ledger union for explicitly verified course successors. */
  replacement_hours?: Record<string, { course_ref: string; accepted_hours: number }>;
  planned_total: number | null;
  mapped_count: number;
  planned_mapped_count: number;
  activities: StudentActivityItem[];
  source_status?: 'live' | 'historical';
  verification_status?: 'verified' | 'unavailable';
  activity_sources?: Record<string, { module_id: string; group_id: number; activity_id: number }>;
  activity_source_issues?: Record<string, 'missing_source_component_id' | 'missing_group_id_activity_id' | 'ambiguous_lineage'>;
  subjects?: { id: number; name: string; module_id?: string | null; catalogue_count?: number; accepted_hours?: number }[];
  covers?: Record<string, string>;
}

export interface SubjectQuiz {
  id: string;
  body: string;
  ready: boolean;
  message: string;
  passing_percent: number | null;
  questions: { id: string; text: string; type: string; options: { id: string; text: string }[] }[];
}

export interface SubjectAttemptResult {
  score_percent: number | null;
  passed: boolean | null;
  completed: boolean;
  study_time?: { startedAt: string; submittedAt: string; verifiedSeconds: number; declaredCompletedAt?: string | null };
}

export interface SubjectMaterial {
  title: string;
  reading_html: string;
  media: { kind: string; url: string; title: string; can_embed?: boolean; file_name?: string }[];
  quiz: SubjectQuiz | null;
  has_reading: boolean;
  available: boolean;
  source_live: boolean;
  unavailable_attachments?: string[];
  persistence_ready: boolean;
  can_attempt: boolean;
  completed?: boolean;
  csrf_token: string;
  history: { id: string; score_percent: number | null; passed: boolean | null; completed: boolean;
    study_time?: SubjectAttemptResult['study_time'];
    submitted_at: string; answers: Record<string, string[]>;
    answer_review?: { question: string; selected: string[]; correct: boolean }[] }[];
  historical: { score: number | null; maximum_score: number | null; passed: boolean | null;
    attempt_number: number | null; status: string | null;
    answers: { question_id: number; question_body: string; learner_answer: unknown; is_correct: boolean }[] };
}

type SubjectRequestOptions = NonNullable<Parameters<typeof fetch>[1]> & { revalidate?: boolean };

export async function subjectRequest<T>(url: string, options: SubjectRequestOptions = {}): Promise<T> {
  if (!options.method || options.method.toUpperCase() === 'GET') {
    const cacheable = url.includes('/subject-covers/');
    return readLearnerJson<T>(url, { ...options, ttlMs: cacheable ? 30_000 : 0 });
  }
  const response = await fetch(url, { credentials: 'include', cache: 'no-store', ...options });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw Object.assign(new Error(body?.error || `Request failed (${response.status})`),
    { status: response.status, validation: body?.validation });
  if (body == null) throw new Error('The server returned an invalid response.');
  const identity = new URL(url, window.location.origin).pathname.match(/^\/learner_api\/student-activity\/(commercial|apprenticeship)\/([^/]+)\//);
  if (identity) invalidateWeeklyProgress(identity[1] as LearnerKind, decodeURIComponent(identity[2]));
  else invalidateLearnerReads();
  return body as T;
}

export async function fetchStudentActivity(kind: LearnerKind, learnerId: string, signal?: AbortSignal, force = false, view?: 'my-learning'): Promise<StudentActivityResponse> {
  const payload = await readLearnerJson<StudentActivityResponse>(
    `/learner_api/student-activity/${kind}/${learnerId}/${view ? '?view=my-learning' : ''}`,
    { signal, headers: { Accept: 'application/json' }, ttlMs: 30_000, force },
  );
  if (!Array.isArray(payload.activities)) {
    invalidateLearnerReads();
    throw new Error('Received an invalid student activity response.');
  }
  return payload;
}

export function peekStudentActivity(kind: LearnerKind, learnerId: string, view?: 'my-learning'): StudentActivityResponse | undefined {
  return peekLearnerJson(`/learner_api/student-activity/${kind}/${learnerId}/${view ? '?view=my-learning' : ''}`, { headers: { Accept: 'application/json' } });
}
