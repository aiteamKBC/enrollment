import { learningFetch } from '@/lib/personalLearning';
import { createCachedResource, invalidateCachedResource } from './cachedRequest';

export const LEARNER_READ_TIMEOUT_MS = 45_000;
export const LEARNER_SOURCE_READ_TIMEOUT_MS = 180_000;
export const MY_LEARNING_READ_TIMEOUT_MS = 120_000;
const DASHBOARD_OVERVIEW_READ_TIMEOUT_MS = 120_000;
const TRANSIENT_READ_RETRY_DELAY_MS = 600;

export class LearnerReadError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status = 0, code?: string) {
    super(message);
    this.name = 'LearnerReadError';
    this.status = status;
    this.code = code;
  }
}

type ReadOptions = Pick<globalThis.RequestInit, 'headers' | 'credentials' | 'cache' | 'signal'> & {
  force?: boolean;
  revalidate?: boolean;
  /** Default: zero (only share in-flight reads). Opt in to 30s snapshots. */
  ttlMs?: 0 | 30_000;
};

function aborted() { return new DOMException('The operation was aborted.', 'AbortError'); }

function waitForRetry(signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(aborted());
  return new Promise((resolve, reject) => {
    const timer = setTimeout(done, TRANSIENT_READ_RETRY_DELAY_MS);
    function done() {
      signal.removeEventListener('abort', cancel);
      resolve();
    }
    function cancel() {
      clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
      reject(aborted());
    }
    signal.addEventListener('abort', cancel, { once: true });
  });
}

export function withCallerSignal<T>(pending: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return pending;
  if (signal.aborted) return Promise.reject(aborted());
  return new Promise((resolve, reject) => {
    const abort = () => reject(aborted());
    signal.addEventListener('abort', abort, { once: true });
    pending.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

async function fetchJson(key: string): Promise<unknown> {
  const [url, headers, credentials, cache] = JSON.parse(key) as [
    string,
    [string, string][],
    NonNullable<globalThis.RequestInit['credentials']>,
    NonNullable<globalThis.RequestInit['cache']>,
  ];
  // Previous-learning reads can verify several original accounts and courses.
  // Keep their shared request alive while the bounded upstream reads finish.
  // My Learning aggregates the whole plan. Its deadline must cover the local
  // server's 65s connection checkout plus projection time, not abort at 45s
  // while that server is still legitimately waiting for a connection.
  // Overview also aggregates the complete plan, progress and saved registers;
  // a successful large-plan read can exceed 45s. Keep its deadline bounded
  // without extending the deadlines of other dashboard or calendar requests.
  const timeoutMs = /^\/learner_api\/my-learning\//.test(url)
    ? MY_LEARNING_READ_TIMEOUT_MS
    : /^\/learner_api\/dashboard\/(commercial|apprenticeship)\/[^/?]+\/overview\/(?:\?|$)/.test(url)
      ? DASHBOARD_OVERVIEW_READ_TIMEOUT_MS
      : /^\/learner_api\/(student-activity|metrics)\//.test(url)
        ? LEARNER_SOURCE_READ_TIMEOUT_MS : LEARNER_READ_TIMEOUT_MS;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      reject(new LearnerReadError('Loading is taking longer than expected. Please try again.', 0, 'timeout'));
      controller.abort();
    }, timeoutMs);
  });
  const loadOnce = async () => {
    let response: Response;
    try {
      response = await learningFetch(url, { headers: Object.fromEntries(headers), credentials, cache, signal: controller.signal });
    } catch {
      throw new LearnerReadError('Could not reach the server. Please check your connection and try again.', 0, 'network');
    }
    let data: unknown;
    try { data = await response.json(); }
    catch { throw new LearnerReadError(`The server returned an invalid response (${response.status}).`, response.status); }
    if (!response.ok) {
      const body = data as { error?: string; detail?: string; code?: string } | null;
      throw new LearnerReadError(body?.error || body?.detail || `Request failed (${response.status}).`, response.status, body?.code);
    }
    if (data == null) throw new LearnerReadError('The server returned an empty response.', response.status);
    return data;
  };
  const load = async () => {
    try {
      return await loadOnce();
    } catch (error) {
      const transient = error instanceof LearnerReadError
        && (error.status >= 500 || error.status === 429 || error.code === 'network');
      if (!transient || controller.signal.aborted) throw error;
      await waitForRetry(controller.signal);
      return loadOnce();
    }
  };
  try { return await Promise.race([load(), deadline]); }
  finally { clearTimeout(timer!); }
}

const cached = createCachedResource('learner-json', fetchJson);
const live = createCachedResource('learner-json-live', fetchJson, 0);

/** Share a read across panels and StrictMode, while cancelling only its caller.
 * Cached responses stay in memory for 30 seconds and are keyed by the complete
 * URL and request headers, so learners, filters and staff previews stay apart. */
export function readLearnerJson<T>(url: string, options: ReadOptions = {}): Promise<T> {
  if (options.signal?.aborted) return Promise.reject(aborted());
  const key = readKey(url, options);
  const resource = options.ttlMs === 30_000 ? cached : live;
  return withCallerSignal(resource.read(key, { force: options.force, revalidate: options.revalidate }) as Promise<T>, options.signal);
}

function readKey(url: string, options: ReadOptions): string {
  const headers = [...new Headers(options.headers).entries()].sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify([url, headers, options.credentials ?? 'include', options.cache ?? 'no-store']);
}

/** Display a recent snapshot while readLearnerJson independently refreshes it.
 * Only opt-in cached reads are eligible; live permissions/tokens never are. */
export function peekLearnerJson<T>(url: string, options: ReadOptions = {}): T | undefined {
  return cached.peek(readKey(url, options), 5 * 60_000) as T | undefined;
}

/** Drop shared JSON reads after a successful action. Callers also invalidate
 * their own detail/calendar resource when that action changes those records. */
const invalidationListeners = new Set<() => void>();

export function subscribeLearnerReadInvalidation(listener: () => void): () => void {
  invalidationListeners.add(listener);
  return () => { invalidationListeners.delete(listener); };
}

export function invalidateLearnerReads(): void {
  invalidateCachedResource('coach-case-file-session');
  cached.invalidate();
  live.invalidate();
  invalidationListeners.forEach(listener => listener());
}
