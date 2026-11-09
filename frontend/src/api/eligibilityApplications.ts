/** Enrolment staff review API. Applicant data is never cached locally. */
export type AssessmentStatus = 'pending_assessment' | 'provisional_eligible' | 'not_eligible';
export type ReviewStatus = 'approved' | 'rejected' | 'more_evidence_required';
export interface ReviewAction {
  action: ReviewStatus; notes: string; expectedVersion: number; requestId: string;
}
export interface ReviewEntry {
  id: string; applicationId: string; version: number; reviewerId: string; reviewerName: string;
  previousStatus: string; status: ReviewStatus; notes: string; decidedAt: string;
}
export interface ApplicationReview {
  status: ReviewStatus | null; version: number; history: ReviewEntry[];
  enabled: boolean; available: boolean; allowedActions: ReviewStatus[];
}
export interface EligibilityApplication {
  id: string;
  first_name: string | null;
  surname: string | null;
  work_email: string | null;
  submitted_at: string | null;
  employer_name: string | null;
  employer_postcode: string | null;
  main_workplace_postcode: string | null;
  workplace_arrangement: string | null;
  assessment_status: AssessmentStatus | null;
  assessment_reason: string | null;
  review_status?: ReviewStatus | null;
  review_version?: number | null;
}
export type ApplicationDetail = EligibilityApplication & Record<string, string | boolean | number | null>;
export interface ApplicationsPage {
  applications: EligibilityApplication[];
  total: number;
  page: number;
  pageSize: number;
}

const BASE = '/enrolment_api/eligibility-applications/';

async function read<T>(url: string, action?: ReviewAction): Promise<T> {
  const response = await fetch(url, {
    method: action ? 'POST' : 'GET', credentials: 'include', cache: 'no-store',
    headers: { 'X-Requested-With': 'XMLHttpRequest', ...(action ? { 'Content-Type': 'application/json' } : {}) },
    ...(action ? { body: JSON.stringify(action) } : {}),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw Object.assign(new Error(data?.error || 'Could not load eligibility applications.'), { status: response.status });
  return data as T;
}

export function fetchEligibilityApplications(options: { search: string; status: string; page: number }): Promise<ApplicationsPage> {
  return read(`${BASE}?${new URLSearchParams({ search: options.search, status: options.status, page: String(options.page), pageSize: '25' })}`);
}

export function fetchEligibilityApplication(id: string): Promise<{ application: ApplicationDetail }> {
  return read(`${BASE}${encodeURIComponent(id)}/`);
}

export function fetchApplicationReview(id: string): Promise<ApplicationReview> {
  return read(`${BASE}${encodeURIComponent(id)}/reviews/`);
}

export function recordApplicationReview(id: string, action: ReviewAction): Promise<ApplicationReview> {
  return read(`${BASE}${encodeURIComponent(id)}/reviews/`, action);
}
