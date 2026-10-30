import { learningFetch } from '@/lib/personalLearning';
// ============================================================================
// Video-progress API client.
// Records that a learner watched a video component + their post-watch reflection.
// POSTs to /learner_api/videos/<componentId>/complete/ (proxied to :8000 in dev).
// ============================================================================

import { invalidateLearnerDetailCache } from '@/api/learnerDetail';
import { CompletionValidationError, type WorkingRuleReason } from '@/lib/completionValidation';

const BASE = '/learner_api/videos';

export interface VideoProgressSubmission {
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
  videoTitle?: string | null;
  ksbs?: string[];
  feedback?: string;
  reportedTime?: string;
  skipReflection?: boolean;
}

// Slim stored record (references the video by componentId; no name fields).
export interface VideoProgressRecord {
  kind: 'video';
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

// The full submit response: the slim record + display fields (not stored).
export interface VideoProgressResponse {
  record: VideoProgressRecord;
  videoTitle: string;
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

/** Record a completed video watch + reflection for a learner. */
export function submitVideoProgress(
  componentId: string,
  kind: 'commercial' | 'apprenticeship',
  learnerId: string,
  submission: VideoProgressSubmission,
): Promise<VideoProgressResponse> {
  return request<VideoProgressResponse>(
    `${BASE}/${encodeURIComponent(componentId)}/complete/?kind=${kind}&learnerId=${learnerId}`,
    { method: 'POST', body: JSON.stringify(submission) },
  ).then((result) => {
    invalidateLearnerDetailCache(kind, learnerId, { activityId: componentId });
    return result;
  });
}
