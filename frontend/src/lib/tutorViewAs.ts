/**
 * Which tutor's workspace an administrator currently has open.
 *
 * The sibling of `@/lib/coachViewAs`, and deliberately the smaller half of it.
 * An admin holds no teaching load of their own, so `/workspace/tutor` asks them
 * whose workspace to open rather than showing a dashboard belonging to nobody.
 *
 * What is missing compared with the coach store, and why: there is no
 * `withTutorViewAs` and no request parameter. Every `/coach_api/coach/*` route
 * derives its caseload from the session, so a coach selection has to be appended
 * to each request and re-authorised server-side. The tutor workspace resolves
 * its tutor from an `?email=`/`?name=` pair the caller already supplies — see
 * `api/tutorWorkspace` — so a selection is simply the identity the page asks
 * about, and there is no URL to rewrite.
 *
 * Held outside React because the selection has to survive a full navigation from
 * the tutor sidebar, and in `localStorage` for the same reason.
 */

export interface TutorViewAs {
  /**
   * The tutor whose workspace is open. Either key may be blank, but not both:
   * the workspace endpoint resolves a tutor by email OR by name, because a
   * curriculum tutor profile and a login account are separate records sharing
   * neither. A tutor added in Curriculum with no address is still openable — by
   * the name the modules carry.
   */
  email: string;
  name: string;
  /**
   * The admin who chose them. A selection is honoured only for the account that
   * made it, so a shared browser cannot leave one admin's choice applied to
   * whoever signs in next.
   */
  adminEmail: string;
}

const STORAGE_KEY = 'kbc_tutor_view_as';
const SUPER_ADMIN_ACCESS = 'super-admin';

function normalizeEmail(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

function parse(raw: string | null): TutorViewAs | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<TutorViewAs>;
    const email = normalizeEmail(parsed?.email);
    const name = String(parsed?.name || '').trim();
    const adminEmail = normalizeEmail(parsed?.adminEmail);
    if ((!email && !name) || !adminEmail) return null;
    return { email, adminEmail, name };
  } catch {
    return null;
  }
}

function readStored(): TutorViewAs | null {
  try {
    return parse(localStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

function writeStored(next: TutorViewAs | null) {
  try {
    if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // A blocked storage write is not worth failing the click over: the
    // selection still applies for this page's lifetime.
  }
}

// Cached so `tutorViewAs()` returns the same object between changes —
// `useSyncExternalStore` treats a fresh object as a new value and would
// re-render every tutor page on each read.
let current: TutorViewAs | null = readStored();
const listeners = new Set<() => void>();

function publish(next: TutorViewAs | null) {
  const unchanged = current?.email === next?.email && current?.adminEmail === next?.adminEmail
    && current?.name === next?.name;
  if (unchanged) return;
  current = next;
  listeners.forEach(listener => listener());
}

/** The active selection, or null when the workspace shows its own tutor. */
export function tutorViewAs(): TutorViewAs | null {
  return current;
}

/** Open `tutor`'s workspace. `adminEmail` is the account making the choice. */
export function setTutorViewAs(tutor: { email?: string; name?: string }, adminEmail: string) {
  const email = normalizeEmail(tutor.email);
  const name = String(tutor.name || '').trim();
  const owner = normalizeEmail(adminEmail);
  if ((!email && !name) || !owner) return;
  const next: TutorViewAs = { email, adminEmail: owner, name };
  writeStored(next);
  publish(next);
}

/** Leave the tutor's workspace and go back to the picker. */
export function clearTutorViewAs() {
  writeStored(null);
  publish(null);
}

/**
 * Drop a selection the resolved account is not entitled to.
 *
 * Called wherever the account resolves, so a tutor signing in on a browser an
 * admin used does not open somebody else's workspace by inheritance.
 */
export function syncTutorViewAsAccount(account: { email?: string; access?: string | null } | null) {
  if (!current) return;
  const access = String(account?.access || '').trim().toLowerCase();
  const email = normalizeEmail(account?.email);
  if (access !== SUPER_ADMIN_ACCESS || email !== current.adminEmail) clearTutorViewAs();
}

// One listener for the whole app rather than one per subscriber: switching tutor
// in another tab has to move every open page, and `publish` already ignores a
// value that has not changed.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', event => {
    if (event.key === STORAGE_KEY) publish(parse(event.newValue));
  });
}

/** Subscribe to selection changes, including those made in another tab. */
export function subscribeTutorViewAs(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Test seam: re-read storage after a test replaces it. */
export function reloadTutorViewAs() {
  publish(readStored());
}
