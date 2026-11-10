/** Read-only eligibility applications API. Applicant data is never cached locally. */
export type AssessmentStatus = 'pending_assessment' | 'provisional_eligible' | 'not_eligible';
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
}
export type ApplicationDetail = EligibilityApplication & Record<string, string | boolean | number | null>;
export interface ApplicationsPage {
  applications: EligibilityApplication[];
  total: number;
  page: number;
  pageSize: number;
}

const BASE = '/enrolment_api/eligibility-applications/';

async function read<T>(url: string): Promise<T> {
  const response = await fetch(url, {
    method: 'GET', credentials: 'include', cache: 'no-store',
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
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
