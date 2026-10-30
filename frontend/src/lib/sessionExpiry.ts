/**
 * Notice when the server stops recognising this browser, and say so once.
 *
 * The problem this solves is new. The API used to answer anonymous callers, so
 * a lapsed session mostly went unnoticed; now that `login/api_gate.py` refuses
 * them, an expired or revoked session turns every panel on the page into an
 * error at the same moment, with nothing telling the person why. Sessions do
 * lapse — they expire, an administrator revokes one, the account is
 * deactivated, or the person signed out in another tab.
 *
 * Catching it per call site is not realistic: roughly forty modules under
 * `api/` and `lib/` each own their own `fetch`, and a new one is written most
 * weeks. So this wraps `window.fetch` itself — the one place every request
 * already passes through — and reports a 401 from a gated API prefix to a
 * single listener, which `AuthProvider` registers.
 *
 * Three things it is careful about:
 *
 *  - **It only reads `response.status`.** The response is handed back
 *    untouched, so no caller loses its body and nothing needs `clone()`.
 *  - **`/login_api/` is excluded.** `apiMe` 401s on every page load for a
 *    signed-out visitor, and a wrong password 401s too. Both are normal answers
 *    from the endpoint that issues sessions, not evidence one has ended.
 *  - **It fires once.** A dashboard opening twelve panels gets twelve 401s
 *    within a few milliseconds; the person should be told once, not bounced
 *    twelve times. `notified` latches until `resetSessionExpiryNotice`, which
 *    the next successful sign-in calls.
 *
 * A 403 is deliberately not treated as expiry: the session is fine, the account
 * is simply not admitted to that API. Signing someone out for it would hide a
 * routing bug behind a logout.
 */

type Listener = () => void;

/**
 * Prefixes whose 401 means "your session has ended".
 *
 * Mirrors `RULES` in backend `login/api_gate.py`, minus the entries whose 401s
 * mean something else: `/login_api/` (see above) and `/api/chat/`, which
 * authenticates on a separate Django session, so its 401 says nothing about
 * this one.
 */
const SESSION_API_PREFIXES = [
  '/curriculum_api/',
  '/coach_api/',
  '/quiz_api/',
  '/learner_api/',
  '/audit_api/',
  '/hours_test_api/',
  '/manual_audit_api/',
  '/engagement_api/',
  '/enrolment_api/',
  '/api/batch/',
];

// These Engagement endpoints deliberately authenticate with a scoped bearer
// token instead of an LMS session. A refusal here means the QR/feedback link
// is invalid or expired; it must never sign a public visitor out or redirect
// them to the workspace login page.
const PUBLIC_TOKEN_API_PREFIXES = [
  '/engagement_api/feedback/public-event/',
  '/engagement_api/feedback/public-rsvp/',
  '/engagement_api/event-check-in/',
];

let listener: Listener | null = null;
let notified = false;
let originalFetch: typeof window.fetch | null = null;

/** The path of a fetch argument, or null when it is not same-origin. */
function samePathname(input: RequestInfo | URL): string | null {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.href
        : input instanceof Request
          ? input.url
          : '';
  if (!raw) return null;

  try {
    const url = new URL(raw, window.location.origin);
    return url.origin === window.location.origin ? url.pathname : null;
  } catch {
    return null;
  }
}

function isSessionApi(pathname: string): boolean {
  return !PUBLIC_TOKEN_API_PREFIXES.some((prefix) => pathname.startsWith(prefix))
    && SESSION_API_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

/** Report a 401 that means the session has ended. Safe to call repeatedly. */
export function reportSessionExpired(): void {
  if (notified) return;
  notified = true;
  listener?.();
}

/** Re-arm the notice. Called on a successful sign-in. */
export function resetSessionExpiryNotice(): void {
  notified = false;
}

/**
 * Wrap `window.fetch` so a 401 from a gated API reaches `onExpired`.
 *
 * Returns the uninstall function, so `AuthProvider` can hand it straight back
 * from its effect and StrictMode's double-mount does not stack two wrappers.
 */
export function installSessionExpiryHandler(onExpired: Listener): () => void {
  listener = onExpired;

  // Already wrapped (a re-render, or StrictMode mounting twice): swap the
  // listener and leave the single wrapper in place.
  if (originalFetch) {
    return () => {
      listener = null;
    };
  }

  const wrapped = window.fetch;
  originalFetch = wrapped;

  window.fetch = async function sessionAwareFetch(input, init) {
    const response = await wrapped(input, init);

    if (response.status === 401) {
      const pathname = samePathname(input);
      if (pathname && isSessionApi(pathname)) reportSessionExpired();
    }

    return response;
  };

  return () => {
    if (originalFetch) {
      window.fetch = originalFetch;
      originalFetch = null;
    }
    listener = null;
  };
}
