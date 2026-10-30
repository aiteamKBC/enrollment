// ============================================================================
// Organisations + employers API client
// Talks to the Django backend at /learner_api (proxied to :8000 by Vite in dev).
// Backs enrolment."Organisations" and enrolment."Employers" — the two profile
// types created from the Users page's Create menu.
//
// An organisation is a company; an employer is a person at one or more of them.
// The employer form's "Employer Group" picker lists organisations, which is why
// listOrganisations supports search + paging.
// ============================================================================

import type { InvitationOutcome } from '@/pages/users/types';

const ORGS = '/learner_api/organisations';
const EMPLOYERS = '/learner_api/employers';

/** One {day, start, end} row of the organisation form's working-hours repeater. */
export interface WorkingHoursSession {
  day: string;
  start: string;
  end: string;
}

export interface OrganisationRow {
  id: string;
  status: string;
  name: string;
  owner: string;
  category: string;
  /** Shown in the Employer Group picker's own columns. */
  groupType: string;
  parentName: string;
  edrsErnNumber: string;
  apprenticeshipAgreementId: string;
  postCode: string;
  address1: string;
  address2: string;
  cityTown: string;
  county: string;
  country: string;
  workingHours: WorkingHoursSession[];
  contactName: string;
  contactEmail: string;
  contactTelephone: string;
  contactRole: string;
  website: string;
  referenceNumber: string;
  levyPayer: string;
  approxNoOfEmployees: number | null;
  healthAndSafety: string;
  logoUrl: string;
  /** Null when never set — distinct from a deliberate "no". */
  sendHoursVerificationEmails: boolean | null;
}

export interface EmployerRow {
  id: string;
  firstName: string;
  surname: string;
  /** Server-side "first surname", so a list row needs no assembly. */
  name: string;
  gender: string;
  email: string;
  mobile: string;
  postCode: string;
  address1: string;
  address2: string;
  townCity: string;
  county: string;
  country: string;
  /** Organisation ids this person belongs to — the real link. */
  employerGroupIds: string[];
  /** Their names, denormalised server-side so a row renders without a lookup. */
  employerGroupNames: string[];
  /**
   * Present on create, where an invitation is always issued. Reports the
   * invitation's fate separately from the employer's creation, which succeeds
   * either way — see login/services.py.
   */
  invitation?: InvitationOutcome;
}

/** Option lists for both forms, served from the constants the API validates against. */
export interface EmployerOptions {
  status: string[];
  groupType: string[];
  levyPayer: string[];
  healthAndSafety: string[];
  owners: string[];
}

export type OrganisationInput = Partial<Omit<OrganisationRow, 'id'>>;
export type EmployerInput = Partial<
  Omit<EmployerRow, 'id' | 'name' | 'employerGroupNames' | 'invitation'>
>;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      // Sends the kbc_session cookie — writes require an authenticated staff
      // session (see login.permissions.staff_only).
      credentials: 'include',
      ...init,
      // Spread last: with `...init` after it, a caller passing any headers at
      // all would silently drop these two, failing the Content-Type parse and
      // the CSRF check.
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
    throw new Error((data && data.error) || `Request failed (${res.status})`);
  }
  return data as T;
}

export interface OrganisationPage {
  count: number;
  page: number;
  pageSize: number;
  results: OrganisationRow[];
}

/**
 * List organisations. `page` drives the Employer Group picker's pager; omitting
 * it returns every row, which is what a plain list screen wants.
 */
export function listOrganisations(opts: { search?: string; status?: string; page?: number } = {}): Promise<OrganisationPage> {
  const qs = new URLSearchParams();
  if (opts.search) qs.set('search', opts.search);
  if (opts.status) qs.set('status', opts.status);
  if (opts.page) qs.set('page', String(opts.page));
  const suffix = qs.toString() ? `?${qs}` : '';
  return request<OrganisationPage>(`${ORGS}/${suffix}`);
}

export function createOrganisation(input: OrganisationInput): Promise<OrganisationRow> {
  return request<OrganisationRow>(`${ORGS}/`, { method: 'POST', body: JSON.stringify(input) });
}

export function updateOrganisation(id: string, patch: OrganisationInput): Promise<OrganisationRow> {
  return request<OrganisationRow>(`${ORGS}/${id}/`, { method: 'PATCH', body: JSON.stringify(patch) });
}

/** List employers, optionally filtered to the people at one organisation. */
export function listEmployers(opts: { search?: string; organisation?: string } = {}): Promise<{ count: number; results: EmployerRow[] }> {
  const qs = new URLSearchParams();
  if (opts.search) qs.set('search', opts.search);
  if (opts.organisation) qs.set('organisation', opts.organisation);
  const suffix = qs.toString() ? `?${qs}` : '';
  return request<{ count: number; results: EmployerRow[] }>(`${EMPLOYERS}/${suffix}`);
}

export function createEmployer(input: EmployerInput): Promise<EmployerRow> {
  return request<EmployerRow>(`${EMPLOYERS}/`, { method: 'POST', body: JSON.stringify(input) });
}

export function updateEmployer(id: string, patch: EmployerInput): Promise<EmployerRow> {
  return request<EmployerRow>(`${EMPLOYERS}/${id}/`, { method: 'PATCH', body: JSON.stringify(patch) });
}

export function fetchEmployerOptions(): Promise<EmployerOptions> {
  return request<EmployerOptions>(`${EMPLOYERS}/options/`);
}
