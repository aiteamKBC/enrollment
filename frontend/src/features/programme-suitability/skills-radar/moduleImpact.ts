import { competenceScore, MAX_COMPETENCE_SCORE } from '@/mocks/enrolment-console';
import type { KsbAssessment } from '@/pages/users/types';
import type { Ksb } from '@/pages/users/types';
import type { RadarModule } from './allocation';

export interface ImpactKsb {
  id: string; code: string; description: string; kind: string; standard: string; standardVersion: string;
  weight?: { total_weight: string; mapping_count: number; hard_weight?: string; hard_count?: number };
}
export interface ImpactModule {
  id: string; name: string; status: 'ready' | 'unavailable'; reason?: string;
  revisionId?: string; revisionNumber?: number; ksbs: ImpactKsb[];
}
export interface ModuleImpact {
  status: 'ready' | 'unavailable'; reason?: string; programmeId?: string;
  cohortId?: string; groupId?: string; modules: ImpactModule[]; savedLevels?: Record<string, string>;
  minimumQualifyingRating?: number; maximumQualifyingRating?: number; minimumHighRatedHardKsbs?: number;
}

export interface ScreeningRule {
  minimumQualifyingRating: number;
  maximumQualifyingRating: number;
  minimumHighRatedHardKsbs: number;
}

export function screeningRuleFromImpact(impact: ModuleImpact | null): ScreeningRule | null {
  if (!impact) return null;
  const { minimumQualifyingRating: low, maximumQualifyingRating: high,
    minimumHighRatedHardKsbs: count } = impact;
  if (!Number.isInteger(low) || !Number.isInteger(high) || !Number.isInteger(count) ||
      low! < 1 || high! > MAX_COMPETENCE_SCORE || low! > high! || count! < 1 || count! > 500) return null;
  return { minimumQualifyingRating: low!, maximumQualifyingRating: high!, minimumHighRatedHardKsbs: count! };
}

export function screenModule(module: ImpactModule | undefined, answers: Record<string, KsbAssessment>,
  answerIdByCode: Map<string, string>, rule: ScreeningRule | null) {
  if (!rule || !module || module.status !== 'ready' || !module.revisionId || !module.ksbs.length)
    return { status: 'unavailable' as const, qualifyingCodes: [] as string[] };
  const qualified = new Map<string, string>();
  let incompleteMapping = false;
  for (const ksb of module.ksbs) {
    const weight = ksb.weight;
    const hard = Number(weight?.hard_weight);
    const total = Number(weight?.total_weight);
    if (!weight || weight.hard_weight == null || !Number.isInteger(weight.hard_count) ||
        !Number.isInteger(weight.mapping_count) || weight.mapping_count < 0 ||
        weight.hard_count! < 0 || weight.hard_count! > weight.mapping_count ||
        !Number.isFinite(hard) || hard < 0 || !Number.isFinite(total) || total < hard ||
        (weight.hard_count === 0 && hard !== 0)) {
      incompleteMapping = true;
      continue;
    }
    const rating = competenceScore(answers[answerIdByCode.get(ksb.code) ?? '']?.level);
    if (weight.hard_count! > 0 && rating != null && rating >= rule.minimumQualifyingRating &&
        rating <= rule.maximumQualifyingRating)
      qualified.set(ksb.id, ksb.code);
  }
  const qualifyingCodes = [...qualified.values()];
  return { status: qualifyingCodes.length >= rule.minimumHighRatedHardKsbs ? 'potential' as const
    : incompleteMapping ? 'unavailable' as const : 'none' as const, qualifyingCodes };
}

export function isPotentialPriorLearning(screening: ReturnType<typeof screenModule>): boolean {
  return screening.status === 'potential';
}

/** The profile summary uses only persisted answers and the verified, pinned revision. */
export function screenedModules(impact: ModuleImpact): { id: string; name: string; qualifyingCodes: string[] }[] {
  if (impact.status !== 'ready') return [];
  const rule = screeningRuleFromImpact(impact);
  const answers = Object.fromEntries(Object.entries(impact.savedLevels ?? {}).map(([id, level]) =>
    [id, { ksbId: id, level, evidenceFiles: [], actionPlan: null }]));
  return impact.modules.flatMap((module) => {
    if (module.status !== 'ready' || !module.revisionId) return [];
    const ids = new Map(module.ksbs.map((ksb) => [ksb.code,
      `${ksb.standard.toLowerCase()}-v${ksb.standardVersion.replace(/\./g, '-')}-${ksb.code}`]));
    const screening = screenModule(module, answers as Record<string, KsbAssessment>, ids, rule);
    return isPotentialPriorLearning(screening)
      ? [{ id: module.id, name: module.name, qualifyingCodes: screening.qualifyingCodes }] : [];
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
  if (module.ksbs.some((item) => item.weight?.hard_weight == null || item.weight?.hard_count == null))
    return { count, answered, score: null, reason: 'hard_weight_missing' };
  const weighted = module.ksbs.map((item) => ({ item, weight: Number(item.weight!.hard_weight),
    totalWeight: Number(item.weight!.total_weight), hardCount: item.weight!.hard_count!,
    mappingCount: item.weight!.mapping_count }));
  if (weighted.some(({ weight, totalWeight, hardCount, mappingCount }) =>
    !Number.isFinite(weight) || weight < 0 || !Number.isFinite(totalWeight) || totalWeight < 0 ||
    weight > totalWeight || !Number.isInteger(hardCount) || hardCount < 0 ||
    !Number.isInteger(mappingCount) || mappingCount < 0 || hardCount > mappingCount ||
    (hardCount === 0 && weight !== 0)))
    return { count, answered, score: null, reason: 'hard_weight_invalid' };
  const contributing = weighted.filter(({ weight, hardCount }) => hardCount > 0 && weight > 0);
  const availableWeight = contributing.reduce((total, { weight }) => total + weight, 0);
  if (availableWeight <= 0) return { count, answered, score: null, reason: 'hard_weight_unavailable',
    hardKsbCount: 0, coveredOnlyCount: count };
  const rated = contributing.map(({ item, weight }) => ({ weight, score: competenceScore(answers[answerIdByCode.get(item.code) ?? '']?.level) }));
  const answeredWeight = rated.reduce((total, entry) => total + (entry.score == null ? 0 : entry.weight), 0);
  if (answeredWeight <= 0) return { count, answered, score: null, reason: 'not_answered',
    hardKsbCount: contributing.length, coveredOnlyCount: count - contributing.length };
  const score = Math.round(100 * rated.reduce((total, entry) =>
    total + (entry.score == null ? 0 : entry.weight * (entry.score - 1) / (MAX_COMPETENCE_SCORE - 1)), 0) / answeredWeight);
  return { count, answered, score, reason: answered < count ? 'incomplete' : null,
    answeredWeight, availableWeight, hardKsbCount: contributing.length,
    coveredOnlyCount: count - contributing.length };
}
