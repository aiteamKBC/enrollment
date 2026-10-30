import { learningFetch } from '@/lib/personalLearning';
import type { SubmissionAttempt } from './assignmentAttempts';
import { readLearnerJson } from './learnerRead';
import { invalidateWeeklyProgress } from './weeklyLearning';
import type { MonthlyAssignment } from './monthlyAssignment';

export interface LearningReflectionSubmissionInput {
  learnerKind: 'commercial' | 'apprenticeship';
  learnerId: string;
  learnerName: string;
  programmeName: string;
  activityType: string;
  activityId: string;
  activityTitle: string;
  moduleTitle: string;
  weekTitle: string;
  plannedOtjh: string;
  learningReflection: string;
  ksbCodes: string[];
  ksbWeights: Record<string, number>;
  ksbExplanations: Record<string, string>;
  confidenceBefore: Record<string, number>;
  confidenceAfter: Record<string, number>;
  applicationType: string;
  applicationText: string;
  evidenceFiles: string[];
  evidenceConsentConfirmed: boolean;
  selectedBenefits: string[];
  benefitExplanation: string;
  actualTimeHours: string;
  /** A corrected reflection duration takes precedence when restoring a draft. */
  reflectionTimeEdited?: boolean;
  completedDuringPaidHours: string;
  dateCompleted: string;
  otjhConfirmed: boolean;
  signedDeclaration: boolean;
  qualityScore: number;
  /** Assignment wizard fields. Other reflection flows omit these. */
  submissionMode?: 'draft' | 'submit';
  assignmentAnswer?: string;
  whatYouLearned?: string;
  businessImpact?: string;
  outsideWorkingHours?: boolean;
  insideWorkingHoursConfirmed?: boolean;
  insideWorkingHoursConfirmedAt?: string | null;
  outsideWorkingHoursConfirmed?: boolean;
  monthlyAssignment?: MonthlyAssignment;
  assignmentTimeSource?: 'timer' | 'input';
  assignmentTopicId?: string;
  assignmentTopicName?: string;
  assignmentQuestion?: string;
  assignmentElapsedSeconds?: number;
}

export interface HistoricalAssignmentContent {
  method: string;
  notices: string[];
  cards: Array<{
    title: string;
    emptyMessage: string;
    sections: Array<{ label: string; text: string; source: string; kind: 'original' | 'feedback' | 'record' }>;
  }>;
}

export interface StoredLearningReflectionSubmission extends LearningReflectionSubmissionInput {
  submissionAttempts?: SubmissionAttempt[];
  /** Server-owned provenance; learners cannot request the import exemption. */
  submissionOrigin?: 'learner' | 'imported_legacy' | 'classified_legacy';
  legacyAssignment?: {
    content?: HistoricalAssignmentContent;
    aptemLearnerId: number;
    componentId: number;
    evidenceIds: number[];
    sourceStatus: string;
    documents: Array<{ evidenceId: number; part: 'file' | 'report'; name: string }>;
    feedbacks?: Array<{ author?: string; date?: string; message?: string }>;
    lmsReviews?: Array<{ decision: string; feedback: string; reviewedBy: string; reviewedAt: string }>;
  };
  id: string;
  status: string;
  coachFeedback: string | null;
  reviewedBy: string | null;
  reviewedAt: string | null;
  submittedAt: string | null;
  locked: boolean;
}

export type LearningReflectionStatusMap = Record<string, string>;

export function learningReflectionStatusKey(activityType: string, activityId: string): string {
  return `${activityType}:${activityId}`;
}

/** Load every reflection status for a learner in one query. Dashboard cards
 * only need the status badge, not each submission's full seven-step payload. */
export async function loadLearningReflectionStatuses(input: {
  learnerKind: 'commercial' | 'apprenticeship';
  learnerId: string;
}, options: { signal?: AbortSignal; revalidate?: boolean } = {}): Promise<LearningReflectionStatusMap> {
  const params = new URLSearchParams(input);
  const data = await readLearnerJson<{ statuses?: Array<{ activityType: string; activityId: string; status: string }> }>(
    `/learner_api/reflection/submissions/?${params.toString()}`,
    options,
  );
  return Object.fromEntries(
    (data?.statuses || []).map((row) => [
      learningReflectionStatusKey(row.activityType, row.activityId),
      row.status,
    ]),
  );
}

export async function loadLearningReflectionSubmission(input: {
  learnerKind: 'commercial' | 'apprenticeship';
  learnerId: string;
  activityType: string;
  activityId: string;
  attempt?: string;
  assignmentTopicId?: string;
}): Promise<StoredLearningReflectionSubmission | null> {
  const params = new URLSearchParams(input);
  const data = await readLearnerJson<{ submission?: StoredLearningReflectionSubmission | null }>(
    `/learner_api/reflection/submissions/?${params.toString()}`,
    input.attempt === undefined ? undefined : { revalidate: true },
  );
  return data.submission || null;
}

export async function saveLearningReflectionSubmission(
  input: LearningReflectionSubmissionInput,
): Promise<{ id: string; status: string; submissionAttempts?: SubmissionAttempt[] }> {
  let response: Response;
  try {
    response = await learningFetch('/learner_api/reflection/submissions/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
  } catch {
    throw new Error('Could not reach the server to save this reflection.');
  }

  const text = await response.text();
  let data: { id?: string; status?: string; error?: string; submissionAttempts?: SubmissionAttempt[] };
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Unexpected response (${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(data.error || `Could not save the reflection (${response.status}).`);
  }
  invalidateWeeklyProgress(input.learnerKind, input.learnerId, input.activityId);
  return { id: data.id || '', status: data.status || '', submissionAttempts: data.submissionAttempts };
}
