/** Opt-in page/request diagnostics. The server alone decides who is enabled. */

const API_PATH = /^\/(?:curriculum_api|coach_api|learner_api|enrolment_api|quiz_api|engagement_api|audit_api|manual_audit_api|hours_test_api|progress_reviews_api|login_api|api)\//;
const RECORD_URL = '/curriculum_api/performance/record/';
const DIAGNOSTIC_HEADER = 'X-LMS-Performance-Diagnostic';
const QUIET_WINDOW_MS = 500;
const INITIAL_GRACE_MS = 750;
const MAX_NAVIGATION_MS = 119_000;
const MAX_REQUESTS = 100;
// How long a finished sample may wait for the current page's reads to settle.
const RECORD_MAX_DEFER_MS = 30_000;
const RECORD_RETRY_MS = 500;

interface RequestTiming {
  method: 'GET' | 'HEAD';
  path: string;
  status: number;
  durationMs: number;
  requestId?: string;
  queryCount?: number;
  dbMs?: number;
  serverMs?: number;
  cacheStatus?: 'HIT' | 'MISS';
}

interface NavigationSample {
  token: number;
  path: string;
  startedAt: number;
  cold: boolean;
  pending: number;
  closing: boolean;
  sent: boolean;
  requests: RequestTiming[];
  quietTimer: ReturnType<typeof setTimeout> | null;
  hardTimer: ReturnType<typeof setTimeout> | null;
}

let installed = false;
let diagnosticEnabled = false;
let tokenSequence = 0;
let current: NavigationSample | null = null;
let baseFetch: typeof fetch | null = null;
let domObserver: MutationObserver | null = null;
const seenPaths = new Set<string>();
const queuedRecords = new Set<string>();

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

function storageValue(key: string, create: () => string): string {
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const value = create();
    sessionStorage.setItem(key, value);
    return value;
  } catch {
    return create();
  }
}

function randomId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  }
}

function runId(): string {
  return storageValue('kbc_performance_run', randomId);
}

function cleanRoute(path: string): string {
  const clean = String(path || '').split('?')[0].split('#')[0].replace(/\/$/, '');
  return clean || '/';
}

function isApiGet(input: RequestInfo | URL, init?: RequestInit): { method: 'GET' | 'HEAD'; path: string } | null {
  if (typeof location === 'undefined') return null;
  const request = input instanceof Request ? input : null;
  const method = String(init?.method || request?.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') return null;
  const url = new URL(request?.url || String(input), location.href);
  if (url.origin !== location.origin || !API_PATH.test(url.pathname) || url.pathname === RECORD_URL) return null;
  return { method, path: url.pathname } as { method: 'GET' | 'HEAD'; path: string };
}

function timingValue(header: string | null, metric: string): number | undefined {
  if (!header) return undefined;
  const match = header.match(new RegExp(`(?:^|,)\\s*${metric};dur=([0-9.]+)`, 'i'));
  if (!match) return undefined;
  const value = Number(match[1]);
  return Number.isFinite(value) ? round(value) : undefined;
}

function scheduleQuiet(sample: NavigationSample, delay = QUIET_WINDOW_MS): void {
  if (sample.sent || sample.pending > 0) return;
  if (sample.quietTimer !== null) clearTimeout(sample.quietTimer);
  const initialGraceRemaining = Math.max(0, INITIAL_GRACE_MS - (performance.now() - sample.startedAt));
  sample.quietTimer = setTimeout(() => finish(sample), Math.max(delay, initialGraceRemaining));
}

function finish(sample: NavigationSample, force = false): void {
  if (sample.sent || (!force && sample.pending > 0)) {
    sample.closing = true;
    return;
  }
  sample.sent = true;
  if (sample.quietTimer !== null) clearTimeout(sample.quietTimer);
  if (sample.hardTimer !== null) clearTimeout(sample.hardTimer);
  if (!diagnosticEnabled || !baseFetch) return;
  const body = JSON.stringify({
    runId: runId(),
    path: sample.path,
    cold: sample.cold,
    pageReadyMs: round(performance.now() - sample.startedAt),
    requests: sample.requests.slice(0, MAX_REQUESTS),
  });
  dispatchRecord(body, performance.now());
}

/**
 * Send a finished sample without competing with the page that is loading now.
 *
 * A sample usually finishes because the user navigated: the previous page is
 * closed at the exact moment the next one starts its own API reads. Posting
 * then put the diagnostic write in the same queue as those reads -- the
 * browser runs a handful of requests per host at a time, and on a slow page
 * the record waited behind them for tens of seconds while holding a slot.
 *
 * So a record waits while the current page still has API reads in flight (up
 * to RECORD_MAX_DEFER_MS, after which it goes anyway), is sent at low priority,
 * and anything still queued when the tab is hidden or closed is flushed then;
 * `keepalive` lets that last send outlive the page.
 */
function dispatchRecord(body: string, queuedAt: number): void {
  const busy = current !== null && !current.sent && current.pending > 0;
  if (busy && performance.now() - queuedAt < RECORD_MAX_DEFER_MS) {
    queuedRecords.add(body);
    setTimeout(() => {
      if (!queuedRecords.has(body)) return;
      queuedRecords.delete(body);
      dispatchRecord(body, queuedAt);
    }, RECORD_RETRY_MS);
    return;
  }
  queuedRecords.delete(body);
  sendRecord(body);
}

function sendRecord(body: string): void {
  if (!baseFetch) return;
  // `priority` is a fetch hint the browser may ignore; it is not in every
  // TypeScript DOM lib yet, hence the widened type.
  const init: RequestInit & { priority?: 'high' | 'low' | 'auto' } = {
    method: 'POST',
    credentials: 'include',
    keepalive: true,
    priority: 'low',
    headers: { 'Content-Type': 'application/json' },
    body,
  };
  try {
    void baseFetch(RECORD_URL, init).catch(() => undefined);
  } catch {
    // Diagnostics can never affect navigation.
  }
}

function flushQueuedRecords(): void {
  const bodies = [...queuedRecords];
  queuedRecords.clear();
  bodies.forEach(sendRecord);
}

function settle(sample: NavigationSample, detail: RequestTiming): void {
  sample.pending = Math.max(0, sample.pending - 1);
  if (sample.requests.length < MAX_REQUESTS) sample.requests.push(detail);
  if (sample.pending === 0) scheduleQuiet(sample, sample.closing ? 0 : QUIET_WINDOW_MS);
}

export function installPerformanceDiagnostics(): () => void {
  if (installed || typeof globalThis.fetch !== 'function' || typeof location === 'undefined') return () => {};
  installed = true;
  const original = globalThis.fetch;
  baseFetch = original;
  const wrapped: typeof fetch = async (input, init) => {
    const tracked = isApiGet(input, init);
    const sample = tracked ? current : null;
    const startedAt = performance.now();
    if (sample && !sample.sent) {
      sample.pending += 1;
      if (sample.quietTimer !== null) clearTimeout(sample.quietTimer);
    }
    try {
      const response = await original(input, init);
      if (response.headers.get(DIAGNOSTIC_HEADER) === '1') diagnosticEnabled = true;
      if (sample && tracked && !sample.sent) {
        const timing = response.headers.get('Server-Timing');
        const queryHeader = response.headers.get('X-DB-Query-Count');
        const queryCount = queryHeader === null ? undefined : Number(queryHeader);
        const cacheHeader = response.headers.get('X-LMS-Cache')?.toUpperCase();
        settle(sample, {
          method: tracked.method,
          path: tracked.path,
          status: response.status,
          durationMs: round(performance.now() - startedAt),
          requestId: response.headers.get('X-Request-ID') || undefined,
          queryCount: Number.isFinite(queryCount) ? queryCount : undefined,
          dbMs: timingValue(timing, 'db'),
          serverMs: timingValue(timing, 'total'),
          cacheStatus: cacheHeader === 'HIT' || cacheHeader === 'MISS' ? cacheHeader : undefined,
        });
      }
      return response;
    } catch (error) {
      if (sample && tracked && !sample.sent) {
        settle(sample, {
          method: tracked.method,
          path: tracked.path,
          status: 0,
          durationMs: round(performance.now() - startedAt),
        });
      }
      throw error;
    }
  };
  globalThis.fetch = wrapped;
  const flushOnHide = () => {
    if (typeof document === 'undefined' || document.visibilityState === 'hidden') flushQueuedRecords();
  };
  if (typeof window !== 'undefined') window.addEventListener('pagehide', flushQueuedRecords);
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', flushOnHide);
  if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
    domObserver = new MutationObserver(() => {
      if (current && !current.sent) scheduleQuiet(current);
    });
    domObserver.observe(document.documentElement, { childList: true, characterData: true, subtree: true });
  }
  return () => {
    if (globalThis.fetch === wrapped) globalThis.fetch = original;
    if (typeof window !== 'undefined') window.removeEventListener('pagehide', flushQueuedRecords);
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', flushOnHide);
    installed = false;
    baseFetch = null;
    domObserver?.disconnect();
    domObserver = null;
  };
}

export function recordPerformanceNavigation(path: string): void {
  if (typeof performance === 'undefined') return;
  const clean = cleanRoute(path);
  if (current && current.path === clean) return;
  if (current && !current.sent) {
    current.closing = true;
    scheduleQuiet(current, current.pending ? QUIET_WINDOW_MS : 0);
  }
  const cold = !seenPaths.has(clean);
  seenPaths.add(clean);
  const sample: NavigationSample = {
    token: ++tokenSequence,
    path: clean,
    startedAt: performance.now(),
    cold,
    pending: 0,
    closing: false,
    sent: false,
    requests: [],
    quietTimer: null,
    hardTimer: null,
  };
  current = sample;
  sample.hardTimer = setTimeout(() => finish(sample, true), MAX_NAVIGATION_MS);
  scheduleQuiet(sample, INITIAL_GRACE_MS);
}

export function resetPerformanceDiagnostics(): void {
  if (current?.quietTimer !== null && current?.quietTimer !== undefined) clearTimeout(current.quietTimer);
  if (current?.hardTimer !== null && current?.hardTimer !== undefined) clearTimeout(current.hardTimer);
  current = null;
  tokenSequence = 0;
  diagnosticEnabled = false;
  seenPaths.clear();
  queuedRecords.clear();
}

