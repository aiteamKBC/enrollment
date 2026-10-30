/**
 * Report what a person does anywhere in the LMS, for the Audit Trail's People
 * view.
 *
 * The change trail is written by the backend's own save helpers, so it already
 * knows every edit it covers. What it cannot know is the reading: somebody
 * opening a coach's caseload, searching it and leaving touches no record, so
 * nothing anywhere records that it happened. That half has to be reported by
 * the browser, and this is the only place that does it.
 *
 * Three rules this file exists to keep:
 *
 * 1. **It never affects the page.** Every failure is swallowed, the queue is
 *    bounded, and nothing here is awaited by a render or a save. If the
 *    endpoint is down, or the table has not been created yet, the person
 *    notices nothing at all.
 * 2. **It says nothing the server must believe.** The account, the page name,
 *    the workspace and the record id in a URL are all resolved server-side
 *    against `system_audit/pages.py`. What goes up is the route, the kind of
 *    action, and short descriptive detail.
 * 3. **It reports actions, never content.** A search records that a search
 *    happened and the term typed; it never records the results, the payload or
 *    anything on screen.
 *
 * Scope is the whole LMS, minus `EXCLUDED_PREFIXES` below. The exclusions are
 * mirrored server-side in `system_audit/pages.py` and enforced there too: what
 * the LMS does not record is a decision, and a decision like that cannot live
 * only in code the client could skip.
 */

/** Matches `curriculumApi.ts` — the SPA talks to the same origin-relative base. */
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
const RECORD_PATH = '/activity/record/';

const VISIT_KEY = 'kbc_lms_visit';

/**
 * Routes that are never recorded. Kept in step with `EXCLUDED` in
 * `backend/system_audit/pages.py`, which is the copy that actually decides —
 * this one only saves the request.
 *
 * - The signed-out pages: there is no account to attribute them to, and
 *   `login."Login_audit"` already records sign-ins, resets and failed attempts
 *   properly, including against addresses that have no account.
 * - The learner content runner: a learner working through material would
 *   produce one page open per quiz, video or component and swamp the table,
 *   and it would say less about their learning than their own progress records
 *   already say. The rest of the learner workspace is still recorded.
 */
const EXCLUDED_PREFIXES = [
  '/login',
  '/forgot-password',
  '/reset-password',
  '/set-password',
  '/verify-certificate',
  '/verify-personal-certificate',
  // Event QR check-in and guest feedback are bearer-token public pages. An
  // audit write here would call the authenticated Curriculum API and its 401
  // would look like an expired LMS session to the global session handler.
  '/event-check-in',
  '/event-feedback',
  '/event-rsvp',
  // Public booking pages must not call the authenticated audit endpoint.
  '/coach-booking/',
  // Neither must the public apprenticeship application.
  '/apprenticeship_application',
  '/learner/quiz/',
  '/learner/video/',
  '/learner/component/',
  '/learner/historical-assignment/',
  '/learner/monthly-submission/',
  '/old-otjh',
];

/** What the backend accepts. Anything else is dropped before it is sent. */
export type ActivityKind =
  | 'page_view'
  | 'search'
  | 'filter'
  | 'sort'
  | 'export'
  | 'download'
  | 'record_view'
  | 'tab'
  | 'print';

export interface ActivityDetail {
  query?: string;
  filter?: string;
  value?: string;
  tab?: string;
  sort?: string;
  format?: string;
  fileName?: string;
  count?: string | number;
  scope?: string;
}

interface QueuedEvent {
  kind: ActivityKind;
  path: string;
  at: string;
  visitId: string;
  targetType?: string;
  targetId?: string;
  targetLabel?: string;
  durationMs?: number;
  detail?: ActivityDetail;
}

/**
 * Batched rather than sent per event: a page with a filter bar produces a small
 * burst as somebody types, and one request per keystroke would be both wasteful
 * and a worse record than one request carrying the burst in order.
 */
const FLUSH_DELAY_MS = 2000;
const FLUSH_AT_QUEUE_SIZE = 12;
/** A hard ceiling so a long offline spell cannot grow memory without bound. */
const MAX_QUEUE = 60;

let queue: QueuedEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;
/**
 * Set once the server says the activity table does not exist. Recording then
 * stops for the rest of this page's life rather than posting into the void on
 * every navigation; a reload asks again, which is what makes the table appear
 * without a redeploy.
 */
let recordingDisabled = false;
let listenersBound = false;

let currentPath = '';
let currentPathOpenedAt = 0;

/** True when this route is one the LMS records at all. */
export function isRecordedPath(path: string): boolean {
  const clean = String(path || '').split('?')[0].split('#')[0];
  if (!clean.startsWith('/')) return false;
  return !EXCLUDED_PREFIXES.some(prefix => (
    prefix.endsWith('/')
      ? clean.startsWith(prefix)
      : clean === prefix || clean.startsWith(`${prefix}/`)
  ));
}

/**
 * One sitting in one browser tab.
 *
 * `sessionStorage`, so a second tab is a second visit — which is what it is —
 * and a reload of the same tab continues the one already in progress. A browser
 * that refuses storage still gets a working id; it just starts a new visit on
 * every page load, which is a coarser record rather than a broken one.
 */
function visitId(): string {
  try {
    const existing = sessionStorage.getItem(VISIT_KEY);
    if (existing) return existing;
    const created = newId();
    sessionStorage.setItem(VISIT_KEY, created);
    return created;
  } catch {
    if (!fallbackVisitId) fallbackVisitId = newId();
    return fallbackVisitId;
  }
}

let fallbackVisitId = '';

function newId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch {
    // Falls through to the timestamp id below.
  }
  return `v${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function enqueue(event: QueuedEvent): void {
  if (recordingDisabled) return;
  if (queue.length >= MAX_QUEUE) {
    // Drop the oldest, not the newest: the recent past is what a reader of the
    // trail is looking at, and a full queue means something is already wrong.
    queue.shift();
  }
  queue.push(event);
  bindUnloadFlush();
  if (queue.length >= FLUSH_AT_QUEUE_SIZE) {
    flushActivity();
    return;
  }
  if (flushTimer === null) {
    flushTimer = setTimeout(() => {
      flushTimer = null;
      flushActivity();
    }, FLUSH_DELAY_MS);
  }
}

/**
 * Send what is queued.
 *
 * `keepalive` so a flush started as the tab closes is still delivered; the
 * browser caps such a body at 64KB, which this never approaches. On the unload
 * path `sendBeacon` is used instead, because it is the one send that survives a
 * document being torn down mid-request.
 */
export function flushActivity(useBeacon = false): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (!queue.length || recordingDisabled) return;
  const events = queue;
  queue = [];
  const body = JSON.stringify({ visitId: visitId(), events });
  const url = `${API_BASE_URL}${RECORD_PATH}`;

  if (useBeacon && typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
    try {
      // A typed Blob rather than a bare string: without the type the browser
      // sends text/plain, which some proxies treat differently. Django reads
      // the body either way.
      navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }));
    } catch {
      // A refused beacon is a lost note, never an error the person sees.
    }
    return;
  }

  try {
    void fetch(url, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body,
    })
      .then(response => {
        // Signed out, or not a role this prefix serves. Retrying on every
        // navigation would achieve nothing but a 403 per click.
        if (response.status === 401 || response.status === 403) {
          recordingDisabled = true;
          return null;
        }
        return response.ok ? response.json() : null;
      })
      .then(payload => {
        // The table is created by hand against Neon. Until it exists there is
        // nothing to write to, and saying so once is better than a failed
        // request per navigation for the rest of the session.
        if (payload && payload.available === false) recordingDisabled = true;
      })
      .catch(() => {
        // Offline, aborted, blocked: all the same answer. The page carries on.
      });
  } catch {
    // Some embedded webviews throw synchronously on fetch. Same answer.
  }
}

function bindUnloadFlush(): void {
  if (listenersBound || typeof window === 'undefined') return;
  listenersBound = true;
  // `pagehide` and a hidden `visibilitychange` between them cover the cases
  // `beforeunload` misses on mobile, where a backgrounded tab is often never
  // formally unloaded at all.
  const send = () => {
    closeCurrentPage();
    flushActivity(true);
  };
  window.addEventListener('pagehide', send);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') send();
  });
}

/** Record how long the page being left was open, if one was. */
function closeCurrentPage(): void {
  if (!currentPath || !currentPathOpenedAt) return;
  const durationMs = Date.now() - currentPathOpenedAt;
  currentPathOpenedAt = 0;
  const leaving = currentPath;
  currentPath = '';
  if (durationMs < 250) return;
  enqueue({
    kind: 'page_view',
    path: leaving,
    at: new Date().toISOString(),
    visitId: visitId(),
    durationMs,
  });
}

/**
 * A page was opened.
 *
 * Called once per navigation from the router, for every workspace. The arrival
 * is sent immediately; when the page is left, the same path is sent again
 * carrying how long it was open, which the backend uses to CLOSE the arrival
 * rather than to add a second entry. A tab closed without warning simply never
 * sends the second one, and the page keeps an honest "opened, duration unknown"
 * instead of a guess.
 */
export function recordPageView(path: string, targetLabel = ''): void {
  if (typeof window === 'undefined') return;
  const clean = String(path || '').split('?')[0].split('#')[0];
  if (!isRecordedPath(clean)) {
    // Moved onto a route the LMS does not record: close the page that was open,
    // then stop. The visit so far is still reported.
    closeCurrentPage();
    flushActivity();
    return;
  }
  if (clean === currentPath) return;
  closeCurrentPage();
  currentPath = clean;
  currentPathOpenedAt = Date.now();
  enqueue({
    kind: 'page_view',
    path: clean,
    at: new Date().toISOString(),
    visitId: visitId(),
    targetLabel: targetLabel || undefined,
  });
}

/**
 * Something was done on the page that is not a save: a search, a filter, an
 * export, a download.
 *
 * Saves are deliberately not reportable here. They are recorded by the backend
 * write helpers with a before and an after, and a browser-declared edit in the
 * audit trail would be an unverified claim sitting beside verified ones.
 */
export function recordAction(
  kind: Exclude<ActivityKind, 'page_view'>,
  detail: ActivityDetail = {},
  target: { type?: string; id?: string; label?: string } = {},
): void {
  if (typeof window === 'undefined') return;
  // Only ever against the page this recorder knows is open — never against
  // `location.pathname`. Reading the address bar instead would attribute an
  // action to whatever route the browser happened to be showing, and on an
  // excluded route (a sign-in page, the content runner) that is exactly the
  // recording the exclusion list exists to prevent. No open recorded page means
  // there is no page to name, so nothing is recorded rather than guessed.
  if (!currentPath) return;
  enqueue({
    kind,
    path: currentPath,
    at: new Date().toISOString(),
    visitId: visitId(),
    targetType: target.type,
    targetId: target.id,
    targetLabel: target.label,
    detail,
  });
}

/**
 * A search box, reported once the typing stops.
 *
 * Per-keystroke rows would say nothing useful and would bury the rest of the
 * trail, so each box reports the term it settled on. Keyed by name so two boxes
 * on one page do not cancel each other's timer.
 */
const searchTimers = new Map<string, ReturnType<typeof setTimeout>>();
const SEARCH_SETTLE_MS = 1200;

export function recordSearch(query: string, scope = 'search'): void {
  const existing = searchTimers.get(scope);
  if (existing) clearTimeout(existing);
  const text = String(query || '').trim();
  if (!text) return;
  searchTimers.set(scope, setTimeout(() => {
    searchTimers.delete(scope);
    recordAction('search', { query: text, scope });
  }, SEARCH_SETTLE_MS));
}

/** Test seam: forget everything this module is holding. */
export function resetActivity(): void {
  queue = [];
  if (flushTimer !== null) clearTimeout(flushTimer);
  flushTimer = null;
  recordingDisabled = false;
  currentPath = '';
  currentPathOpenedAt = 0;
  searchTimers.forEach(timer => clearTimeout(timer));
  searchTimers.clear();
}
