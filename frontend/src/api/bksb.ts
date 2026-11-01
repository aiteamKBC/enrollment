import type { LearnerKind } from './extendedIlr';

export type InitialAssessmentSubject = 'english' | 'maths';
export type InitialAssessmentStatus = 'not_started' | 'in_progress' | 'completed' | 'error';

export interface InitialAssessmentAttempt {
  sessionId: string;
  status: string;
  startedAt?: string | null;
  completedAt?: string | null;
}

export interface InitialAssessment {
  subject: InitialAssessmentSubject;
  status: InitialAssessmentStatus;
  accessStatus?: 'not_ready' | 'ready';
  bksbAccountReady?: boolean;
  courseSubject?: string | null;
  courseName?: string | null;
  result?: string | null;
  level?: string | null;
  assessmentDate?: string | null;
  lastSyncedAt?: string | null;
  error?: string | null;
  attempts?: InitialAssessmentAttempt[];
  directLaunchEnabled?: boolean;
}

export interface InitialAssessmentsResponse {
  enabled: boolean;
  assessments: InitialAssessment[];
}

export interface InitialAssessmentLaunchResponse {
  subject: InitialAssessmentSubject;
  kind: 'assessment' | 'report';
  url: string;
  expiresAt?: string | null;
}

export interface BksbAutoLoginResponse {
  url: string;
  expiresAt?: string | null;
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  const value = document.cookie.split('; ').find((item) => item.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : null;
}

// The standalone enrollment service deliberately uses its own cookie name so
// it cannot collide with another Django service hosted on the same site. Keep
// the default Django name as a compatibility fallback for older local setups.
function readCsrfCookie(): string | null {
  return readCookie('enrollment_csrftoken') || readCookie('csrftoken');
}

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { credentials: 'include', ...init });
  } catch {
    throw new Error('Could not reach the server.');
  }
  const text = await response.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!response.ok) {
    const message = data && typeof data === 'object' && 'error' in data
      ? String((data as { error: unknown }).error)
      : `Request failed (${response.status})`;
    throw Object.assign(new Error(message), { status: response.status });
  }
  return data as T;
}

const base = (kind: LearnerKind, learnerId: string) => `/enrolment_api/wizard/${kind}/${learnerId}/initial-assessments`;

export function fetchInitialAssessments(kind: LearnerKind, learnerId: string): Promise<InitialAssessmentsResponse> {
  return send<InitialAssessmentsResponse>(`${base(kind, learnerId)}/`);
}

export function launchInitialAssessment(
  kind: LearnerKind,
  learnerId: string,
  subject: InitialAssessmentSubject,
): Promise<InitialAssessmentLaunchResponse> {
  return send<InitialAssessmentLaunchResponse>(`${base(kind, learnerId)}/${subject}/launch/`, { method: 'POST' });
}

export function autoLoginBksbAccount(
  kind: LearnerKind,
  learnerId: string,
  subject: InitialAssessmentSubject = 'english',
): Promise<BksbAutoLoginResponse> {
  const csrfToken = readCsrfCookie();
  if (!csrfToken) {
    return Promise.reject(new Error('Request verification is unavailable. Please reload the page and try again.'));
  }
  const query = subject === 'english' ? '' : `?subject=${encodeURIComponent(subject)}`;
  return send<BksbAutoLoginResponse>(`${base(kind, learnerId)}/bksb-account/${query}`, {
    method: 'POST',
    headers: {
      'X-CSRFToken': csrfToken,
      'X-Requested-With': 'XMLHttpRequest',
    },
  }).catch((reason) => {
    if (reason instanceof Error && (reason as Error & { status?: number }).status === 403) {
      throw new Error('Request verification failed. Please reload the page and try again.');
    }
    throw reason;
  });
}
