/** Saved keys (including deliberate blanks/false/null) override source defaults. */
export function mergeFields<T>(base: T, incoming: Partial<T>): T {
  const result = { ...base } as Record<string, unknown>;
  for (const [key, value] of Object.entries(incoming)) {
    const prior = result[key];
    result[key] = value && typeof value === 'object' && !Array.isArray(value)
      ? mergeFields(prior && typeof prior === 'object' && !Array.isArray(prior) ? prior : {}, value)
      : value;
  }
  return result as T;
}

/** A late response may fill untouched fields, never edits made while loading. */
export function preserveEdits<T>(baseline: T, current: T, incoming: T): T {
  if (JSON.stringify(current) === JSON.stringify(baseline)) return incoming;
  if (!current || typeof current !== 'object' || Array.isArray(current)) return current;
  const out = { ...incoming } as Record<string, unknown>;
  for (const [key, value] of Object.entries(current)) {
    const before = (baseline as Record<string, unknown> | null)?.[key];
    if (JSON.stringify(value) !== JSON.stringify(before)) {
      out[key] = preserveEdits(before, value, out[key]);
    }
  }
  return out as T;
}
