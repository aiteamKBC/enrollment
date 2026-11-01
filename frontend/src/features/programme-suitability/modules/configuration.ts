import type { ProgrammeId } from '../types';

export interface ProgrammeModule {
  id: string; title: string; sessions: number | null; credits?: number;
  description: string; order: number; active: boolean; selectable: boolean; isMandatory?: boolean; items?: ProgrammeModule[];
}
export interface ModulePathway {
  id: string; title: string; subtitle: string; description: string; recognition: string;
  targetCredits: number | null; order: number; active: boolean; allowModuleSelection: boolean;
  requiredOptionalModulesCount?: number | null;
  modules: ProgrammeModule[];
}
export interface ProgrammeModules {
  id: ProgrammeId; title: string; modules: ProgrammeModule[]; pathways: ModulePathway[];
}
export interface ModulesConfiguration { schemaVersion: 1 | 2; programmes: ProgrammeModules[] }
export interface ProgrammeModuleSelection {
  programme: 'PCP_L6'; pathway: string; modulesByPathway: Record<string, string[]>;
  configurationVersion?: number | null;
}
export interface ModulesState { configuration: ModulesConfiguration; configurationVersion: number | null; selectedProgramme: ProgrammeId;
  selectionComplete?: boolean; savedSelection?: ProgrammeModuleSelection | null }

export const ordered = <T extends { order: number }>(items: T[]): T[] => [...items].sort((a, b) => a.order - b.order);
export function selectableModules(modules: ProgrammeModule[]): ProgrammeModule[] {
  return modules.filter((item) => item.active).flatMap((item) => [...(item.selectable ? [item] : []), ...selectableModules(item.items ?? [])]);
}
export function selectedModuleIds(selection: ProgrammeModuleSelection | undefined, pathway: ModulePathway): string[] {
  if (selection?.programme !== 'PCP_L6' || selection.pathway !== pathway.id || !pathway.active) return [];
  const modules = selection.modulesByPathway?.[pathway.id] ?? [];
  if (!Array.isArray(modules)) return [];
  const allowed = new Set(selectableModules(pathway.modules).filter((m) => pathway.allowModuleSelection || m.isMandatory).map((m) => m.id));
  return [...new Set([...modules.filter((id) => allowed.has(id)), ...mandatoryIds(pathway)])];
}
export const mandatoryIds = (path: ModulePathway): string[] => selectableModules(path.modules).filter((m) => m.isMandatory).map((m) => m.id);
export function selectionFingerprint(choice: ProgrammeModuleSelection | undefined): string {
  if (!choice) return '';
  return JSON.stringify([choice.programme, choice.pathway, choice.configurationVersion ?? null,
    Object.entries(choice.modulesByPathway ?? {}).sort(([a], [b]) => a.localeCompare(b)).map(([id, values]) => [id, [...values].sort()])]);
}
export function pathwaySelectionError(path: ModulePathway, ids: string[]): string {
  const required = path.requiredOptionalModulesCount;
  const mandatory = mandatoryIds(path);
  if (!mandatory.every((id) => ids.includes(id))) return 'All mandatory modules must remain selected.';
  if (required == null) return 'This pathway needs published optional selection requirements before it can be completed.';
  if (ids.filter((id) => !mandatory.includes(id)).length !== required) return `Select exactly ${required} optional modules.`;
  const total = selectableModules(path.modules).filter((m) => ids.includes(m.id)).reduce((sum, m) => sum + (m.credits ?? 0), 0);
  return Math.abs(total - (path.targetCredits ?? 0)) > 1e-8 ? `Selected modules must achieve exactly ${path.targetCredits} credits.` : '';
}
export function moduleSelectionComplete(config: ModulesConfiguration, selection: ProgrammeModuleSelection | undefined, version: number | null): boolean {
  if (!selection || selection.programme !== 'PCP_L6' || (selection.configurationVersion !== version && !(version === 0 && selection.configurationVersion === undefined))) return false;
  const path = config.programmes.find((p) => p.id === 'PCP_L6')?.pathways.find((p) => p.id === selection.pathway && p.active);
  if (!path) return false;
  if (config.schemaVersion === 2 && path.requiredOptionalModulesCount == null) return false;
  if (!selection.modulesByPathway || typeof selection.modulesByPathway !== 'object' || Array.isArray(selection.modulesByPathway)) return false;
  for (const [pathId, values] of Object.entries(selection.modulesByPathway)) {
    const other = config.programmes.find((p) => p.id === 'PCP_L6')?.pathways.find((p) => p.id === pathId);
    if (!other || !Array.isArray(values) || new Set(values).size !== values.length) return false;
    const allowed = new Set(selectableModules(other.modules).filter((m) => other.allowModuleSelection || m.isMandatory).map((m) => m.id));
    if (values.some((id) => typeof id !== 'string' || !allowed.has(id)) || (values.length > 0 && !other.active)) return false;
  }
  const ids = selection.modulesByPathway[path.id] ?? [];
  const allowed = new Set(selectableModules(path.modules).map((m) => m.id));
  return new Set(ids).size === ids.length && ids.every((id) => allowed.has(id)) && (path.allowModuleSelection || ids.every((id) => mandatoryIds(path).includes(id))) && !pathwaySelectionError(path, ids);
}
export function pathwayRuleErrors(path: ModulePathway): string[] {
  const required = path.requiredOptionalModulesCount;
  if (required == null || !path.active) return [];
  if (!Number.isInteger(required) || required < 0) return ['Required optional module count must be a non-negative integer.'];
  const modules = selectableModules(path.modules);
  const mandatory = modules.filter((m) => m.isMandatory);
  const optional = modules.filter((m) => !m.isMandatory);
  if ((required > 0 && !path.allowModuleSelection) || required > optional.length) return ['Required optional module count exceeds available optional modules.'];
  if (path.targetCredits === null) return ['Set target credits when optional module requirements are configured.'];
  const remaining = path.targetCredits - mandatory.reduce((sum, m) => sum + (m.credits ?? 0), 0);
  if (remaining < -1e-8) return ['Mandatory credits exceed target credits.'];
  const sums: Set<number>[] = Array.from({ length: required + 1 }, () => new Set<number>());
  sums[0].add(0);
  for (const module of optional) {
    for (let size = required; size > 0; size--) for (const value of sums[size - 1]) {
      const total = Number((value + (module.credits ?? 0)).toFixed(8));
      if (total <= remaining + 1e-8) sums[size].add(total);
    }
    if (sums.reduce((n, set) => n + set.size, 0) > 100000) return ['Simplify the optional credit combinations before publishing.'];
  }
  return [...sums[required]].some((value) => Math.abs(value - remaining) < 1e-8) ? [] : ['The optional module count cannot produce the target credits.'];
}
export function configurationErrors(config: ModulesConfiguration): string[] {
  const errors: string[] = [];
  const modules = (items: ProgrammeModule[], seen = new Set<string>()) => items.forEach((item) => {
    if (!item.title.trim()) errors.push('Give every module a name.');
    if (seen.has(item.id)) errors.push('Module identifiers must be unique within a pathway.');
    seen.add(item.id);
    if (item.sessions !== null && (!Number.isInteger(item.sessions) || item.sessions < 0)) errors.push('Sessions must be non-negative integers.');
    if (item.credits !== undefined && (!Number.isFinite(item.credits) || item.credits < 0)) errors.push('Credits must be non-negative numbers.');
    if (!Number.isInteger(item.order) || item.order < 0) errors.push('Order must be a non-negative integer.');
    if (item.isMandatory && (!item.selectable || item.items?.length)) errors.push('Only selectable actual modules can be mandatory.');
    modules(item.items ?? [], seen);
  });
  config.programmes.forEach((programme) => {
    modules(programme.modules);
    const seen = new Set<string>();
    programme.pathways.forEach((pathway) => {
      if (!pathway.title.trim()) errors.push('Give every pathway a name.');
      if (seen.has(pathway.id)) errors.push('Pathway identifiers must be unique.');
      seen.add(pathway.id);
      if (pathway.targetCredits !== null && (!Number.isFinite(pathway.targetCredits) || pathway.targetCredits < 0)) errors.push('Target credits must be non-negative numbers.');
      if (!Number.isInteger(pathway.order) || pathway.order < 0) errors.push('Pathway order must be a non-negative integer.');
      if (pathway.active && pathway.requiredOptionalModulesCount == null && (config.schemaVersion === 2 || mandatoryIds(pathway).length > 0)) errors.push('Set the required optional module count for every active PCP pathway.');
      modules(pathway.modules);
      errors.push(...pathwayRuleErrors(pathway));
      const groups = (items: ProgrammeModule[]) => items.forEach((item) => {
        if (pathway.requiredOptionalModulesCount != null && item.items?.length && (item.selectable || (item.credits ?? 0) !== 0)) errors.push('Parent group headings must not be selectable or credited modules.');
        groups(item.items ?? []);
      });
      groups(pathway.modules);
    });
  });
  return [...new Set(errors)];
}
