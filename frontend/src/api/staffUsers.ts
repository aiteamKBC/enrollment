// ============================================================================
// Staff / admin users API client
// Talks to the Django backend at /learner_api (proxied to :8000 by Vite in dev).
// Backs enrolment."Staff_users" — non-learner accounts created via the Create
// menu's "Create admin" option.
// ============================================================================
import type { UserListRow } from '@/pages/users/types';

const BASE = '/learner_api/staff-users';

/**
 * Staff positions. Mirrors POSITION_CHOICES in backend/learner_api/constants.py,
 * which validates writes — a value missing there is rejected on save.
 *
 * Position is a job title only. It no longer decides what an account can do —
 * that is `ACCESS_OPTIONS` below. Kept because existing rows carry these values
 * and the edit form still shows them.
 */
export const POSITION_OPTIONS = [
  'Caseowner',
  'Admin',
  'Enrolment',
  'Curriculum team',
  'Operations team',
  'Tutor',
];

/**
 * The position the Create admin form sends. It does not ask: access is the thing
 * that decides anything, and it is granted separately from the Accounts page.
 */
export const ADMIN_POSITION = 'Admin';

/**
 * The position the Create tutor form sends.
 *
 * Unlike the admin form, that one grants `access: 'tutor'` at the same time —
 * a tutor created without a grant lands on /access-required, so leaving it to a
 * second step would mean every tutor is broken until somebody notices.
 */
export const TUTOR_POSITION = 'Tutor';

/** Supported access grants. Mirrors ACCESS_CHOICES in learner_api/constants.py. */
export type StaffAccess = 'enrolment' | 'curriculum' | 'coach' | 'tutor' | 'record-monitor' | 'advanced-admin' | 'super-admin' | 'support';

/**
 * What each access permits, and where it lands on sign-in.
 *
 * The backend is the authority on all of this — it sends `access`, `accessHome`
 * and `accessNavRole` on the account payload, and enforces the permission with
 * `require_access`. This table exists so the console can *describe* the choices
 * it offers; it grants nothing on its own.
 */
export const ACCESS_OPTIONS: {
  id: StaffAccess;
  label: string;
  description: string;
  home: string;
  icon: string;
}[] = [
  { id: 'support', label: 'Support access', description: 'Review and respond to learner support tickets in the LMS.', home: '/support/ticket-queue', icon: 'ri-customer-service-2-line' },
  {
    id: 'advanced-admin', label: 'Advanced Admin access',
    description: 'Review the assigned PCP and ME learners and mark their assessments.',
    home: '/workspace/advanced-admin', icon: 'ri-team-line',
  },
  {
    id: 'record-monitor', label: 'Learning record monitoring',
    description: 'Read-only access to active enrolled learners, previous records and signatures.',
    home: '/old-otjh/monitor', icon: 'ri-dashboard-line',
  },
  {
    id: 'enrolment',
    label: 'Enrolment access',
    description:
      'The enrolment workspace and every learner record. With Super Admin, the only access that can change enrolment data.',
    home: '/users',
    icon: 'ri-user-add-line',
  },
  {
    id: 'curriculum',
    label: 'Curriculum access',
    description: 'The curriculum workspace — programmes, modules and their content.',
    home: '/workspace/curriculum',
    icon: 'ri-book-open-line',
  },
  {
    id: 'coach',
    label: 'Coach access',
    description: 'The coach workspace — their caseload, reviews and evidence validation.',
    home: '/workspace/coach',
    icon: 'ri-user-star-line',
  },
  {
    id: 'tutor',
    label: 'Tutor access',
    description: 'The tutor workspace — their teaching groups and session delivery.',
    home: '/workspace/tutor',
    icon: 'ri-presentation-line',
  },
  {
    id: 'super-admin',
    label: 'Super Admin access',
    description: 'Everything, including this console. Can edit any data on the platform.',
    home: '/workspace/admin',
    icon: 'ri-shield-star-line',
  },
];

export function accessLabel(access: string | null | undefined): string {
  return ACCESS_OPTIONS.find((o) => o.id === access)?.label || 'No access set';
}

/** The grant's name without the word "access".
 *
 * Every label ends in "access", which reads fine on its own but turns a row
 * holding three into "Curriculum access Coach access Enrolment access" — the
 * only word repeated is the one carrying no information. Used where the column
 * heading already says Access; `accessLabel` stays for places that stand alone. */
export function accessShortLabel(access: string | null | undefined): string {
  const label = ACCESS_OPTIONS.find((o) => o.id === access)?.label;
  if (!label) return 'None';
  return label.replace(/\s*access$/i, '');
}

export interface CreateStaffUserInput {
  username: string;
  email: string;
  /** Required — one of POSITION_OPTIONS. */
  position: string;
  /** One of ACCESS_OPTIONS. Omitted on create; granted from the Accounts page. */
  /** The PRIMARY grant — where this account lands at sign-in. */
  access?: StaffAccess;
  /** Every grant the account holds, including the primary. Omit to leave the
   *  existing set alone; send it to replace the set wholesale. */
  accesses?: StaffAccess[];
  type?: string;
  status?: string;
  phone?: string;
  dob?: string;
  title?: string;
  preferredName?: string;
  gender?: string;
  organization?: string;
  caseOwner?: string;
  learningProvider?: string;
  referenceNumber?: string;
  allowCheckpoint?: boolean;
  allowConsole?: boolean;
  allowClassic?: boolean;
}

/**
 * A staff row, shaped like a UserListRow so the directory can list learners and
 * staff in one table. `type` carries the staff position (Admin, Caseowner, ...).
 */
export interface StaffUserRow extends UserListRow {
  position: string;
  /** The access grant — '' when nobody has set one yet. */
  access: StaffAccess | '';
  phone: string;
  organization: string;
  title: string;
  preferredName: string;
  gender: string;
  dob: string;
  caseOwner: string;
  learningProvider: string;
  referenceNumber: string;
  // `invitation` is inherited from UserListRow — present on create, where an
  // invitation is always issued.
}

async function request<T>(url: string, init?: globalThis.RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      // Sends the kbc_session cookie — writes to this endpoint now require an
      // authenticated staff session (see login.permissions.staff_only).
      credentials: 'include',
      ...init,
      // Spread last: with `...init` after it, a caller passing any headers at
      // all would silently drop these two and the request would fail the
      // Content-Type parse and the CSRF check.
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

/** List staff/admin accounts, optionally restricted to the given positions. */
export async function fetchStaffUsers(positions?: string[]): Promise<StaffUserRow[]> {
  const qs = (positions ?? []).map((p) => `position=${encodeURIComponent(p)}`).join('&');
  const data = await request<{ count: number; results: StaffUserRow[] }>(`${BASE}/${qs ? `?${qs}` : ''}`);
  return data.results;
}

/**
 * Positions eligible to own a case. Only these staff appear in the create-user
 * form's Case owner dropdown.
 */
export const CASE_OWNER_POSITIONS = ['Caseowner', 'Admin'];

/**
 * Names for the Case owner dropdown, sourced from Staff_users. Returns the staff
 * whose position is Caseowner or Admin — a case owner has to be a real account,
 * so the list is whatever has actually been created.
 */
export async function fetchCaseOwners(): Promise<string[]> {
  const rows = await fetchStaffUsers(CASE_OWNER_POSITIONS);
  return rows.map((r) => r.name).filter(Boolean);
}

/** A staff member who can be assigned as a learner's coach. */
export interface CoachOption {
  name: string;
  email: string;
}

/**
 * Case owners as {name, email} pairs, for the learner header's coach picker.
 *
 * Same source and positions as `fetchCaseOwners` — a coach has to be a real
 * Caseowner/Admin account — but keeps the email, because assigning a coach
 * writes both coach_name and coach_email to "Learner"."learners" and the email
 * is what the calendar and absence-report code actually sends to.
 *
 * Rows with no email are dropped: they cannot fill both columns, and a
 * name-only pick would leave the two fields disagreeing about who the coach is.
 */
export async function fetchCoachOptions(): Promise<CoachOption[]> {
  const rows = await fetchStaffUsers(CASE_OWNER_POSITIONS);
  return rows
    .filter((r) => r.name && r.email)
    .map((r) => ({ name: r.name, email: r.email }));
}

/**
 * One staff/admin account by id. The Accounts console holds a login account,
 * which carries only `subjectId` — this is how it reaches the person record
 * behind it so the same edit form the directory uses can be opened there too.
 */
export function fetchStaffUser(id: string): Promise<StaffUserRow> {
  return request<StaffUserRow>(`${BASE}/${id}/`);
}

/** Create a staff/admin account; returns the new row. */
export function createStaffUser(input: CreateStaffUserInput): Promise<StaffUserRow> {
  return request<StaffUserRow>(`${BASE}/`, { method: 'POST', body: JSON.stringify(input) });
}

/** Patch a staff/admin account (e.g. change position). */
export function updateStaffUser(id: string, patch: Partial<CreateStaffUserInput>): Promise<StaffUserRow> {
  return request<StaffUserRow>(`${BASE}/${id}/`, { method: 'PATCH', body: JSON.stringify(patch) });
}
