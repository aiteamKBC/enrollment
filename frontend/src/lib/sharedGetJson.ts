interface SharedGetJsonInit extends Omit<globalThis.RequestInit, 'body' | 'method' | 'signal'> {
  signal?: AbortSignal;
}

const inFlightGets = new Map<string, Promise<unknown>>();

function abortError(): DOMException {
  return new DOMException('The operation was aborted.', 'AbortError');
}

function normalizeHeaders(headers?: globalThis.HeadersInit): Array<[string, string]> {
  if (!headers) return [];
  return Array.from(new Headers(headers).entries()).sort(([left], [right]) => left.localeCompare(right));
}

function buildRequestKey(url: string, init?: Omit<SharedGetJsonInit, 'signal'>): string {
  return JSON.stringify({
    url,
    cache: init?.cache,
    credentials: init?.credentials,
    headers: normalizeHeaders(init?.headers),
    integrity: init?.integrity,
    keepalive: init?.keepalive,
    mode: init?.mode,
    redirect: init?.redirect,
    referrer: init?.referrer,
    referrerPolicy: init?.referrerPolicy,
  });
}

async function readJsonResponse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : `Request failed with ${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

function settleWithCallerAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(abortError());
    signal.addEventListener('abort', abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

export function clearSharedGetJsonState() {
  inFlightGets.clear();
}

// Shares only in-flight GETs so React StrictMode remounts do not spam DevTools
// with "(canceled)" rows for requests that the next render still needs.
export function fetchSharedJsonGet<T>(url: string, init?: SharedGetJsonInit): Promise<T> {
  const { signal, ...fetchInit } = init || {};
  const key = buildRequestKey(url, fetchInit);
  const existing = inFlightGets.get(key) as Promise<T> | undefined;
  const pending = existing || fetch(url, fetchInit).then(response => readJsonResponse<T>(response));

  if (!existing) {
    inFlightGets.set(key, pending as Promise<unknown>);
    const clearInFlight = () => {
      if (inFlightGets.get(key) === (pending as Promise<unknown>)) {
        inFlightGets.delete(key);
      }
    };
    pending.then(clearInFlight, clearInFlight);
  }

  return settleWithCallerAbort(pending, signal);
}
