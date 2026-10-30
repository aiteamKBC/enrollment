// ============================================================================
// Saved signature API client
// The signed-in person's own signature, offered back wherever they sign (see
// backend/login/saved_signature.py). Always the caller's own: the server picks
// the account from the session, never from the request.
// ============================================================================

const URL = '/login_api/me/signature/';

export interface SavedSignature {
  /** PNG data URL, or '' when none has been saved. */
  signature: string;
  savedAt: string;
}

/**
 * One read per account per page load; every signature box shares it. Keyed by
 * the account so a sign-out and a different sign-in on the same tab can never
 * be shown the previous person's signature.
 */
let cached: { account: string; value: Promise<SavedSignature> } | null = null;

async function send(init?: Parameters<typeof fetch>[1]): Promise<SavedSignature> {
  const res = await fetch(URL, {
    credentials: 'include',
    ...init,
    headers: { 'X-Requested-With': 'XMLHttpRequest', ...(init?.body ? { 'Content-Type': 'application/json' } : {}) },
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data as SavedSignature;
}

export function fetchSavedSignature(account: string): Promise<SavedSignature> {
  if (cached?.account !== account) {
    const value = send().catch((e) => {
      // A failed read must not be remembered: the next box tries again.
      if (cached?.value === value) cached = null;
      throw e;
    });
    cached = { account, value };
  }
  return cached.value;
}

export async function saveSignature(account: string, signature: string): Promise<SavedSignature> {
  const saved = await send({ method: 'PUT', body: JSON.stringify({ signature }) });
  cached = { account, value: Promise.resolve(saved) };
  return saved;
}

/** Drop the remembered signature (tests; a sign-out). */
export function forgetSavedSignature(): void {
  cached = null;
}
