import type { Ksb } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import { selectionConfirmed } from '../useSuitability';
import { isModuleGroup, ordered, type ModulesState } from '../modules/configuration';
import type { PcpRadar, RadarModule } from './allocation';
import source from './marketing_manager_l6_ksb_full_mapping.json';

type Kind = Ksb['kind'];
interface ReferenceKsb { code: string; category: Kind; full_description: string; module_name: string }
interface ReferenceModule { name: string; ksb_count: number; ksb_codes: string[] }
interface Reference { ksbs: ReferenceKsb[]; modules: ReferenceModule[] }
export type MmRadarModule = RadarModule & { sessions: number | null };
type MmRadar = Omit<PcpRadar, 'modules'> & { modules: MmRadarModule[] };

// Bind the four immutable source groups to existing published IDs, never to
// editable configuration titles or positions. Source names are reference only.
const BINDINGS = [
  { id: 'marketing-strategy', sourceIndex: 0, count: 16 },
  { id: 'customer-journey', sourceIndex: 1, count: 9 },
  { id: 'commercial-intelligence', sourceIndex: 2, count: 11 },
  { id: 'ai-marketing', sourceIndex: 3, count: 6 },
] as const;
const EXPECTED_CODES = [['K', 14], ['S', 19], ['B', 9]].flatMap(([prefix, count]) => Array.from({ length: Number(count) }, (_, i) => `${prefix}${i + 1}`));
const TYPES: Record<string, Kind> = { K: 'Knowledge', S: 'Skill', B: 'Behaviour' };

export function isMmRadar(state?: AssessmentState | null): boolean {
  return Boolean(state && selectionConfirmed(state) && state.selectedProgramme === 'MM_L6');
}

export function mmRadarProfileProgramme(state?: AssessmentState | null): string {
  return state?.configuration?.programmes.find((programme) => programme.id === 'MM_L6')?.catalogueId || 'Marketing Manager Level 6';
}

export function validateMmCatalogue(raw: unknown = source) {
  const data = raw as Reference | null;
  if (!Array.isArray(data?.ksbs) || !Array.isArray(data?.modules) || data.modules.length !== 4) {
    throw new Error('MM KSB configuration: the 42-KSB catalogue and four source groups are required.');
  }
  const ownerByCode = new Map<string, string>();
  const sourceNameByCode = new Map<string, string>();
  for (const binding of BINDINGS) {
    const group = data.modules[binding.sourceIndex];
    if (!Array.isArray(group?.ksb_codes)) throw new Error(`MM KSB configuration: missing source assignments for ${binding.id} (${source.modules[binding.sourceIndex].ksb_codes.join(', ')}).`);
    for (const code of group.ksb_codes) {
      if (!EXPECTED_CODES.includes(code) || ownerByCode.has(code)) throw new Error(`MM KSB configuration: unknown or duplicate module assignment for ${code}.`);
      ownerByCode.set(code, binding.id); sourceNameByCode.set(code, group.name);
    }
  }
  const unassigned = EXPECTED_CODES.filter((code) => !ownerByCode.has(code));
  if (unassigned.length) throw new Error(`MM KSB configuration: missing module assignments for ${unassigned.join(', ')}.`);
  for (const binding of BINDINGS) {
    const group = data.modules[binding.sourceIndex];
    if (group.ksb_count !== binding.count || group.ksb_codes.length !== binding.count) {
      throw new Error(`MM KSB configuration: ${binding.id} must own ${binding.count} codes (${group.ksb_codes.join(', ')}).`);
    }
  }
  const byCode = new Map<string, ReferenceKsb>();
  for (const item of data.ksbs) {
    const code = item?.code;
    if (!EXPECTED_CODES.includes(code) || byCode.has(code)) throw new Error(`MM KSB configuration: unknown or duplicate code ${code ?? '(missing)'}.`);
    if (item.category !== TYPES[code[0]]) throw new Error(`MM KSB configuration: incorrect category for ${code}.`);
    if (typeof item.full_description !== 'string' || !item.full_description.trim()
      || /^(?:[KSB]\d+|knowledge\s*\d+|skill\s*\d+|behaviou?r\s*\d+|KSB Question|placeholder)$/i.test(item.full_description.trim())) {
      throw new Error(`MM KSB configuration: missing or invalid full description for ${code}.`);
    }
    if (!ownerByCode.has(code) || item.module_name !== sourceNameByCode.get(code)) throw new Error(`MM KSB configuration: inconsistent source module assignment for ${code}.`);
    byCode.set(code, item);
  }
  const missing = EXPECTED_CODES.filter((code) => !byCode.has(code) || !ownerByCode.has(code));
  if (missing.length) throw new Error(`MM KSB configuration: missing codes ${missing.join(', ')}.`);
  return { questions: EXPECTED_CODES.map((code) => byCode.get(code)!), ownerByCode };
}

export function allocateMmQuestions(profile: Ksb[], state: ModulesState, raw: unknown = source): MmRadarModule[] {
  const { questions, ownerByCode } = validateMmCatalogue(raw);
  if (state.selectedProgramme !== 'MM_L6') throw new Error('MM KSB configuration: modules do not match the confirmed Marketing Manager programme.');
  const programme = state.configuration.programmes.find((item) => item.id === 'MM_L6');
  const modules = ordered(programme?.modules ?? []).filter((item) => item.active);
  for (const binding of BINDINGS) {
    const matches = modules.filter((item) => item.id === binding.id && !isModuleGroup(item));
    if (matches.length !== 1) {
      const codes = questions.filter((item) => ownerByCode.get(item.code) === binding.id).map((item) => item.code);
      throw new Error(`MM KSB configuration: missing or duplicate published module ${binding.id} for ${codes.join(', ')}.`);
    }
  }
  const unexpected = modules.filter((item) => !BINDINGS.some((binding) => binding.id === item.id));
  if (unexpected.length || programme?.pathways.some((path) => path.active)) throw new Error(`MM KSB configuration: expected four fixed modules; unsupported modules/pathways ${unexpected.map((item) => item.id).join(', ')}.`);
  const byCode = new Map<string, Ksb>(); const ids = new Set<string>();
  for (const item of profile) {
    const code = item.codes[0];
    if (item.codes.length !== 1 || !EXPECTED_CODES.includes(code) || byCode.has(code) || !item.id || ids.has(item.id)) {
      throw new Error(`MM KSB configuration: ambiguous programme profile identifier for ${item.codes.join(', ') || item.id || '(missing code)'}.`);
    }
    byCode.set(code, item); ids.add(item.id);
  }
  const missing = EXPECTED_CODES.filter((code) => !byCode.has(code));
  if (missing.length) throw new Error(`MM KSB configuration: programme profile is missing ${missing.join(', ')}.`);
  return modules.map((module) => ({ id: module.id, title: module.title, sessions: module.sessions ?? null,
    questions: questions.filter((item) => ownerByCode.get(item.code) === module.id).map((item) => ({ ...byCode.get(item.code)!, title: item.full_description, kind: item.category })) }));
}

export function resolveMmRadar(profile: Ksb[] | null, state: ModulesState | null | undefined, error = ''): MmRadar {
  const empty = { modules: [], questions: [] };
  if (error) return { ...empty, status: 'error', message: error };
  if (profile === null || !state) return { ...empty, status: 'loading', message: 'Loading Marketing Manager modules and Skills Radar questions…' };
  try {
    const modules = allocateMmQuestions(profile, state);
    return { status: 'ready', message: '', modules, questions: modules.flatMap((module) => module.questions) };
  } catch (failure) {
    return { ...empty, status: 'error', message: failure instanceof Error ? failure.message : 'Invalid MM KSB configuration.' };
  }
}
