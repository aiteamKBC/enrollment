/** Attach the initiating SPA page to same-origin LMS writes, without bodies,
 * query strings, credentials or changes to request/response handling. */
const API_PATH = /^\/(?:curriculum_api|coach_api|learner_api|enrolment_api|quiz_api|engagement_api|audit_api|manual_audit_api|hours_test_api|progress_reviews_api|api)\//;

export function installAuditRequestContext(): () => void {
  const original = globalThis.fetch;
  if (typeof original !== 'function' || typeof location === 'undefined') return () => {};
  const wrapped: typeof fetch = (input, init) => {
    const request = input instanceof Request ? input : null;
    const method = (init?.method || request?.method || 'GET').toUpperCase();
    if (['GET', 'HEAD', 'OPTIONS'].includes(method)) return original(input, init);
    const url = new URL(request?.url || String(input), location.href);
    if (url.origin !== location.origin || !API_PATH.test(url.pathname)) return original(input, init);
    const headers = new Headers(init?.headers ?? request?.headers);
    headers.set('X-Audit-Page', location.pathname);
    return original(input, { ...init, headers });
  };
  globalThis.fetch = wrapped;
  return () => { if (globalThis.fetch === wrapped) globalThis.fetch = original; };
}
