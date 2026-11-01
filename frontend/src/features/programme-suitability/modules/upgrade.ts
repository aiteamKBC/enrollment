import type { ModulesConfiguration, ProgrammeModule } from './configuration';

/** Explicit draft upgrade only; never mutate a learner's pinned publication. */
export function applyPcpDefaults(value: ModulesConfiguration, seed: ModulesConfiguration): ModulesConfiguration {
  const next = structuredClone(value);
  const defaultPaths = seed.programmes.find((p) => p.id === 'PCP_L6')!.pathways;
  const flags = (items: ProgrammeModule[], defaults: ProgrammeModule[]): ProgrammeModule[] => items.map((item) => {
    const standard = defaults.find((m) => m.id === item.id);
    return { ...item, isMandatory: standard?.isMandatory ?? item.isMandatory ?? false,
      ...(item.items ? { items: flags(item.items, standard?.items ?? []) } : {}) };
  });
  next.schemaVersion = 2;
  next.programmes = next.programmes.map((programme) => programme.id !== 'PCP_L6' ? programme : { ...programme,
    pathways: programme.pathways.map((path) => {
      const standard = defaultPaths.find((p) => p.id === path.id);
      if (!standard) return path;
      return { ...path, description: path.description || standard.description,
        targetCredits: path.targetCredits ?? standard.targetCredits,
        requiredOptionalModulesCount: path.requiredOptionalModulesCount ?? standard.requiredOptionalModulesCount,
        modules: flags(path.modules, standard.modules) };
    }) });
  return next;
}
