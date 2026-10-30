import { useCallback, useEffect, useRef, useState } from 'react';
import type { LearnerKind } from '@/api/learnerDetail';
import { subscribeLearnerReadInvalidation } from '@/api/learnerRead';

type Reader<T> = (kind: LearnerKind, id: string, signal?: AbortSignal, fresh?: boolean) => Promise<T>;
type Peek<T> = (kind: LearnerKind, id: string) => T | undefined;

export type LiveLearnerReadResult<T> = {
  data: T | null;
  error: string;
  loading: boolean;
  refresh: () => void;
};

/** Load learner data and keep the last successful response visible during explicit refreshes. */
export function useLiveLearnerRead<T>(
  kind: LearnerKind | null | undefined,
  id: string | null | undefined,
  enabled: boolean,
  read: Reader<T>,
  peek: Peek<T>,
  active = true,
): LiveLearnerReadResult<T> {
  const key = `${kind}:${id}`;
  const [revision, setRevision] = useState(0);
  const consumedRevision = useRef({ key, revision: 0 });
  const [state, setState] = useState<{ key: string; data: T | undefined; error: string } | null>(null);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!enabled || !active || !kind || !id) return;
    let disposed = false;
    let sequence = 0;
    let pending = false;
    let controller: AbortController | undefined;

    const load = (fresh = false, supersede = false) => {
      if (disposed || (pending && !supersede)) return;
      const requestSequence = ++sequence;
      controller?.abort();
      controller = new AbortController();
      pending = true;
      void read(kind, id, controller.signal, fresh).then(
        data => {
          if (!disposed && requestSequence === sequence) setState({ key, data, error: '' });
        },
        reason => {
          if (!disposed && requestSequence === sequence) setState(previous => ({
            key,
            data: previous?.key === key ? previous.data : peek(kind, id),
            error: reason instanceof Error ? reason.message : 'Could not refresh learner data. Please try again.',
          }));
        },
      ).finally(() => { if (requestSequence === sequence) pending = false; });
    };
    const unsubscribe = subscribeLearnerReadInvalidation(() => {
      // A pre-save request may finish last. Replace its caller and ignore it.
      load(true, true);
    });
    const fresh = consumedRevision.current.key === key && consumedRevision.current.revision !== revision;
    consumedRevision.current = { key, revision };
    load(fresh);

    return () => {
      disposed = true;
      controller?.abort();
      unsubscribe();
    };
  }, [key, kind, id, enabled, active, read, peek, revision]);

  const current = enabled && state?.key === key ? state : null;
  const data = enabled && kind && id
    ? current?.data !== undefined ? current.data : peek(kind, id)
    : undefined;
  return {
    data: data ?? null,
    error: current?.error || '',
    loading: enabled && !!kind && !!id && data === undefined && !current?.error,
    refresh,
  };
}
