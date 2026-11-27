import type { KsbAssessment } from '@/pages/users/types';
import { screenModule, screeningRuleFromImpact, type BoundModule, type ModuleImpact } from '../skills-radar/moduleImpact';

export interface AcademicModule { id: string; name: string; weeks: number; revisionIds: string[] }
export interface Offering {
  id: string; programmeId: string; moduleId: string; cohortId: string; cohortName: string;
  cohortStart?: string;
  groupId?: string; groupName?: string; start: string; end: string; home: boolean; extendsWindow: boolean;
}
export interface YourModulesData {
  status: 'ready' | 'unavailable'; message?: string; version?: number;
  selectedProgramme?: string; programmeId?: string; programmeName?: string;
  homeCohort?: { id: string; name: string; start: string; end: string };
  evaluatedOn?: string; requiresModuleSelection?: boolean; modules: AcademicModule[]; offerings?: Offering[];
}
export type Result = 'potential' | 'none' | 'unavailable';

/** The same bound revisions, persisted answers and calculator as Skills Radar.
 * A revision UUID is a stable bridge to its Programme Structure module; names
 * never match a module or open another programme's offerings.
 */
export function moduleResults(data: YourModulesData, impact: ModuleImpact | null | undefined, bound: BoundModule[]) {
  const matches = new Map<string, BoundModule[]>();
  const answers = Object.fromEntries(Object.entries(impact?.savedLevels ?? {}).map(([id, level]) =>
    [id, { ksbId: id, level, evidenceFiles: [], actionPlan: null }])) as Record<string, KsbAssessment>;
  const rule = screeningRuleFromImpact(impact ?? null);
  const byCode = new Map<string, string>();
  const ambiguous = new Set<string>();
  for (const module of bound) for (const question of module.questions) for (const code of question.codes) {
    if (byCode.has(code) && byCode.get(code) !== question.id) ambiguous.add(code);
    else byCode.set(code, question.id);
  }
  for (const code of ambiguous) byCode.delete(code);
  for (const module of data.modules) matches.set(module.id, impact?.status === 'ready' && impact.programmeId === data.selectedProgramme
    ? bound.filter(item => item.impact?.revisionId && module.revisionIds.includes(item.impact.revisionId)) : []);
  const results = new Map<string, Result>();
  for (const module of data.modules) {
    const records = matches.get(module.id) ?? [];
    const record = records.length === 1 ? records[0].impact : undefined;
    results.set(module.id, impact?.savedLevels == null ? 'unavailable' : screenModule(record, answers, byCode, rule).status);
  }
  // PCP elective/pathway scope comes from the existing saved Skills Radar
  // module selection. Never substitute every optional module when it is absent.
  const selectionKnown = !data.requiresModuleSelection || Boolean(impact?.status === 'ready' &&
    impact.programmeId === data.selectedProgramme && bound.length && bound.length === impact.modules.length &&
    impact.modules.every(module => bound.some(item => item.id === module.id)) &&
    bound.every(item => data.modules.filter(module => item.impact?.revisionId &&
      module.revisionIds.includes(item.impact.revisionId)).length === 1));
  return { results, selectionKnown, modules: data.requiresModuleSelection
    ? data.modules.filter(module => (matches.get(module.id)?.length ?? 0) > 0) : data.modules };
}

export function permittedOfferings(data: YourModulesData, moduleId: string, allowAlternatives: boolean): Offering[] {
  const home = data.homeCohort;
  if (!home) return [];
  return (data.offerings ?? []).filter(offering => offering.moduleId === moduleId &&
    offering.programmeId === data.programmeId && (offering.cohortId === home.id ||
      (allowAlternatives && offering.start >= home.start && offering.start <= home.end &&
        offering.start >= (data.evaluatedOn ?? londonToday()))));
}

export function londonToday() {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(new Date());
  const part = (type: string) => parts.find(item => item.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

export function dateLabel(iso: string) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
}
