/**
 * Which coach's workspace an administrator currently has open.
 *
 * The coach workspace is scoped entirely by the signed-in account: every
 * `/coach_api/coach/*` route derives the caseload from the session, which is
 * why an admin used to land on an empty dashboard reading "Coach access is
 * required". An admin instead picks a coach, and that choice travels on each
 * request as `viewAsCoach` — the server still decides what may be read, and
 * refuses to write through it (see `coach_api/auth.py`).
 *
 * Held here rather than in React state because `coachFetch` has to reach it
 * without a hook, and in `localStorage` so opening a page from the coach
 * sidebar — a full navigation, not a route change — keeps the same coach.
 */

export interface CoachViewAs {
  /** The coach whose data the workspace shows. */
  email: string;
  name: string;
  /**
   * The admin who chose them. A selection is honoured only for the account that
   * made it, so a shared browser cannot leave one admin's choice applied to
   * whoever signs in next.
   */
  adminEmail: string;
}

const STORAGE_KEY = 'kbc_coach_view_as';
const VIEW_AS_PARAM = 'viewAsCoach';

/**
 * Only the coach-scoped routes take the parameter. `/coach_api/csrf` and
 * `/coach_api/coaches` are the caller's own identity and the admin directory.
 */
const COACH_SCOPED_PREFIX = '/coach_api/coach/';

const SUPER_ADMIN_ACCESS = 'super-admin';

function normalizeEmail(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

function parse(raw: string | null): CoachViewAs | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<CoachViewAs>;
    const email = normalizeEmail(parsed?.email);
    const adminEmail = normalizeEmail(parsed?.adminEmail);
    if (!email || !adminEmail) return null;
    return { email, adminEmail, name: String(parsed?.name || '').trim() || email };
  } catch {
    return null;
  }
}

function readStored(): CoachViewAs | null {
  try {
    return parse(localStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

function writeStored(next: CoachViewAs | null) {
  try {
    if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // A blocked storage write is not worth failing the click over: the
    // selection still applies for this page's lifetime.
  }
}

// Cached so `coachViewAs()` returns the same object between changes —
// `useSyncExternalStore` treats a fresh object as a new value and re-renders
// every coach page on each read.
let current: CoachViewAs | null = readStored();
const listeners = new Set<() => void>();

function publish(next: CoachViewAs | null) {
  const unchanged = current?.email === next?.email && current?.adminEmail === next?.adminEmail
    && current?.name === next?.name;
  if (unchanged) return;
  current = next;
  listeners.forEach(listener => listener());
}

/** The active selection, or null when the workspace shows its own coach. */
export function coachViewAs(): CoachViewAs | null {
  return current;
}

/** Open `coach`'s workspace. `adminEmail` is the account making the choice. */
export function setCoachViewAs(coach: { email: string; name?: string }, adminEmail: string) {
  const email = normalizeEmail(coach.email);
  const owner = normalizeEmail(adminEmail);
  if (!email || !owner) return;
  const next: CoachViewAs = { email, adminEmail: owner, name: String(coach.name || '').trim() || email };
  writeStored(next);
  publish(next);
}

/** Leave the coach's workspace and go back to the picker. */
export function clearCoachViewAs() {
  writeStored(null);
  publish(null);
}

/**
 * Drop a selection the resolved account is not entitled to.
 *
 * Called from the one place every sign-in, session restore and refresh passes
 * through, so a coach signing in on a browser an admin used is never left
 * requesting somebody else's caseload — the server would refuse it as an
 * identity mismatch, and their own workspace would look broken.
 */
export function syncCoachViewAsAccount(account: { email?: string; access?: string | null } | null) {
  if (!current) return;
  const access = String(account?.access || '').trim().toLowerCase();
  const email = normalizeEmail(account?.email);
  if (access !== SUPER_ADMIN_ACCESS || email !== current.adminEmail) clearCoachViewAs();
}

// One listener for the whole app rather than one per subscriber: switching
// coach in another tab has to move every open page, and `publish` already
// ignores a value that has not changed.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', event => {
    if (event.key === STORAGE_KEY) publish(parse(event.newValue));
  });
}

/** Subscribe to selection changes, including those made in another tab. */
export function subscribeCoachViewAs(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function isCoachScoped(target: string): boolean {
  const path = target.split('?')[0].split('#')[0];
  return path.startsWith(COACH_SCOPED_PREFIX)
    || /^[a-z]+:\/\/[^/]+\/coach_api\/coach\//i.test(path);
}

/**
 * Add the active selection to a coach-scoped URL.
 *
 * A no-op when no coach is selected, so a coach's own requests are byte-for-byte
 * what they were before this existed.
 */
export function withCoachViewAs(url: string): string {
  const selection = current;
  if (!selection || !isCoachScoped(url)) return url;

  const hashAt = url.indexOf('#');
  const base = hashAt === -1 ? url : url.slice(0, hashAt);
  const hash = hashAt === -1 ? '' : url.slice(hashAt);
  if (new RegExp(`[?&]${VIEW_AS_PARAM}=`).test(base)) return url;

  const separator = base.includes('?') ? '&' : '?';
  return `${base}${separator}${VIEW_AS_PARAM}=${encodeURIComponent(selection.email)}${hash}`;
}

/** Test seam: re-read storage after a test replaces it. */
export function reloadCoachViewAs() {
  publish(readStored());
}
