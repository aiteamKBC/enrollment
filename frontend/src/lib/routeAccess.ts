import type { AccessWorkspace, AuthUser, Role } from '@/api/auth';
import { canManageEnrolment, hasOwnEnrolment } from './enrollmentAccess';

/**
 * Which audience each area of the SPA is for, and where an account belongs.
 *
 * This is the second half of route protection. `RequireAuth` answers "is there
 * a session"; this answers "is this account's kind of person meant to be here",
 * so a signed-in learner pasting /curriculum/teams-meetings lands back on their
 * own dashboard instead of rendering the curriculum console.
 *
 * It is deliberately drawn on `role` — the four coarse roles the server issues
 * and recomputes on every request — and not on the fifteen-role RBAC map in
 * `@/mocks/rbac`. That map cannot carry this: `ROLE_TO_RBAC_IDS` in useAuth
 * projects *every* staff account onto `role_compliance`, so enforcing routes
 * through it would lock a curriculum designer out of curriculum.
 *
 * The boundary this draws is between staff, learners and employers. Dividing
 * the staff console further — enrolment vs curriculum vs coach vs tutor — is
 * the `access` grant's job, and belongs here only once the API enforces it too;
 * a rule the UI applies and the server does not is theatre, and locks people
 * out for nothing. `/admin` is the exception, and only because the server
 * already backs it: `sessions._refresh_staff_role` awards `role: 'admin'` to
 * the super-admin access grant and to nothing else.
 *
 * None of this is a security boundary. It decides which page is drawn; the
 * boundary is the API gate (backend `login/api_gate.py`), which refuses the
 * same requests whether or not the page renders.
 */

/** Roles that run the staff console. Mirrors `STAFF` in login/api_gate.py. */
const STAFF: readonly Role[] = ['admin', 'staff'];

/** The super-admin console only. `role: 'admin'` *is* the super-admin grant. */
const ADMIN: readonly Role[] = ['admin'];

/** Any signed-in account, whoever they are. */
const ANYONE: readonly Role[] = ['admin', 'staff', 'employer', 'learner'];

/**
 * A learner's own pages, which staff also open to review them. The API keeps
 * them apart where it matters: `learner_self_only` gives staff 403 on progress
 * writes, so a caseowner can read a learner's week but cannot tick it off as
 * them. Employers have their own portal and are not admitted here.
 */
const LEARNER_AND_STAFF: readonly Role[] = ['admin', 'staff', 'learner'];

/**
 * The employer portal, which staff also open from the Users directory. Scoped
 * per-record on the server by `employer_or_staff`, so an employer reaching it
 * still sees only their own organisation.
 */
const EMPLOYER_AND_STAFF: readonly Role[] = ['admin', 'staff', 'employer'];

/**
 * Route prefix -> roles admitted, applied longest-prefix-first so a specific
 * path can differ from the area around it (`/workspace` is the case that needs
 * it: three of its children have different audiences from the rest).
 *
 * Every top-level segment the router defines is listed. That is what makes the
 * fallback below safe to close.
 */
const RULES: ReadonlyArray<readonly [string, readonly Role[]]> = [
  ['/my-courses', ADMIN],
  ['/old-otjh/coach', STAFF],
  ['/old-otjh', LEARNER_AND_STAFF],
  // Workspaces, which do not share one audience.
  ['/workspace/learner', LEARNER_AND_STAFF],
  ['/workspace/employer', EMPLOYER_AND_STAFF],
  ['/workspace/admin', ADMIN],
  ['/workspace', STAFF],

  // The learner's own experience, and the employer's.
  // Matching is segment-aware (see `rolesForRoute`), so "/employer" does not
  // swallow "/employers" — the two are separate rules that happen to agree.
  ['/learner', LEARNER_AND_STAFF],
  ['/employer', EMPLOYER_AND_STAFF],
  ['/employers', EMPLOYER_AND_STAFF],

  // The super-admin console.
  ['/admin', ADMIN],
  ['/internal-panel', ADMIN],

  // The staff console.
  ['/activity-categories', STAFF],
  ['/audit', STAFF],
  ['/coach', STAFF],
  ['/curriculum', STAFF],
  ['/engagement', STAFF],
  ['/finance', STAFF],
  ['/leadership', STAFF],
  ['/mis', STAFF],
  ['/qa', STAFF],
  ['/safeguarding', STAFF],
  ['/support', STAFF],
  ['/training-plan', STAFF],
  ['/tutor', STAFF],
  ['/users', STAFF],

  // Shared surfaces. Each renders against the viewer's own nav and reads only
  // their own records, so every signed-in account belongs on them.
  // Signed in, but no workspace chosen yet. Open to every role: the whole point
  // is that the account has several, and the page only ever offers what the
  // server already granted it.
  ['/choose-workspace', ANYONE],
  ['/communication', ANYONE],
  ['/home', ANYONE],
  ['/messages', ANYONE],
  ['/notifications', ANYONE],
  ['/starred-messages', ANYONE],
  ['/tasks', ANYONE],
  ['/user-guide', ANYONE],
  ['/profile', ANYONE],
];

const RULES_BY_SPECIFICITY = [...RULES].sort((a, b) => b[0].length - a[0].length);

/**
 * A path nobody has classified is treated as staff console, because that is
 * what almost every page here is. The failure this chooses is a staff-shaped
 * page being shown to staff who should not see it — visible, and caught by the
 * API refusing the data — over a learner page silently opening to everyone.
 *
 * It should never be reached: every segment the router defines has a rule.
 */
const FALLBACK: readonly Role[] = STAFF;

/** The roles admitted to `path`. */
export function rolesForRoute(path: string): readonly Role[] {
  const match = RULES_BY_SPECIFICITY.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`));
  return match ? match[1] : FALLBACK;
}

/** Whether `account` is admitted to `path`. */
export function mayAccessRoute(path: string, account: Pick<AuthUser, 'role' | 'access' | 'accessWorkspaces'>): boolean {
  if (account.access === 'advanced-admin') {
    return path === '/workspace/advanced-admin'
      || path.startsWith('/workspace/advanced-admin/')
      || path === '/profile';
  }
  if (account.access === 'record-monitor') return path === '/old-otjh' || path.startsWith('/old-otjh/');
  if (path === '/support/ticket-queue') {
    const grants = [account.access, ...(account.accessWorkspaces ?? []).map(workspace => workspace.access)];
    return account.role === 'admin' || (account.role === 'staff' && grants.includes('support'));
  }
  if (['/admin/coach_directory', '/users/coach-directory', '/curriculum/coach-directory'].includes(path)) {
    const grants = [account.access, ...(account.accessWorkspaces ?? []).map(workspace => workspace.access)];
    return STAFF.includes(account.role) && grants.some(grant => ['super-admin', 'enrolment', 'curriculum'].includes(grant ?? ''));
  }
  if (path === '/users/first-sessions') {
    // Enrolment only (super-admin passes everywhere) -- the same grant the API
    // enforces in learner_api/first_session_bookings.py.
    const grants = [account.access, ...(account.accessWorkspaces ?? []).map(workspace => workspace.access)];
    return STAFF.includes(account.role) && grants.some(grant => ['super-admin', 'enrolment'].includes(grant ?? ''));
  }
  if (path === '/old-otjh/monitor') return account.access === 'super-admin';
  return rolesForRoute(path).includes(account.role);
}

/**
 * Where an account belongs when it has no particular destination — after
 * signing in, and when it is turned away from a route it may not open.
 *
 * A staff account's `accessHome` wins: the server derives it from the access
 * grant (ACCESS_HOME_ROUTES in learner_api/constants.py), and an account with
 * no grant is sent to /access-required rather than into a console it cannot use.
 */
export function homeRouteFor(
  account: Pick<AuthUser, 'role' | 'access' | 'accesses' | 'accessWorkspaces' | 'accessHome' | 'subjectId' | 'hasLegacyRecord' | 'learnerRecordId'>,
): string {
  if (account.role === 'learner') return '/learner/home';
  if (hasOwnEnrolment(account) && !canManageEnrolment(account)) return '/learner/home';
  if (account.role === 'employer' && account.subjectId) {
    return `/employers/${account.subjectId}/documents`;
  }
  return '/users';
}

function routePathname(path: string | null | undefined): string {
  const cleaned = String(path || '').trim();
  if (!cleaned) return '';
  return cleaned.split(/[?#]/, 1)[0].replace(/\/+$/, '') || '/';
}

export function isBareLearnerWorkspacePath(path: string | null | undefined): boolean {
  return routePathname(path) === '/workspace/learner';
}

/** Where the workspace chooser lives. */
export const CHOOSE_WORKSPACE_ROUTE = '/choose-workspace';

/**
 * The workspaces this account may open, as the server described them.
 *
 * Always an array, so callers need not repeat the null handling. The server is
 * the authority on both the set and each landing route: anything derived here
 * from `access` alone would miss the `learner` entry a staff member who also
 * studies gets, whose route carries the id of their own record.
 */
export function workspacesFor(
  account: Pick<AuthUser, 'accessWorkspaces'> | null | undefined,
): AccessWorkspace[] {
  return account?.accessWorkspaces ?? [];
}

/**
 * Whether this account should be asked which workspace it wants.
 *
 * Only when there is a real choice — two or more. One workspace is not a
 * choice, and showing a page with a single button on it would be a step in
 * everybody's way for nothing.
 *
 * `record-monitor` is deliberately excluded: `homeRouteFor` and
 * `mayAccessRoute` both pin that grant to /old-otjh, so offering it a menu
 * would list workspaces it is then refused at the door.
 */
export function shouldChooseWorkspace(
  account: Pick<AuthUser, 'access' | 'accessWorkspaces'> | null | undefined,
): boolean {
  if (!account) return false;
  if (account.access === 'record-monitor') return false;
  return workspacesFor(account).length > 1;
}

/**
 * Every successful sign-in starts at the account's own home. A route left in
 * browser history or old login state must never choose the new session's page.
 * Keep the optional legacy argument so older callers cannot restore that route.
 *
 * An account holding more than one access grant is asked which one it wants
 * first — landing it on the primary silently would hide the others behind a
 * menu nobody knows to look for. The chooser then navigates to the `home` the
 * server gave for the workspace picked.
 */
export function postLoginRouteFor(
  account: Pick<AuthUser, 'role' | 'access' | 'accessHome' | 'subjectId' | 'hasLegacyRecord' | 'accessWorkspaces'>,
  _requestedPath?: string | null,
): string {
  if (shouldChooseWorkspace(account)) return CHOOSE_WORKSPACE_ROUTE;
  return homeRouteFor(account);
}

const HOME_BY_ROLE: Record<Role, string> = {
  admin: '/workspace/admin',
  staff: '/users',
  employer: '/workspace/employer',
  learner: '/learner/home',
};
