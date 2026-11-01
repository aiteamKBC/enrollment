import { useEffect, useState } from 'react';
import type { LearnerKind } from '@/api/extendedIlr';
import { getModules } from './api';
import type { ModulesState } from './configuration';

export function useModulesConfiguration(kind: LearnerKind, id: string, confirmed: boolean, publicationVersion?: number | null) {
  const [snapshot, setSnapshot] = useState<{ key: string; data: ModulesState } | null>(null);
  const [error, setError] = useState('');
  const [attempt, retry] = useState(0);
  const key = JSON.stringify([kind, id, publicationVersion]);
  useEffect(() => {
    if (!confirmed) return;
    let active = true;
    let requestNumber = 0;
    setError('');
    const refresh = () => {
      if (document.visibilityState === 'hidden') return;
      const request = ++requestNumber;
      getModules(kind, id).then((data) => { if (active && request === requestNumber) { setSnapshot({ key, data }); setError(''); } })
        .catch((e: Error) => { if (active && request === requestNumber) setError(e.message); });
    };
    refresh();
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => { active = false; window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); };
  }, [kind, id, key, confirmed, attempt]);
  return { data: snapshot?.key === key ? snapshot.data : null, error, retry: () => retry((n) => n + 1) };
}

export type ModulesController = ReturnType<typeof useModulesConfiguration>;
