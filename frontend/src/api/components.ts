import { learningFetch } from '@/lib/personalLearning';
// ============================================================================
// Generic component-completion API client.
// Records that a learner finished a non-quiz, non-video component (podcast,
// reading, slide deck, reflection, activity, …) + their post-completion
// reflection. POSTs to /learner_api/components/<componentId>/complete/.
// ============================================================================

import { invalidateLearnerDetailCache } from '@/api/learnerDetail';
import { CompletionValidationError, type WorkingRuleReason } from '@/lib/completionValidation';

const BASE = '/learner_api/components';

export interface ComponentProgressSubmission {
  assignmentTopicId?: string;
  week?: string | null;
  module?: string | null;
  startedAt: string;
  timeTakenSeconds: number;
  timeEntrySource?: 'timer' | 'input';
  insideWorkingHoursConfirmed?: boolean;
  insideWorkingHoursConfirmedAt?: string | null;
  outsideWorkingHoursConfirmed?: boolean;
  /** The working instant the learner declared after their Finish click was
   *  refused. Omitted on a first, already-valid attempt. */
  declaredCompletedAt?: string | null;
  trackingToken: string;
  componentTitle?: string | null;
  componentType?: string | null;
  ksbs?: string[];
  feedback?: string;
  reportedTime?: string;
  /** Planned duration stays separate from the selected actual time. */
  plannedOtjh?: string;
  skipReflection?: boolean;
}

export interface ComponentProgressRecord {
  kind: 'component';
  componentType: string;
  componentId: string;
  attempt: number;
  ksbs: string[];
  feedback: string;
  reportedTime: string;
  reflectionSkipped?: boolean;
  startedAt: string | null;
  submittedAt: string;
  timeTaken: string | null;
  timeTrackingSource: string;
  claimedSeconds: number;
  serverSessionSeconds: number;
  verifiedSeconds: number;
  outsideWorkingHours?: boolean;
  insideWorkingHoursConfirmed?: boolean;
  insideWorkingHoursConfirmedAt?: string | null;
  outsideWorkingHoursConfirmed?: boolean;
  outsideWorkingHoursConfirmedAt?: string | null;
  declaredCompletedAt?: string | null;
  submissionValidationReason?: string;
}

export interface ComponentProgressResponse {
  record: ComponentProgressRecord;
  componentTitle: string;
  componentType: string;
  week: string | null;
  module: string | null;
}

async function request<T>(url: string, init?: globalThis.RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await learningFetch(url, { headers: { 'Content-Type': 'application/json' }, ...init });
  } catch {
    throw new Error('Could not reach the server. Is the backend running on port 8000?');
  }
  const text = await res.text();
  let data: { error?: string; validation?: { reason?: string; holidayName?: string } } | null = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`The server returned an unexpected response (${res.status}).`);
  }
  if (!res.ok) {
    const message = (data && data.error) || `Request failed (${res.status})`;
    // 409 + validation is the working-rules refusal: nothing was written, and
    // the caller opens the completion date/time dialog rather than reporting a
    // failure. Any other 409 stays an ordinary error.
    if (res.status === 409 && data && data.validation) {
      throw new CompletionValidationError(
        message,
        (data.validation.reason || '') as WorkingRuleReason | '',
        data.validation.holidayName || '',
      );
    }
    throw new Error(message);
  }
  return data as T;
}

/** Record a completed component + reflection for a learner. */
export function submitComponentProgress(
  componentId: string,
  kind: 'commercial' | 'apprenticeship',
  learnerId: string,
  submission: ComponentProgressSubmission,
): Promise<ComponentProgressResponse> {
  return request<ComponentProgressResponse>(
    `${BASE}/${encodeURIComponent(componentId)}/complete/?kind=${kind}&learnerId=${learnerId}`,
    { method: 'POST', body: JSON.stringify(submission) },
  ).then((result) => {
    invalidateLearnerDetailCache(kind, learnerId, { activityId: componentId });
    return result;
  });
}
