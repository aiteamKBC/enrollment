import { invalidateCachedResource } from '@/api/cachedRequest';

export const PENDING_SIGNATURES_CHANGED = 'learner-pending-signatures-changed';
export const PENDING_SIGNATURES_STORAGE_KEY = 'learner-signature-revalidation';

/** Revalidate only after a successful document write. No counts, document IDs,
 * personal data or signatures are persisted or broadcast between tabs. */
export function pendingSignaturesChanged(): void {
  invalidateCachedResource('learner-pending-signatures');
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(PENDING_SIGNATURES_CHANGED));
  try { window.localStorage.setItem(PENDING_SIGNATURES_STORAGE_KEY, `${Date.now()}:${Math.random()}`); } catch { /* Focus/polling still work with storage disabled. */ }
}
