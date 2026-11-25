import { useEffect, useState } from 'react';
import { CatalogRequestError, catalogRequest } from './catalogApi';

export function useCatalogueResource<Result>(path: string | null, revision: number) {
  const [state, setState] = useState<{
    path: string; revision: number; data: Result | null; error: string | null; errorCode?: string;
  }>();
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    const signal = controller.signal;
    catalogRequest<Result>(path, { signal }).then(data => {
      if (!signal.aborted) setState({ path, revision, data, error: null });
    }).catch((failure: unknown) => {
      if (!signal.aborted) setState({
        path, revision, data: null,
        error: failure instanceof Error ? failure.message : 'Could not load this catalogue record.',
        errorCode: failure instanceof CatalogRequestError ? failure.code : undefined,
      });
    });
    return () => controller.abort();
  }, [path, revision]);
  const matching = state?.path === path ? state : undefined;
  return {
    data: matching?.data ?? null, error: matching?.error ?? null, errorCode: matching?.errorCode,
    loading: Boolean(path && matching?.revision !== revision),
  };
}
