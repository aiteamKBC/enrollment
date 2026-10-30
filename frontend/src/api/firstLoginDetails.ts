import { invalidateLearnerDetailCache } from './learnerDetail';
import { invalidateLearnerReads } from './learnerRead';

// ============================================================================
// The two screens a new apprentice completes on first sign-in — their address
// and personal details, then an electronic signature — before the enrolment
// wizard opens. See backend/learner_api/first_login_details.py.
// ============================================================================

export interface FirstLoginDetailsValues {
  title: string;
  dateOfBirth: string;
  phone: string;
  country: string;
  postcode: string;
  addressLine1: string;
  addressLine2: string;
  townCity: string;
  county: string;
}

export interface FirstLoginDetailsState {
  required: boolean;
  completedAt: string | null;
  signatoryName: string;
  details: FirstLoginDetailsValues;
  hasSavedSignature: boolean;
  programmeStatus: string;
  csrfToken: string;
  alreadyCompleted?: boolean;
}

/** A rejected save, carrying the per-field messages the server returned. */
export class FirstLoginDetailsError extends Error {
  readonly fields: Record<string, string>;
  readonly code: string;

  constructor(message: string, fields: Record<string, string> = {}, code = '') {
    super(message);
    this.name = 'FirstLoginDetailsError';
    this.fields = fields;
    this.code = code;
  }
}

const url = (learnerId: string) => `/learner_api/first-login-details/${encodeURIComponent(learnerId)}/`;

async function parse(response: Response, fallback: string): Promise<FirstLoginDetailsState> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new FirstLoginDetailsError(data.error || fallback, data.fields || {}, data.code || '');
  }
  return data as FirstLoginDetailsState;
}

export async function fetchFirstLoginDetails(learnerId: string): Promise<FirstLoginDetailsState> {
  const response = await fetch(url(learnerId), { credentials: 'include', cache: 'no-store' });
  return parse(response, 'Your details could not be loaded. Please try again.');
}

export async function submitFirstLoginDetails(
  learnerId: string,
  values: FirstLoginDetailsValues,
  signature: string,
  csrfToken: string,
): Promise<FirstLoginDetailsState> {
  const response = await fetch(url(learnerId), {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify({ ...values, signature }),
  });
  const state = await parse(response, 'Your details could not be saved. Please try again.');
  // The programme status just moved to Onboarding: drop every cached read of
  // this learner so the wizard redirect and sidebar see the new status.
  invalidateLearnerDetailCache('apprenticeship', learnerId);
  invalidateLearnerReads();
  return state;
}
