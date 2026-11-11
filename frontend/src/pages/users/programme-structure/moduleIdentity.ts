/** Cohort-module identities follow the UUID convention already used on copying.
 * These are configuration instances, not Curriculum module catalogue records.
 */
export function newModuleId(snapshot: unknown, generate: () => string = () => crypto.randomUUID()): string {
  const existing = new Set<string>();
  const visit = (value: unknown) => {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === 'object') {
      const record = value as Record<string, unknown>;
      if (typeof record.id === 'string') existing.add(record.id);
      Object.values(record).forEach(visit);
    }
  };
  visit(snapshot);
  for (let attempt = 0; attempt < 10; attempt++) {
    const id = generate();
    if (id && !existing.has(id)) return id;
  }
  throw new Error('Could not generate a unique Module identity. Please retry.');
}
