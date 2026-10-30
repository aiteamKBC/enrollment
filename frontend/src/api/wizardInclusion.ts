// ============================================================================
// Enrolment wizard: the Inclusion step's status
//
// Whether the learner has answered the inclusion assessment on the Safeguarding
// site, read from the Safeguarding database by email (see
// enrolment_api/wizard_inclusion.py). Read-only; never cached, since the learner
// answers in another tab and comes back to check.
// ============================================================================
import type { LearnerKind } from './learnerDetail';

export interface InclusionStatus {
  /** False for commercial learners: the step is apprenticeship-only. */
  applicable: boolean;
  completed: boolean;
  /** ISO time of the latest response, when there is one. */
  respondedAt: string | null;
}

/**
 * Where the learner takes the assessment; the site signs them in through the
 * LMS. The live site in every environment, local development included —
 * VITE_SAFEGUARDING_URL can point a deployment elsewhere.
 */
export const SAFEGUARDING_URL: string =
  import.meta.env.VITE_SAFEGUARDING_URL || 'https://safeguarding.kentbusinesscollege.net/';

export async function fetchInclusionStatus(kind: LearnerKind, learnerId: string): Promise<InclusionStatus> {
  let res: Response;
  try {
    res = await fetch(`/enrolment_api/wizard/${kind}/${encodeURIComponent(learnerId)}/inclusion/`, {
      credentials: 'include',
      cache: 'no-store',
    });
  } catch {
    throw new Error('Could not reach the server. Check your connection and try again.');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok || !data || typeof data !== 'object') {
    throw new Error((data && data.error) || `Request failed (${res.status})`);
  }
  return {
    applicable: data.applicable !== false,
    completed: data.completed === true,
    respondedAt: typeof data.respondedAt === 'string' ? data.respondedAt : null,
  };
}
