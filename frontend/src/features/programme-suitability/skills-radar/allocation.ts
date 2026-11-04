import type { Ksb } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import { selectionConfirmed } from '../useSuitability';
import { isModuleGroup, moduleSelectionComplete, ordered, selectableModules, type ModulesState, type ProgrammeModule } from '../modules/configuration';
import source from './pcp_l6_ksb_full_descriptions_and_mapping.json';

type Pathway = 'operational' | 'strategic' | 'chartered';
type Kind = 'Knowledge' | 'Skill' | 'Behaviour';
interface ReferenceKsb { code: string; type: Kind; full_description: string; original_owner_group_by_pathway: Record<Pathway, string> }
export interface RadarModule { id: string; title: string; questions: Ksb[]; sessions?: number | null }
export interface PcpRadar {
  status: 'ready' | 'loading' | 'waiting' | 'error'; message: string; modules: RadarModule[]; questions: Ksb[];
}

// Workbook owner keys are not database IDs. These are existing published module IDs.
const OWNERS: Record<Pathway, Record<string, string>> = {
  operational: { pmp: 'PMP', 'ai-project-control': 'AI', 'risk-apm': 'RISK', 'planning-control': 'PPC', 'earned-value': 'FINANCE', 'pmi-sp': 'SCHEDULE' },
  strategic: { pmp: 'PMP', 'ai-project-control': 'AI', 'risk-apm': 'RISK', 'pmi-pmo': 'PPC', portfolio: 'FINANCE', 'successful-programmes': 'SCHEDULE' },
  chartered: { 'pmo-planning-control': 'CH_PLAN', 'pmo-stakeholders': 'CH_STAKE', 'pmo-risk-quality': 'CH_RISK', 'pmo-office': 'CH_PMO',
    'ai-project-management': 'AI', portfolio: 'FINANCE', 'earned-value': 'FINANCE' },
};
const OPTIONAL_OWNER_ORDER = ['PPC', 'FINANCE', 'SCHEDULE'];
const EXPECTED_CODES = [['K', 31], ['S', 29], ['B', 11]].flatMap(([prefix, count]) => Array.from({ length: Number(count) }, (_, i) => `${prefix}${i + 1}`));
const TYPES: Record<string, Kind> = { K: 'Knowledge', S: 'Skill', B: 'Behaviour' };

export function isPcpRadar(state?: AssessmentState | null): boolean {
  return Boolean(state && selectionConfirmed(state) && state.selectedProgramme === 'PCP_L6');
}

/** Match the existing catalogue resolver, including when the board is still cached. */
export function radarProfileProgramme(boardProgramme: string, state?: AssessmentState | null): string {
  return isPcpRadar(state)
    ? state?.configuration?.programmes.find((p) => p.id === 'PCP_L6')?.catalogueId || 'Project Controls Professional Level 6'
    : boardProgramme;
}

export function validatePcpCatalogue(raw: unknown = source): ReferenceKsb[] {
  const items = (raw as { ksbs?: unknown } | null)?.ksbs;
  if (!Array.isArray(items)) throw new Error('PCP KSB configuration: the full-description catalogue is missing.');
  const byCode = new Map<string, ReferenceKsb>();
  for (const value of items) {
    const item = value as ReferenceKsb & { wording?: string };
    const code = item?.code;
    if (!EXPECTED_CODES.includes(code) || byCode.has(code)) throw new Error(`PCP KSB configuration: unknown or duplicate code ${code ?? '(missing)'}.`);
    if (item.type !== TYPES[code[0]]) throw new Error(`PCP KSB configuration: incorrect type for ${code}.`);
    const text = item.full_description;
    if (typeof text !== 'string' || !text.trim() || /^(?:[KSB]\d+|knowledge\s*\d+|skill\s*\d+|behaviou?r\s*\d+|KSB Question|placeholder)$/i.test(text.trim())
      || text !== item.wording) throw new Error(`PCP KSB configuration: missing or invalid full description for ${code}.`);
    for (const path of ['operational', 'strategic', 'chartered'] as const) {
      if (!Object.values(OWNERS[path]).includes(item.original_owner_group_by_pathway?.[path])) throw new Error(`PCP KSB configuration: invalid ${path} owner for ${code}.`);
    }
    byCode.set(code, item);
  }
  const missing = EXPECTED_CODES.filter((code) => !byCode.has(code));
  if (missing.length) throw new Error(`PCP KSB configuration: missing full descriptions for ${missing.join(', ')}.`);
  return EXPECTED_CODES.map((code) => byCode.get(code)!);
}

/** Reuse persisted profile IDs; never generate IDs from positions or module membership. */
export function bindPcpQuestions(profile: Ksb[], catalogue = validatePcpCatalogue()): Ksb[] {
  const expected = catalogue.map((r) => r.code);
  const byCode = new Map<string, Ksb>();
  const ids = new Set<string>();
  for (const item of profile) {
    const code = item.codes[0];
    if (item.codes.length !== 1 || !expected.includes(code) || byCode.has(code) || !item.id || ids.has(item.id)) {
      throw new Error(`PCP KSB configuration: ambiguous profile identifier for ${item.codes.join(', ') || item.id || '(missing code)'}.`);
    }
    byCode.set(code, item); ids.add(item.id);
  }
  const missing = expected.filter((code) => !byCode.has(code));
  if (missing.length) throw new Error(`PCP KSB configuration: programme profile is missing ${missing.join(', ')}.`);
  return catalogue.map((reference) => ({ ...byCode.get(reference.code)!, title: reference.full_description, kind: reference.type }));
}

function sortedTree(items: ProgrammeModule[]): ProgrammeModule[] {
  return ordered(items).map((item) => item.items ? { ...item, items: sortedTree(item.items) } : item);
}

export function allocatePcpQuestions(profile: Ksb[], state: ModulesState, rawCatalogue: unknown = source, publishedSnapshot = false): RadarModule[] {
  const reference = publishedSnapshot ? validatePublishedPcp(rawCatalogue) : validatePcpCatalogue(rawCatalogue);
  const questions = bindPcpQuestions(profile, reference);
  const selection = state.savedSelection ?? undefined;
  if (state.selectedProgramme !== 'PCP_L6' || !state.selectionComplete || !moduleSelectionComplete(state.configuration, selection, state.configurationVersion)) {
    throw new Error('Complete and save your PCP module selection in Programme Suitability & Modules before answering Skills Radar.');
  }
  const path = state.configuration.programmes.find((p) => p.id === 'PCP_L6')!.pathways.find((p) => p.id === selection!.pathway)!;
  if (!(path.id in OWNERS)) throw new Error(`PCP KSB configuration: no source ownership mapping for pathway ${path.id}.`);
  const ownerMap = OWNERS[path.id as Pathway];
  const selectedIds = new Set(selection!.modulesByPathway[path.id]);
  const selected = selectableModules(sortedTree(path.modules)).filter((m) => selectedIds.has(m.id));
  const byOwner = new Map<string, RadarModule>();
  const result = selected.map((module) => {
    const owner = ownerMap[module.id];
    if (!owner || isModuleGroup(module) || byOwner.has(owner)) throw new Error(`PCP KSB configuration: missing or ambiguous owner mapping for module ${module.id}.`);
    const group: RadarModule = { id: module.id, title: module.title, questions: [], sessions: module.sessions };
    byOwner.set(owner, group); return group;
  });
  const recipients = selected.filter((m) => !m.isMandatory).sort((a, b) => OPTIONAL_OWNER_ORDER.indexOf(ownerMap[a.id]) - OPTIONAL_OWNER_ORDER.indexOf(ownerMap[b.id]));
  const redistributed: Ksb[] = [];
  for (const [index, item] of reference.entries()) {
    const owner = item.original_owner_group_by_pathway[path.id as Pathway];
    const target = byOwner.get(owner);
    if (target) target.questions.push(questions[index]);
    else if (path.id !== 'chartered' && OPTIONAL_OWNER_ORDER.includes(owner) && recipients.length > 0) redistributed.push(questions[index]);
    else throw new Error(`PCP KSB configuration: ${item.code} has no selected module for owner ${owner}.`);
  }
  redistributed.forEach((question, index) => {
    result.find((module) => module.id === recipients[index % recipients.length].id)!.questions.push(question);
  });
  const codeOrder = new Map(reference.map((r, index) => [r.code, index]));
  result.forEach((group) => group.questions.sort((a, b) => codeOrder.get(a.codes[0])! - codeOrder.get(b.codes[0])!));
  const assigned = result.flatMap((group) => group.questions);
  if (assigned.length !== reference.length || new Set(assigned.map((k) => k.codes[0])).size !== reference.length || new Set(assigned.map((k) => k.id)).size !== reference.length) {
    throw new Error(`PCP KSB configuration: allocation must contain exactly ${reference.length} unique profile IDs and codes.`);
  }
  return result;
}

/** Approved imports may add/remove codes and explicitly change categories. */
function validatePublishedPcp(raw: unknown): ReferenceKsb[] {
  const items = (raw as { ksbs?: ReferenceKsb[] })?.ksbs;
  if (!Array.isArray(items) || !items.length) throw new Error('PCP KSB catalogue is missing.');
  const seen = new Set<string>();
  for (const item of items) {
    if (!/^[KSB][1-9]\d{0,4}$/.test(item.code) || seen.has(item.code) || !['Knowledge', 'Skill', 'Behaviour'].includes(item.type) || !item.full_description?.trim()) throw new Error('PCP KSB catalogue contains an invalid or duplicate question.');
    seen.add(item.code);
    for (const path of ['operational', 'strategic', 'chartered'] as const) {
      if (!Object.values(OWNERS[path]).includes(item.original_owner_group_by_pathway?.[path])) throw new Error(`Invalid ${path} owner for ${item.code}.`);
    }
  }
  return items;
}

export function resolvePcpRadar(profile: Ksb[] | null, state: ModulesState | null | undefined, error = ''): PcpRadar {
  const empty = { modules: [], questions: [] };
  if (error) return { ...empty, status: 'error', message: error };
  if (profile === null || !state) return { ...empty, status: 'loading', message: 'Loading saved PCP modules and Skills Radar questions…' };
  if (!state.selectionComplete || !state.savedSelection) return { ...empty, status: 'waiting', message: 'Complete and save your PCP module selection in Programme Suitability & Modules before answering Skills Radar.' };
  try {
    const modules = allocatePcpQuestions(profile, state);
    return { status: 'ready', message: '', modules, questions: modules.flatMap((module) => module.questions) };
  } catch (failure) {
    return { ...empty, status: 'error', message: failure instanceof Error ? failure.message : 'Invalid PCP KSB configuration.' };
  }
}
