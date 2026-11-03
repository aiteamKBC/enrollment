import { useEffect, useMemo, useState } from 'react';
import type { LearnerKind } from '@/api/extendedIlr';
import type { ModulesState } from '../../modules/configuration';
import { getLearnerCatalogue } from './api';
import { resolveCatalogueRadar } from './runtime';
import type { LearnerCatalogue, ManagedRadar } from './types';

export function useCatalogue(enabled: boolean, kind: LearnerKind, id: string, programme: string, modules: ModulesState | null | undefined, modulesError: string, waiting = false): ManagedRadar {
  const [result, setResult] = useState<{ key: string; data: LearnerCatalogue | null; error: string }>({ key: '', data: null, error: '' });
  const key = `${kind}:${id}:${programme}`;
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    getLearnerCatalogue(kind, id).then((data) => { if (!cancelled) setResult({ key, data, error: '' }); })
      .catch((e: Error) => { if (!cancelled) setResult({ key, data: null, error: e.message }); });
    return () => { cancelled = true; };
  }, [enabled, key, kind, id]);
  const loading = waiting || (enabled && result.key !== key);
  const data = result.key === key ? result.data : null;
  const error = result.key === key ? result.error : '';
  const grouping = useMemo(() => !enabled || (data && !data.catalogue) ? null : resolveCatalogueRadar(data?.catalogue ?? null, modules, error || modulesError), [enabled, data, modules, error, modulesError]);
  return useMemo(() => ({ enabled: enabled || waiting, loading, data, error, grouping }), [enabled, waiting, loading, data, error, grouping]);
}
