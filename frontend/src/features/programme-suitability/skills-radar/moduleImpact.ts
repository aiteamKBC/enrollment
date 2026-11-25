import { competenceScore, MAX_COMPETENCE_SCORE } from '@/mocks/enrolment-console';
import type { KsbAssessment } from '@/pages/users/types';
import type { Ksb } from '@/pages/users/types';
import type { RadarModule } from './allocation';

export interface ImpactKsb {
  id: string; code: string; description: string; kind: string; standard: string; standardVersion: string;
  weight?: { total_weight: string; mapping_count: number };
}
export interface ImpactModule {
  id: string; name: string; status: 'ready' | 'unavailable'; reason?: string;
  revisionId?: string; revisionNumber?: number; ksbs: ImpactKsb[];
}
export interface ModuleImpact {
  status: 'ready' | 'unavailable'; reason?: string; programmeId?: string;
  cohortId?: string; groupId?: string; modules: ImpactModule[]; savedLevels?: Record<string, string>;
  screeningThresholdPercent?: number;
}

export function isPotentialPriorLearning(readiness: ReturnType<typeof weightedReadiness> | null | undefined,
  threshold: number): boolean {
  return Boolean(readiness && readiness.reason === null && readiness.count > 0 &&
    readiness.answered === readiness.count && readiness.score != null && readiness.score >= threshold);
}

/** The profile summary uses only persisted answers and the verified, pinned revision. */
export function screenedModules(impact: ModuleImpact): { id: string; name: string; score: number }[] {
  if (impact.status !== 'ready') return [];
  const answers = Object.fromEntries(Object.entries(impact.savedLevels ?? {}).map(([id, level]) =>
    [id, { ksbId: id, level, evidenceFiles: [], actionPlan: null }]));
  return impact.modules.flatMap((module) => {
    if (module.status !== 'ready' || !module.revisionId) return [];
    const ids = new Map(module.ksbs.map((ksb) => [ksb.code,
      `${ksb.standard.toLowerCase()}-v${ksb.standardVersion.replace(/\./g, '-')}-${ksb.code}`]));
    const readiness = weightedReadiness(module, answers as Record<string, KsbAssessment>, ids);
    return isPotentialPriorLearning(readiness, impact.screeningThresholdPercent ?? 50)
      ? [{ id: module.id, name: module.name, score: readiness.score! }] : [];
  });
}

export function matchesQuestionStandard(questions: { id: string }[], declared: string, ksbs: ImpactKsb[]): boolean {
  const pinned = declared.trim().toLowerCase();
  const ids = questions.map((item) => item.id.match(/^([a-z0-9]+-v\d+(?:-\d+)+)-[ksb]\d+$/i)?.[1]?.toLowerCase());
  const inferred = ids.length > 0 && ids.every((item) => item && item === ids[0]) ? `standard:${ids[0]}` : '';
  const expected = pinned || inferred;
  return Boolean(expected && (!pinned || !inferred || pinned === inferred) && ksbs.every((item) =>
    `standard:${item.standard.toLowerCase()}-v${item.standardVersion.replace(/\./g, '-')}` === expected));
}

export type BoundModule = RadarModule & { impact?: ImpactModule };
/** Keep every published question even when local revision coverage is narrower. */
export function assessmentQuestionIds(questions: Ksb[], modules: BoundModule[]): string[] {
  return [...new Set([
    ...questions.map((question) => question.id),
    ...modules.flatMap((module) => module.questions.map((question) => question.id)),
  ])];
}
/** Bind only an exact local module/KSB identity to the pinned wizard questions. */
export function bindImpactModules(modules: RadarModule[], questions: Ksb[], impact: ModuleImpact | null,
  programmeId: string | undefined, standardId: string): BoundModule[] {
  const byCode = new Map<string, Ksb>();
  const ambiguous = new Set<string>();
  for (const question of questions) for (const code of question.codes) {
    if (byCode.has(code) && byCode.get(code)?.id !== question.id) ambiguous.add(code);
    else byCode.set(code, question);
  }
  for (const code of ambiguous) byCode.delete(code);
  const local = new Map((impact && impact.programmeId === programmeId ? impact.modules : []).map((item) => [item.id, item]));
  return modules.map((module) => {
    const record = local.get(module.id);
    if (!record || record.status !== 'ready') return { ...module, impact: record };
    const mapped = record.ksbs.map((item) => byCode.get(item.code));
    if (mapped.some((item) => !item) || new Set(mapped.map((item) => item!.id)).size !== mapped.length)
      return { ...module, impact: { ...record, status: 'unavailable', reason: 'question_version_mismatch' } };
    if (!matchesQuestionStandard(mapped as Ksb[], standardId, record.ksbs))
      return { ...module, impact: { ...record, status: 'unavailable', reason: 'question_standard_mismatch' } };
    return { ...module, questions: mapped as Ksb[], impact: record };
  });
}

// Presentation bands follow the existing 1–9 answer labels. They are
// configurable self-assessment descriptions, never RPL reduction thresholds.
export const SELF_ASSESSMENT_BANDS = { strong: 7, mixed: 5 } as const;
export function readinessBand(score: number): 'strong' | 'mixed' | 'learning' {
  const scalePoint = 1 + score / 100 * (MAX_COMPETENCE_SCORE - 1);
  return scalePoint >= SELF_ASSESSMENT_BANDS.strong ? 'strong'
    : scalePoint >= SELF_ASSESSMENT_BANDS.mixed ? 'mixed' : 'learning';
}

/** The rating is relative to the 1–9 answer scale; it is never an RPL decision. */
export function weightedReadiness(module: ImpactModule, answers: Record<string, KsbAssessment>,
  answerIdByCode: Map<string, string>) {
  const count = module.ksbs.length;
  const answered = module.ksbs.filter((item) => competenceScore(answers[answerIdByCode.get(item.code) ?? '']?.level) != null).length;
  if (module.status !== 'ready' || !count) return { count, answered, score: null, reason: 'mapping_unavailable' };
  if (module.ksbs.some((item) => item.weight?.total_weight == null)) return { count, answered, score: null, reason: 'weight_missing' };
  const weighted = module.ksbs.map((item) => ({ item, weight: Number(item.weight!.total_weight) }));
  if (weighted.some(({ weight }) => !Number.isFinite(weight) || weight < 0)) return { count, answered, score: null, reason: 'weight_invalid' };
  const availableWeight = weighted.reduce((total, { weight }) => total + weight, 0);
  if (availableWeight <= 0) return { count, answered, score: null, reason: 'weight_zero' };
  const rated = weighted.map(({ item, weight }) => ({ weight, score: competenceScore(answers[answerIdByCode.get(item.code) ?? '']?.level) }));
  const answeredWeight = rated.reduce((total, entry) => total + (entry.score == null ? 0 : entry.weight), 0);
  if (answeredWeight <= 0) return { count, answered, score: null, reason: 'not_answered' };
  const score = Math.round(100 * rated.reduce((total, entry) =>
    total + (entry.score == null ? 0 : entry.weight * (entry.score - 1) / (MAX_COMPETENCE_SCORE - 1)), 0) / answeredWeight);
  return { count, answered, score, reason: answered < count ? 'incomplete' : null,
    answeredWeight, availableWeight };
}
