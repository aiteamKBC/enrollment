const coachSessionCache = new Map<string, unknown>();

function normalizePart(value: string | number | null | undefined) {
  return String(value ?? '').trim().toLowerCase();
}

export function coachSessionKey(resource: string, ...parts: Array<string | number | null | undefined>) {
  return [resource, ...parts].map(normalizePart).join('::');
}

export function readCoachSessionCache<T>(key: string): T | undefined {
  return coachSessionCache.get(key) as T | undefined;
}

export function writeCoachSessionCache<T>(key: string, value: T) {
  coachSessionCache.set(key, value);
}

export function clearCoachSessionCache() {
  coachSessionCache.clear();
}
