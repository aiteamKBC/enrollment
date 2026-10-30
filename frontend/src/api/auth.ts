/**
 * Client for the platform authentication API (Django `login` app, /login_api/).
 *
 * Every call goes through `request`, which does three things that matter:
 *
 *  - `credentials: 'include'` so the HttpOnly `kbc_session` cookie is sent and
 *    stored. The cookie is never readable from JS — that is the point of it —
 *    so there is no token for this module to hold.
 *  - Sets `X-Requested-With: XMLHttpRequest` on every request. The backend
 *    requires it as its CSRF defence (see login/views.py): a cross-origin form
 *    post cannot set a custom header, and a fetch that tries needs a preflight
 *    this origin does not grant.
 *  - Surfaces the server's `error` string, and preserves the machine-readable
 *    `code` (`locked`, `weak_password`, `invalid_token`, …) on the thrown error
 *    so pages can react to the kind of failure, not just its text.
 */

export type Role = 'admin' | 'staff' | 'employer' | 'learner';
export type SubjectType = 'learner' | 'employer' | 'staff';

/**
 * One workspace an account may open, as the server describes it.
 *
 * `access` is an ACCESS_OPTIONS id (`coach`, `tutor`, …) or the literal
 * `learner` for a staff member's own enrolment record. `home` is the route it
 * opens — already carrying the query string the learner entry needs — so the
 * chooser never has to build a destination itself.
 */
export interface AccessWorkspace {
  access: string;
  home: string;
  navRole?: string | null;
}

export interface AuthUser {
  id: number;
  email: string;
  displayName: string | null;
  role: Role;
  subjectType: SubjectType;
  subjectId: number;
  hasPassword: boolean;
  lastLoginAt: string | null;
  permissions: string[];
  /** Staff only — the Position column, e.g. "Caseowner". */
  position?: string | null;
  /**
   * Staff access grant — one of ACCESS_OPTIONS in `staffUsers.ts`, or '' when
   * none is recorded. Decides where the account lands and which sidebar it
   * gets; the server enforces what it may actually reach.
   */
  access?: string | null;
  /** Landing route for `access`, chosen server-side (ACCESS_HOME_ROUTES). */
  accessHome?: string | null;
  /** `roleNavMap` key for `access`, chosen server-side (ACCESS_NAV_ROLES). */
  accessNavRole?: string | null;
  /** Every grant this account holds, in canonical order. Includes `access`. */
  accesses?: string[];
  /**
   * Each grant with where it leads — what the workspace chooser offers.
   *
   * Built server-side so the set of workspaces and their landing routes are
   * decided in one place (identity.account_payload). A staff member who is also
   * studying gets a `learner` entry here too, pointing at their own record.
   *
   * One entry means there is nothing to choose: sign-in goes straight there.
   */
  accessWorkspaces?: AccessWorkspace[];
  /** This person's own enrolment record, when they have one.
   *
   * Set for a staff member or administrator who is ALSO studying a programme.
   * They keep the single account they already have — a second one on the same
   * address would make sign-in ambiguous — so this is what tells the workspace
   * switcher to offer them the learner side as well. */
  learnerRecordId?: number | null;
  /** `commercial` or `apprenticeship`, for addressing that record. */
  learnerRecordKind?: string | null;
  /** Learners only. */
  learnerType?: string | null;
  programme?: string | null;
  /** Server-derived eligibility for the previous-record portal. */
  hasLegacyRecord?: boolean;
  /** Employers only — the organisations they belong to. */
  organisationIds?: number[];
}

/**
 * Is this signed-in account genuinely studying `(kind, id)` themselves, rather
 * than a staff member reviewing somebody else's record?
 *
 * A staff member or administrator who is ALSO a learner (see
 * `login/learner_enrolment.py`) reaches their own record from the workspace
 * switcher, at their own `learnerRecordId`/`learnerRecordKind` — and there they
 * need the ordinary learner experience: the fresh/onboarding gates, the
 * blocked-until-start-date screen, progress that actually saves. The SAME
 * account browsing a different learner's page from the enrolment workspace
 * must keep the reviewing behaviour (full menu, no redirects, read-only)
 * exactly as before.
 *
 * Comparing only `account.role` could not tell those two apart — an admin was
 * always "reviewing", even on the one record that is actually theirs.
 */
export function isOwnLearnerRecord(
  account: Pick<AuthUser, 'learnerRecordId' | 'learnerRecordKind'> | null | undefined,
  kind: string | null | undefined,
  id: string | null | undefined,
): boolean {
  if (!account?.learnerRecordId || !kind || !id) return false;
  const recordKind = account.learnerRecordKind || 'commercial';
  return recordKind === kind && String(account.learnerRecordId) === String(id);
}

/** An API failure that carries the backend's machine-readable code. */
export class AuthError extends Error {
  code?: string;
  status: number;
  /** Present on a 423: when the lockout lifts. */
  lockedUntil?: string | null;

  constructor(message: string, status: number, code?: string, lockedUntil?: string | null) {
    super(message);
    this.name = 'AuthError';
    this.status = status;
    this.code = code;
    this.lockedUntil = lockedUntil;
  }
}

const BASE = '/login_api';
const SESSION_TIMEOUT_MS = 45_000;

// Concurrent GETs of the same path share one request. React StrictMode runs
// every effect twice in development, so the session lookup on mount left two
// identical /me/ calls on the wire; the same pattern is used for curriculum
// reads in lib/sharedGetJson.ts. In-flight only, never a TTL cache: the cookie
// remains the authority and a later call must still ask the server.
const inFlightGets = new Map<string, Promise<unknown>>();

function request<T>(path: string, init?: globalThis.RequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  if (method !== 'GET') return send<T>(path, init);

  const existing = inFlightGets.get(path) as Promise<T> | undefined;
  if (existing) return existing;

  const pending = send<T>(path, init).finally(() => {
    if (inFlightGets.get(path) === (pending as Promise<unknown>)) inFlightGets.delete(path);
  });
  inFlightGets.set(path, pending as Promise<unknown>);
  return pending;
}

async function send<T>(path: string, init?: globalThis.RequestInit): Promise<T> {
  // Every protected route waits for /me/. Bound both the headers and body so
  // a stalled connection cannot leave the whole app on its loading skeleton.
  const controller = path === '/me/' ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), SESSION_TIMEOUT_MS) : undefined;
  let res: Response;
  let text: string;
  try {
    res = await fetch(`${BASE}${path}`, {
      credentials: 'include',
      ...init,
      ...(controller ? { signal: controller.signal } : {}),
      headers: {
        'Content-Type': 'application/json',
        // Required by the backend — see the module comment.
        'X-Requested-With': 'XMLHttpRequest',
        ...(init?.headers || {}),
      },
    });
    text = await res.text();
  } catch {
    if (controller?.signal.aborted) {
      throw new AuthError('Your session took too long to load. Please try again.', 0, 'timeout');
    }
    throw new AuthError(
      'Could not reach the server. Is the backend running on port 8000?',
      0,
      'network',
    );
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }

  let data: Record<string, unknown> = {};
  if (text) {
    try {
      data = JSON.parse(text) as Record<string, unknown>;
    } catch {
      throw new AuthError(`The server returned an unexpected response (${res.status}).`, res.status);
    }
  }

  if (!res.ok) {
    throw new AuthError(
      typeof data.error === 'string' ? data.error : `Request failed (${res.status})`,
      res.status,
      typeof data.code === 'string' ? data.code : undefined,
      typeof data.lockedUntil === 'string' ? data.lockedUntil : null,
    );
  }

  return data as T;
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

/**
 * A learner signed in with the shared first-sign-in password: no session was
 * opened, only their own single-use set-password link. The login page sends
 * them to it; they are signed in once they have chosen their own password.
 */
export class PasswordSetupRequired extends Error {
  setPasswordPath: string;

  constructor(setPasswordPath: string) {
    super('Choose your own password to finish signing in.');
    this.name = 'PasswordSetupRequired';
    this.setPasswordPath = setPasswordPath;
  }
}

export async function apiLogin(
  email: string,
  password: string,
  remember = false,
): Promise<AuthUser> {
  const data = await request<{ user?: AuthUser; passwordSetupRequired?: boolean; setPasswordPath?: string }>('/login/', {
    method: 'POST',
    body: JSON.stringify({ email, password, remember }),
  });
  // Only ever an in-app path from our own server; never follow anything else.
  if (data.passwordSetupRequired && data.setPasswordPath?.startsWith('/set-password?token=')) {
    throw new PasswordSetupRequired(data.setPasswordPath);
  }
  if (!data.user) throw new AuthError('Something went wrong signing in. Please try again.', 500);
  return data.user;
}

export async function apiLogout(): Promise<void> {
  await request('/logout/', { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Sign in with Microsoft
// ---------------------------------------------------------------------------

/**
 * Ask the backend where to send the browser to sign in with Microsoft.
 *
 * The URL is built server-side rather than here because it carries a signed
 * `state` only the server can mint — that signature is what stands in for the
 * `X-Requested-With` CSRF check on the callback, which arrives as a top-level
 * redirect and so cannot carry a custom header.
 *
 * The caller navigates to the result with `window.location.href`. It must be a
 * full page navigation, not a fetch: the whole point is to hand the browser to
 * Microsoft and let it come back with a `Set-Cookie`.
 *
 * Successful sign-in opens the account home; no previous-page destination is
 * sent to the provider flow.
 *
 * Throws `AuthError` with code `sso_unconfigured` (503) when the deployment has
 * no Microsoft app registration wired up.
 */
export async function apiMicrosoftStart(download?: 'android', intent?: 'lecture-feedback'): Promise<string> {
  const path = download === 'android' ? '/microsoft/start/?download=android'
    : intent === 'lecture-feedback' ? '/microsoft/start/?intent=lecture-feedback' : '/microsoft/start/';
  const data = await request<{ authorizationUrl: string }>(path);
  return data.authorizationUrl;
}

/**
 * The signed-in identity, or null when there is no live session.
 *
 * A 401 here is the normal "not signed in" answer, not an error, so it is
 * translated to null; anything else still throws.
 */
export async function apiMe(): Promise<AuthUser | null> {
  try {
    const data = await request<{ user: AuthUser }>('/me/');
    return data.user;
  } catch (err) {
    if (err instanceof AuthError && err.status === 401) return null;
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Passwords
// ---------------------------------------------------------------------------

export async function apiChangePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await request('/change-password/', {
    method: 'POST',
    body: JSON.stringify({ currentPassword, newPassword }),
  });
}

/**
 * Request a reset email. Always resolves for a well-formed address — the
 * backend deliberately does not disclose whether an account exists.
 */
export async function apiForgotPassword(email: string): Promise<string> {
  const data = await request<{ message: string }>('/forgot-password/', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
  return data.message;
}

export interface TokenInfo {
  email: string;
  displayName: string | null;
  expiresAt: string;
  /** Invitations only. */
  role?: Role;
}

export function apiResetInfo(token: string): Promise<TokenInfo> {
  return request<TokenInfo>(`/reset/?token=${encodeURIComponent(token)}`);
}

export async function apiResetPassword(token: string, password: string): Promise<string> {
  const data = await request<{ message: string }>('/reset-password/', {
    method: 'POST',
    body: JSON.stringify({ token, password }),
  });
  return data.message;
}

// ---------------------------------------------------------------------------
// Invitations
// ---------------------------------------------------------------------------

export function apiInvitationInfo(token: string): Promise<TokenInfo> {
  return request<TokenInfo>(`/invitation/?token=${encodeURIComponent(token)}`);
}

export async function apiAcceptInvitation(token: string, password: string): Promise<string> {
  const data = await request<{ message: string }>('/accept-invitation/', {
    method: 'POST',
    body: JSON.stringify({ token, password }),
  });
  return data.message;
}

export interface InviteResult {
  ok: boolean;
  accountCreated: boolean;
  emailSent: boolean;
  emailError: string | null;
  expiresAt: string;
  account: AuthUser;
}

/** (Staff) Invite or re-invite an existing person. */
export function apiInviteAccount(
  subjectType: SubjectType,
  subjectId: number,
): Promise<InviteResult> {
  return request<InviteResult>('/accounts/invite/', {
    method: 'POST',
    body: JSON.stringify({ subjectType, subjectId }),
  });
}

export interface InvitationLinkResult {
  ok: boolean;
  link: string;
  expiresAt: string;
  accountCreated: boolean;
}

/**
 * (Staff) A learner's set-password link to send another way (Teams, WhatsApp,
 * SMS) when their employer's mail filter blocks our email. Replaces any
 * earlier invitation link; never cache or log the result.
 */
export function apiInvitationLink(subjectId: number): Promise<InvitationLinkResult> {
  return request<InvitationLinkResult>('/accounts/invitation-link/', {
    method: 'POST',
    body: JSON.stringify({ subjectType: 'learner', subjectId }),
  });
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export interface AuthHealth {
  ok: boolean;
  database: { ok: boolean; error: string | null; accounts: number | null; accountsWithPassword: number | null };
  email: { configured: boolean; missing: string[] };
  /** Whether the Microsoft sign-in button can work. `missing` is names, never values. */
  microsoftSso: { configured: boolean; missing: string[] };
}

export function apiAuthHealth(): Promise<AuthHealth> {
  return request<AuthHealth>('/health/');
}

// ---------------------------------------------------------------------------
// Shared password rules
// ---------------------------------------------------------------------------
// Mirrors login/security.py so the forms can give immediate feedback. The
// server re-validates on every submit; this is a convenience, never the check.

export const MIN_PASSWORD_LENGTH = 8;

export function describePasswordProblem(password: string, email?: string): string | null {
  if (!password) return 'Password is required.';
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (new Set(password).size === 1) {
    return 'Password cannot be a single repeated character.';
  }
  if (email) {
    const local = email.split('@')[0]?.trim().toLowerCase();
    if (local && local.length >= 3 && password.toLowerCase().includes(local)) {
      return 'Password must not contain your email address.';
    }
  }
  return null;
}
