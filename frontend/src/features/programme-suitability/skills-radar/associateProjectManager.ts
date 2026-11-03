import type { Ksb } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import { selectionConfirmed } from '../useSuitability';
import { isModuleGroup, ordered, type ModulesState } from '../modules/configuration';
import type { PcpRadar, RadarModule } from './allocation';
import source from './associate_project_manager_l4_ksb_full_mapping.json';

interface ReferenceKsb { code: string; type: Ksb['kind']; full_description: string; module_mapping_key: string; module_name: string }
interface ReferenceModule { mapping_key: string; name: string; expected_ksbs: number; ksb_codes: string[] }
interface Reference { programme_code_reference: string; ksb_total: number; ksbs: ReferenceKsb[]; modules: ReferenceModule[] }
export type ApmRadarModule = RadarModule & { sessions: number | null };
type ApmRadar = Omit<PcpRadar, 'modules'> & { modules: ApmRadarModule[] };

// Source mapping keys are references, not database IDs. Only the published
// APM module IDs below are used for configuration lookup; titles remain editable.
const BINDINGS = [
  { id: 'pmp', key: 'apm_l4_pmp', count: 42 },
  { id: 'ai-project-management', key: 'apm_l4_ai', count: 20 },
] as const;
const EXPECTED_CODES = [['K', 31], ['S', 26], ['B', 5]].flatMap(([prefix, count]) => Array.from({ length: Number(count) }, (_, i) => `${prefix}${i + 1}`));
const TYPES: Record<string, Ksb['kind']> = { K: 'Knowledge', S: 'Skill', B: 'Behaviour' };

export function isApmRadar(state?: AssessmentState | null): boolean {
  return Boolean(state && selectionConfirmed(state) && state.selectedProgramme === 'APM_L4');
}

/** Staff can review an assigned APM learner before Step 6 is completed. */
export function isApmStaffReview(readOnly: boolean, assignedProgramme: string, state?: AssessmentState | null): boolean {
  // A confirmed choice wins over a cached/stale board programme.
  if (!readOnly || selectionConfirmed(state ?? null)) return false;
  const normalise = (value: string) => value.toLowerCase().replace(/[—–-]/g, ' ').replace(/\s+/g, ' ').trim();
  const catalogueId = state?.configuration?.programmes.find((programme) => programme.id === 'APM_L4')?.catalogueId;
  return [apmRadarProfileProgramme(), catalogueId].some((value) => Boolean(value && normalise(value) === normalise(assignedProgramme)));
}

export function apmRadarProfileProgramme(state?: AssessmentState | null): string {
  return state?.configuration?.programmes.find((programme) => programme.id === 'APM_L4')?.catalogueId || 'Associate Project Manager Level 4';
}

export function validateApmCatalogue(raw: unknown = source) {
  const data = raw as Reference | null;
  if (!Array.isArray(data?.ksbs) || !Array.isArray(data?.modules) || data.modules.length !== 2
    || data.programme_code_reference !== 'APM_L4' || data.ksb_total !== 62) {
    throw new Error('APM KSB configuration: the 62-KSB catalogue and two fixed source groups are required.');
  }
  const ownerByCode = new Map<string, string>();
  const keyByCode = new Map<string, string>();
  const nameByCode = new Map<string, string>();
  for (const binding of BINDINGS) {
    const groups = data.modules.filter((group) => group.mapping_key === binding.key);
    const group = groups[0];
    if (groups.length !== 1 || !Array.isArray(group?.ksb_codes)) {
      throw new Error(`APM KSB configuration: missing or duplicate source group ${binding.key} (${source.modules.find((item) => item.mapping_key === binding.key)!.ksb_codes.join(', ')}).`);
    }
    for (const code of group.ksb_codes) {
      if (!EXPECTED_CODES.includes(code) || ownerByCode.has(code)) throw new Error(`APM KSB configuration: unknown or duplicate module assignment for ${code}.`);
      ownerByCode.set(code, binding.id); keyByCode.set(code, binding.key); nameByCode.set(code, group.name);
    }
  }
  const unassigned = EXPECTED_CODES.filter((code) => !ownerByCode.has(code));
  if (unassigned.length) throw new Error(`APM KSB configuration: missing module assignments for ${unassigned.join(', ')}.`);
  for (const binding of BINDINGS) {
    const group = data.modules.find((item) => item.mapping_key === binding.key)!;
    if (group.expected_ksbs !== binding.count || group.ksb_codes.length !== binding.count) {
      throw new Error(`APM KSB configuration: ${binding.id} must own ${binding.count} codes (${group.ksb_codes.join(', ')}).`);
    }
  }
  const byCode = new Map<string, ReferenceKsb>();
  for (const item of data.ksbs) {
    const code = item?.code;
    if (!EXPECTED_CODES.includes(code) || byCode.has(code)) throw new Error(`APM KSB configuration: unknown or duplicate code ${code ?? '(missing)'}.`);
    if (item.type !== TYPES[code[0]]) throw new Error(`APM KSB configuration: incorrect type for ${code}.`);
    if (typeof item.full_description !== 'string' || !item.full_description.trim()
      || /^(?:[KSB]\d+|knowledge\s*\d+|skill\s*\d+|behaviou?r\s*\d+|KSB Question|placeholder)$/i.test(item.full_description.trim())) {
      throw new Error(`APM KSB configuration: missing or invalid full description for ${code}.`);
    }
    if (item.module_mapping_key !== keyByCode.get(code) || item.module_name !== nameByCode.get(code)) {
      throw new Error(`APM KSB configuration: inconsistent source module assignment for ${code}.`);
    }
    byCode.set(code, item);
  }
  const missing = EXPECTED_CODES.filter((code) => !byCode.has(code));
  if (missing.length) throw new Error(`APM KSB configuration: missing codes ${missing.join(', ')}.`);
  return { questions: EXPECTED_CODES.map((code) => byCode.get(code)!), ownerByCode };
}

export function allocateApmQuestions(profile: Ksb[], state: ModulesState, raw: unknown = source): ApmRadarModule[] {
  const { questions, ownerByCode } = validateApmCatalogue(raw);
  if (state.selectedProgramme !== 'APM_L4') throw new Error('APM KSB configuration: modules do not match the confirmed Associate Project Manager programme.');
  const programme = state.configuration.programmes.find((item) => item.id === 'APM_L4');
  const modules = ordered(programme?.modules ?? []).filter((item) => item.active);
  for (const binding of BINDINGS) {
    const matches = modules.filter((item) => item.id === binding.id && !isModuleGroup(item));
    if (matches.length !== 1) {
      const codes = questions.filter((item) => ownerByCode.get(item.code) === binding.id).map((item) => item.code);
      throw new Error(`APM KSB configuration: missing or duplicate published module ${binding.id} for ${codes.join(', ')}.`);
    }
  }
  const unexpected = modules.filter((item) => !BINDINGS.some((binding) => binding.id === item.id));
  if (unexpected.length || programme?.pathways.some((path) => path.active)) {
    throw new Error(`APM KSB configuration: expected two fixed modules; unsupported modules/pathways ${unexpected.map((item) => item.id).join(', ')}.`);
  }
  const byCode = new Map<string, Ksb>(); const ids = new Set<string>();
  for (const item of profile) {
    const code = item.codes[0];
    if (item.codes.length !== 1 || !EXPECTED_CODES.includes(code) || byCode.has(code) || !item.id || ids.has(item.id)) {
      throw new Error(`APM KSB configuration: ambiguous programme profile identifier for ${item.codes.join(', ') || item.id || '(missing code)'}.`);
    }
    byCode.set(code, item); ids.add(item.id);
  }
  const missing = EXPECTED_CODES.filter((code) => !byCode.has(code));
  if (missing.length) throw new Error(`APM KSB configuration: programme profile is missing ${missing.join(', ')}.`);
  return modules.map((module) => ({ id: module.id, title: module.title, sessions: module.sessions ?? null,
    questions: questions.filter((item) => ownerByCode.get(item.code) === module.id).map((item) => ({ ...byCode.get(item.code)!, title: item.full_description, kind: item.type })) }));
}

export function resolveApmRadar(profile: Ksb[] | null, state: ModulesState | null | undefined, error = ''): ApmRadar {
  const empty = { modules: [], questions: [] };
  if (error) return { ...empty, status: 'error', message: error };
  if (profile === null || !state) return { ...empty, status: 'loading', message: 'Loading Associate Project Manager modules and Skills Radar questions…' };
  try {
    const modules = allocateApmQuestions(profile, state);
    return { status: 'ready', message: '', modules, questions: modules.flatMap((module) => module.questions) };
  } catch (failure) {
    return { ...empty, status: 'error', message: failure instanceof Error ? failure.message : 'Invalid APM KSB configuration.' };
  }
}
