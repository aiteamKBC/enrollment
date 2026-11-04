// The public apprenticeship eligibility form (/apprenticeship_application).
// Applicants have no account yet, so nothing here sends cookies; the backend
// requires only the X-Requested-With header (its CSRF defence for public JSON).
// Nothing is saved until the applicant submits: there is no draft.
import type { EligibilityLayoutResponse } from '@/pages/apprenticeship-application/layout/types';

export const EMPLOYMENT_STATUSES = [
  'In paid employment',
  'Self employment',
  'Not in paid employment, looking for work and available to start work',
  'Not in paid employment, not looking for work and/or not available to start work',
] as const;

export const WORKPLACE_ARRANGEMENTS = ['same', 'different', 'remote_hybrid_multiple'] as const;
export const WORKPLACE_LABELS: Readonly<Record<string, string>> = {
  same: 'Yes — I usually work at this address.',
  different: 'No — I usually work at a different location.',
  remote_hybrid_multiple: 'I work remotely, hybrid, or across multiple locations.',
};

export const RESIDENCY_STATUSES = [
  'UK Citizen',
  'Settled Status',
  'Pre-settled Status',
  'Skilled Worker Visa',
  'Study or Graduate Visa',
  'Other',
] as const;

export const PROGRAMMES = [
  'Marketing Executive Level 4',
  'Marketing Manager Level 6',
  'Associate Project Manager Level 4',
  'Project Controls Professional Level 6',
] as const;

export interface ApprenticeshipApplication {
  title?: string;
  firstName: string;
  surname: string;
  workEmail: string;
  phoneNumber: string;
  fullAddress: string;
  postcode: string;
  /** YYYY-MM-DD */
  dateOfBirth: string;
  employmentStatus: string;
  /** YYYY-MM-DD */
  startDateWithEmployer: string;
  jobTitle: string;
  majorityHoursInEngland: boolean | null;
  employerName: string;
  employerAddress: string;
  employerAddressLine1?: string;
  employerAddressLine2?: string;
  employerAddressLine3?: string;
  /** Visible Address Line 3; stable town/city API key retained. */
  employerTownCity?: string;
  /** Visible Address Line 4 (County and Country); existing column retained. */
  employerCounty?: string;
  residentialAddressLine1?: string;
  residentialAddressLine2?: string;
  residentialAddressLine3?: string;
  /** Visible Address Line 3; stable town/city API key retained. */
  residentialTownCity?: string;
  /** Visible Address Line 4 (County and Country); Country of Residence stays separate. */
  residentialCounty?: string;
  employerPostcode: string;
  workplaceArrangement?: string;
  mainWorkplacePostcode?: string;
  representativeName: string;
  representativeEmail: string;
  /** Residency and funding answers. */
  livesInUk: boolean | null;
  countryOfResidence: string;
  nationality: string;
  residentPrev3Years: boolean | null;
  yearsInUk: string;
  residenceNotForEducation: boolean | null;
  fullTimeEducationPrior: boolean | null;
  /** YYYY-MM-DD; relevant only when fullTimeEducationPrior is Yes. */
  expectedLeavingDate?: string | null;
  residencyStatus: string;
  programme: string;
  attendedFundedTraining: boolean | null;
  /** YYYY-MM-DD; only sent after a Yes to attendedFundedTraining. */
  fundedTrainingCompletedOn: string | null;
  notPreviouslyCompletedProgramme: boolean | null;
}

export type ApplicationField = keyof ApprenticeshipApplication;

/** An answer to a question added in the eligibility form builder. */
export type CustomAnswer = string | boolean | null;

/** What is sent: the built-in answers, the custom ones, and which form the applicant was shown. */
export interface ApplicationSubmission extends ApprenticeshipApplication {
  custom: Record<string, CustomAnswer>;
  /** The published layout version the form was built from; null for the standard form. */
  layoutVersion: number | null;
}

export class ApplicationSubmitError extends Error {
  readonly status: number;
  /** The server's message per answer it refused, keyed by question (built-in field or custom key). */
  readonly fields: Record<string, string>;
  /** The form was republished while the applicant filled it in (409). */
  readonly formChanged: boolean;
  constructor(message: string, status: number, fields: Record<string, string> = {}, formChanged = false) {
    super(message);
    this.status = status;
    this.fields = fields;
    this.formChanged = formChanged;
  }
}

const HEADERS = { 'X-Requested-With': 'XMLHttpRequest' };
const url = '/enrolment_api/public/apprenticeship-application/';

/**
 * Completed initial screening has a boolean eligibility result. Invitation
 * outcomes describe the next step; technical retries are not eligibility results.
 * pendingAssessment/review remain readable for historical submission receipts,
 * which are never reclassified or re-invited by the new screening flow.
 */
export type ApplicationResult = 'invited' | 'invitePending' | 'existingAccount' | 'notEligible' | 'retryLater' | 'pendingAssessment';

const RESULTS: readonly ApplicationResult[] = ['invited', 'invitePending', 'existingAccount', 'notEligible', 'retryLater', 'pendingAssessment'];

export interface ApplicationOutcome {
  review?: { status: 'approved' | 'rejected' | 'more_evidence_required'; version: number; message: string };
  assessmentStatus?: 'provisional_eligible' | 'pending_assessment' | 'not_eligible';
  assessmentReason?: string | null;
  eligible: boolean | null;
  reasons: string[];
  outcome: ApplicationResult | null;
}

export async function submitApprenticeshipApplication(body: ApplicationSubmission): Promise<ApplicationOutcome> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      credentials: 'omit',
      headers: { 'Content-Type': 'application/json', ...HEADERS },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApplicationSubmitError('We could not reach the application service. Check your connection and try again.', 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || data?.submitted !== true) {
    throw new ApplicationSubmitError(
      data?.error || 'We could not submit your application. Please try again.',
      response.status,
      data?.fields && typeof data.fields === 'object' ? data.fields : {},
      response.status === 409 && data?.formChanged === true,
    );
  }
  return {
    ...(data.review && ['approved', 'rejected', 'more_evidence_required'].includes(data.review.status)
      && Number.isInteger(data.review.version) && data.review.version > 0 && typeof data.review.message === 'string'
      ? { review: { status: data.review.status, version: data.review.version, message: data.review.message } } : {}),
    ...(data.assessmentStatus === 'provisional_eligible' || data.assessmentStatus === 'pending_assessment' || data.assessmentStatus === 'not_eligible'
      ? { assessmentStatus: data.assessmentStatus, assessmentReason: typeof data.assessmentReason === 'string' ? data.assessmentReason : null } : {}),
    eligible: typeof data.eligible === 'boolean' ? data.eligible : null,
    reasons: Array.isArray(data.reasons) ? data.reasons.filter((reason: unknown): reason is string => typeof reason === 'string') : [],
    outcome: RESULTS.includes(data.outcome) ? data.outcome : null,
  };
}

/** The form in force: the layout published from the eligibility form builder, or null for the standard form. */
export async function fetchEligibilityForm(): Promise<EligibilityLayoutResponse> {
  let response: Response;
  try {
    response = await fetch('/enrolment_api/public/eligibility-form-layout/', { credentials: 'omit', headers: HEADERS });
  } catch {
    throw new Error('We could not reach the application service. Check your connection and try again.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data || typeof data !== 'object' || !('layout' in data)) {
    throw new Error(data?.error || 'The application form is not available just now. Please try again in a few minutes.');
  }
  return { layout: data.layout ?? null, version: typeof data.version === 'number' ? data.version : null,
    workplaceEnabled: data.workplaceEnabled === true };
}

// ── postcode check ──────────────────────────────────────────────────────────

/** A UK postcode with or without its space, e.g. "CT1 1AA", "ct11aa". */
const UK_POSTCODE = /^[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}$/;

/** The postcode upper-cased with its spaces removed: the key a check is remembered by. */
export const compactPostcode = (value: string) => value.replace(/\s+/g, '').toUpperCase();
export const isUkPostcode = (value: string) => UK_POSTCODE.test(compactPostcode(value));

export type PostcodeCheck =
  | { status: 'found'; postcode: string; country: string; region: string; district: string; inEngland: boolean }
  | { status: 'notFound' }
  /** The check could not be made (service down, too many checks, offline); retry verification. */
  | { status: 'unavailable' };

/** Where a UK postcode is. Never throws: a failed check is 'unavailable'. */
export async function lookupPostcode(postcode: string): Promise<PostcodeCheck> {
  try {
    const response = await fetch(`/enrolment_api/public/postcode-lookup/?postcode=${encodeURIComponent(postcode.trim())}`, {
      credentials: 'omit',
      headers: HEADERS,
    });
    const data = await response.json().catch(() => null);
    if (response.status === 400) return { status: 'notFound' };
    if (!response.ok || !data) return { status: 'unavailable' };
    if (data.found !== true) return { status: 'notFound' };
    const text = (value: unknown) => (typeof value === 'string' ? value : '');
    return {
      status: 'found',
      postcode: text(data.postcode),
      country: text(data.country),
      region: text(data.region),
      district: text(data.district),
      inEngland: data.inEngland === true,
    };
  } catch {
    return { status: 'unavailable' };
  }
}
