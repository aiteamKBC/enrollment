import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

type QueryPrimitive = string | number;
type QueryDefaults = Record<string, QueryPrimitive>;

export interface QueryUpdateOptions {
  resetPage?: boolean;
  replace?: boolean;
}

function readValue(defaultValue: QueryPrimitive, raw: string | null): QueryPrimitive {
  if (raw == null || raw === '') return defaultValue;
  if (typeof defaultValue === 'number') {
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : defaultValue;
  }
  return raw;
}

/**
 * Keeps durable list controls in the URL. Defaults are omitted so links stay
 * compact; Back/Forward and refresh rehydrate state directly from location.
 */
export function useListQueryState<T extends QueryDefaults>(defaults: T) {
  const [params, setParams] = useSearchParams();
  const defaultsKey = JSON.stringify(defaults);

  const state = useMemo(() => {
    const result: QueryDefaults = {};
    Object.entries(defaults).forEach(([key, defaultValue]) => {
      result[key] = readValue(defaultValue, params.get(key));
    });
    return result as T;
    // defaultsKey deliberately captures a caller-created object by value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultsKey, params]);

  const setValues = useCallback((patch: Partial<T>, options: QueryUpdateOptions = {}) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      const values: Partial<T> = options.resetPage && 'page' in defaults
        ? { ...patch, page: defaults.page } as Partial<T>
        : patch;
      Object.entries(values).forEach(([key, value]) => {
        const defaultValue = defaults[key];
        if (value == null || value === '' || value === defaultValue) next.delete(key);
        else next.set(key, String(value));
      });
      return next;
    }, { replace: options.replace ?? true });
  }, [defaults, setParams]);

  const reset = useCallback((keys?: (keyof T)[]) => {
    const selected = keys || Object.keys(defaults) as (keyof T)[];
    const patch = Object.fromEntries(selected.map(key => [key, defaults[String(key)]])) as Partial<T>;
    setValues(patch, { replace: true });
  }, [defaults, setValues]);

  return { state, setValues, reset, searchParams: params };
}
