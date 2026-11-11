import { useCallback, useEffect, useState } from 'react';
import type { AuthUser } from '@/api/auth';
import { pendingSignatureResource as resource, type PendingSignatures } from './api';
import { PENDING_SIGNATURES_CHANGED, PENDING_SIGNATURES_STORAGE_KEY } from './events';

export const SIGNATURE_POLL_MS = 30000;
const EMPTY: PendingSignatures = { count: 0, results: [] };

/** One confirmed backend snapshot feeds both header and sidebar. Opening the
 * dropdown does not mark an obligation read or change its count. */
export function usePendingSignatures(account: AuthUser | null) {
  const key = account?.role === 'learner' && account.subjectId != null ? `${account.id}:${account.subjectId}` : '';
  const [state, setState] = useState<{ key: string; data: PendingSignatures; loading: boolean; error: string }>(() => ({ key, data: resource.peek(key) || EMPTY, loading: !!key, error: '' }));
  const [retry, setRetry] = useState(0);
  const refresh = useCallback(() => setRetry((n) => n + 1), []);
  useEffect(() => {
    if (!key) { resource.invalidate(); return; }
    let active = true, sequence = 0;
    const load = () => {
      if (document.visibilityState === 'hidden') return;
      const request = ++sequence;
      void resource.read(key, { revalidate: true }).then((data) => {
        if (active && request === sequence) setState({ key, data, loading: false, error: '' });
      }).catch((error: Error & { status?: number }) => {
        if (!active || request !== sequence) return;
        if (error.status === 401 || error.status === 403) resource.invalidate(key);
        setState((old) => ({ key, data: old.key === key && error.status !== 401 && error.status !== 403 ? old.data : EMPTY, loading: false, error: error.message || 'Could not refresh signature requests.' }));
      });
    };
    const changed = () => { resource.invalidate(key); load(); };
    const storage = (e: StorageEvent) => { if (e.key === PENDING_SIGNATURES_STORAGE_KEY) changed(); };
    load();
    const timer = window.setInterval(load, SIGNATURE_POLL_MS);
    window.addEventListener('focus', load); document.addEventListener('visibilitychange', load);
    window.addEventListener(PENDING_SIGNATURES_CHANGED, changed); window.addEventListener('storage', storage);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', load); document.removeEventListener('visibilitychange', load); window.removeEventListener(PENDING_SIGNATURES_CHANGED, changed); window.removeEventListener('storage', storage); };
  }, [key, retry]);
  const current = state.key === key && key ? state : { data: EMPTY, loading: !!key, error: '' };
  return { data: current.data, loading: current.loading, error: current.error, refresh };
}
