// ============================================================================
// Learner coach-contact API client.
// Keeps the enrolment case owner and linked learner coach contact in sync.
// This picker is available for Active learners; GET 404s otherwise.
// ============================================================================
import { invalidateLearnerDetailCache } from './learnerDetail';
import { invalidateWizardCacheById } from './extendedIlr';
import type { ReviewHandover } from './reviewHandover';

const BASE = '/learner_api/learners';

export interface CoachContact {
  coachName: string;
  coachEmail: string;
  reviewHandover?: ReviewHandover;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      // Writes here are gated by login.permissions.staff_only, which reads the
      // HttpOnly kbc_session cookie — without this the PATCH 401s even though
      // the GET (an open read path) succeeds.
      credentials: 'include',
      ...init,
      // Spread last so a caller passing headers cannot drop these two.
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
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return data as T;
}

/** Current coach contact for a learner. Throws (status 404) if not Active yet. */
export function fetchLearnerCoach(id: string): Promise<CoachContact> {
  return request<CoachContact>(`${BASE}/${id}/coach/`);
}

/** Update coach contact. Pass only the field(s) you want to change. */
export async function updateLearnerCoach(id: string, patch: Partial<CoachContact>): Promise<CoachContact> {
  const contact = await request<CoachContact>(`${BASE}/${id}/coach/`, { method: 'PATCH', body: JSON.stringify(patch) });
  invalidateWizardCacheById(id);
  invalidateLearnerDetailCache();
  return contact;
}
