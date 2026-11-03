import type { Ksb } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import { selectionConfirmed } from '../useSuitability';
import { isModuleGroup, ordered, type ModulesState } from '../modules/configuration';
import type { PcpRadar, RadarModule } from './allocation';
import source from './marketing_executive_l4_ksb_full_mapping.json';

type Kind = Ksb['kind'];
interface ReferenceKsb { code: string; category: Kind; full_description: string; module_name: string }
interface ReferenceModule { name: string; ksb_count: number; ksb_codes: string[] }
interface Reference { ksbs: ReferenceKsb[]; modules: ReferenceModule[] }
export type MeRadarModule = RadarModule & { sessions: number | null };
type MeRadar = Omit<PcpRadar, 'modules'> & { modules: MeRadarModule[] };

// Bind the three immutable source groups to existing published IDs, never to
// editable configuration titles or positions. Source names are reference only.
const BINDINGS = [
  { id: 'marketing-impact', sourceIndex: 0, count: 14 },
  { id: 'social-media', sourceIndex: 1, count: 10 },
  { id: 'martech', sourceIndex: 2, count: 9 },
] as const;
const EXPECTED_CODES = [['K', 11], ['S', 14], ['B', 8]].flatMap(([prefix, count]) => Array.from({ length: Number(count) }, (_, i) => `${prefix}${i + 1}`));
const TYPES: Record<string, Kind> = { K: 'Knowledge', S: 'Skill', B: 'Behaviour' };

export function isMeRadar(state?: AssessmentState | null): boolean {
  return Boolean(state && selectionConfirmed(state) && state.selectedProgramme === 'ME_L4');
}

export function meRadarProfileProgramme(state?: AssessmentState | null): string {
  return state?.configuration?.programmes.find((programme) => programme.id === 'ME_L4')?.catalogueId || 'Marketing Executive Level 4';
}

export function validateMeCatalogue(raw: unknown = source) {
  const data = raw as Reference | null;
  if (!Array.isArray(data?.ksbs) || !Array.isArray(data?.modules) || data.modules.length !== 3) {
    throw new Error('ME KSB configuration: the 33-KSB catalogue and three source groups are required.');
  }
  const ownerByCode = new Map<string, string>();
  const sourceNameByCode = new Map<string, string>();
  for (const binding of BINDINGS) {
    const group = data.modules[binding.sourceIndex];
    if (!Array.isArray(group?.ksb_codes)) throw new Error(`ME KSB configuration: missing source assignments for ${binding.id} (${source.modules[binding.sourceIndex].ksb_codes.join(', ')}).`);
    for (const code of group.ksb_codes) {
      if (!EXPECTED_CODES.includes(code) || ownerByCode.has(code)) throw new Error(`ME KSB configuration: unknown or duplicate module assignment for ${code}.`);
      ownerByCode.set(code, binding.id); sourceNameByCode.set(code, group.name);
    }
  }
  const unassigned = EXPECTED_CODES.filter((code) => !ownerByCode.has(code));
  if (unassigned.length) throw new Error(`ME KSB configuration: missing module assignments for ${unassigned.join(', ')}.`);
  for (const binding of BINDINGS) {
    const group = data.modules[binding.sourceIndex];
    if (group.ksb_count !== binding.count || group.ksb_codes.length !== binding.count) {
      throw new Error(`ME KSB configuration: ${binding.id} must own ${binding.count} codes (${group.ksb_codes.join(', ')}).`);
    }
  }
  const byCode = new Map<string, ReferenceKsb>();
  for (const item of data.ksbs) {
    const code = item?.code;
    if (!EXPECTED_CODES.includes(code) || byCode.has(code)) throw new Error(`ME KSB configuration: unknown or duplicate code ${code ?? '(missing)'}.`);
    if (item.category !== TYPES[code[0]]) throw new Error(`ME KSB configuration: incorrect category for ${code}.`);
    if (typeof item.full_description !== 'string' || !item.full_description.trim()
      || /^(?:[KSB]\d+|knowledge\s*\d+|skill\s*\d+|behaviou?r\s*\d+|KSB Question|placeholder)$/i.test(item.full_description.trim())) {
      throw new Error(`ME KSB configuration: missing or invalid full description for ${code}.`);
    }
    if (!ownerByCode.has(code) || item.module_name !== sourceNameByCode.get(code)) throw new Error(`ME KSB configuration: inconsistent source module assignment for ${code}.`);
    byCode.set(code, item);
  }
  const missing = EXPECTED_CODES.filter((code) => !byCode.has(code) || !ownerByCode.has(code));
  if (missing.length) throw new Error(`ME KSB configuration: missing codes ${missing.join(', ')}.`);
  return { questions: EXPECTED_CODES.map((code) => byCode.get(code)!), ownerByCode };
}

export function allocateMeQuestions(profile: Ksb[], state: ModulesState, raw: unknown = source): MeRadarModule[] {
  const { questions, ownerByCode } = validateMeCatalogue(raw);
  if (state.selectedProgramme !== 'ME_L4') throw new Error('ME KSB configuration: modules do not match the confirmed Marketing Executive programme.');
  const programme = state.configuration.programmes.find((item) => item.id === 'ME_L4');
  const modules = ordered(programme?.modules ?? []).filter((item) => item.active);
  for (const binding of BINDINGS) {
    const matches = modules.filter((item) => item.id === binding.id && !isModuleGroup(item));
    if (matches.length !== 1) {
      const codes = questions.filter((item) => ownerByCode.get(item.code) === binding.id).map((item) => item.code);
      throw new Error(`ME KSB configuration: missing or duplicate published module ${binding.id} for ${codes.join(', ')}.`);
    }
  }
  const unexpected = modules.filter((item) => !BINDINGS.some((binding) => binding.id === item.id));
  if (unexpected.length || programme?.pathways.some((path) => path.active)) throw new Error(`ME KSB configuration: expected three fixed modules; unsupported modules/pathways ${unexpected.map((item) => item.id).join(', ')}.`);
  const byCode = new Map<string, Ksb>(); const ids = new Set<string>();
  for (const item of profile) {
    const code = item.codes[0];
    if (item.codes.length !== 1 || !EXPECTED_CODES.includes(code) || byCode.has(code) || !item.id || ids.has(item.id)) {
      throw new Error(`ME KSB configuration: ambiguous programme profile identifier for ${item.codes.join(', ') || item.id || '(missing code)'}.`);
    }
    byCode.set(code, item); ids.add(item.id);
  }
  const missing = EXPECTED_CODES.filter((code) => !byCode.has(code));
  if (missing.length) throw new Error(`ME KSB configuration: programme profile is missing ${missing.join(', ')}.`);
  return modules.map((module) => ({ id: module.id, title: module.title, sessions: module.sessions ?? null,
    questions: questions.filter((item) => ownerByCode.get(item.code) === module.id).map((item) => ({ ...byCode.get(item.code)!, title: item.full_description, kind: item.category })) }));
}

export function resolveMeRadar(profile: Ksb[] | null, state: ModulesState | null | undefined, error = ''): MeRadar {
  const empty = { modules: [], questions: [] };
  if (error) return { ...empty, status: 'error', message: error };
  if (profile === null || !state) return { ...empty, status: 'loading', message: 'Loading Marketing Executive modules and Skills Radar questions…' };
  try {
    const modules = allocateMeQuestions(profile, state);
    return { status: 'ready', message: '', modules, questions: modules.flatMap((module) => module.questions) };
  } catch (failure) {
    return { ...empty, status: 'error', message: failure instanceof Error ? failure.message : 'Invalid ME KSB configuration.' };
  }
}
