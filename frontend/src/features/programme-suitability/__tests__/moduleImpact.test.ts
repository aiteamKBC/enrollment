import { describe, expect, it } from 'vitest';
import { assessmentQuestionIds, bindImpactModules, isPotentialPriorLearning, matchesQuestionStandard, readinessBand, screenModule, screenedModules, screeningRuleFromImpact, weightedReadiness, type ImpactModule, type ScreeningRule } from '../skills-radar/moduleImpact';
import type { KsbAssessment } from '@/pages/users/types';
import type { Ksb } from '@/pages/users/types';

const module = (weights: (string | undefined)[]): ImpactModule => ({
  id: 'social-media', name: 'Social Media', status: 'ready', revisionId: 'verified-revision', ksbs: weights.map((weight, index) => ({
    id: `local-${index}`, code: `K${index + 1}`, description: `Knowledge ${index + 1}`,
    kind: 'Knowledge', standard: 'ST0596', standardVersion: '1.0',
    ...(weight === undefined ? {} : { weight: { total_weight: weight, hard_weight: weight,
      hard_count: 1, mapping_count: 1 } }),
  })),
});
const answers = (levels: (KsbAssessment['level'] | null)[]) => Object.fromEntries(levels.map((level, index) =>
  [`profile-${index}`, { ksbId: `profile-${index}`, level, evidenceFiles: [], actionPlan: null }]));
const ids = new Map([['K1', 'profile-0'], ['K2', 'profile-1']]);
const rule = (changes: Partial<ScreeningRule> = {}): ScreeningRule => ({
  minimumQualifyingRating: 5, maximumQualifyingRating: 9, minimumHighRatedHardKsbs: 2, ...changes,
});

describe('weighted module readiness', () => {
  it('uses both configured rating bounds and the distinct Hard KSB count inclusively', () => {
    const record = module(['40', '20', '10']);
    const questionIds = new Map([...ids, ['K3', 'profile-2']]);
    const configured = rule({ minimumQualifyingRating: 6, maximumQualifyingRating: 8,
      minimumHighRatedHardKsbs: 3 });
    expect(screenModule(record, answers(['proficient', 'expert', 'mastery']), questionIds, configured).status).toBe('potential');
    expect(screenModule(record, answers(['consistently', 'expert', 'mastery']), questionIds, configured).status).toBe('none');
    expect(screenModule(record, answers(['proficient', 'expert', 'champion']), questionIds, configured).status).toBe('none');
    expect(screenModule(record, answers(['proficient', 'expert', null]), questionIds,
      rule({ ...configured, minimumHighRatedHardKsbs: 2 })).status).toBe('potential');
    expect(screenModule(record, answers(['proficient', 'expert', 'mastery']), questionIds, null).status).toBe('unavailable');
    expect(screeningRuleFromImpact({ status: 'ready', modules: [], ...configured })).toEqual(configured);
    expect(screeningRuleFromImpact({ status: 'ready', modules: [], ...rule({ minimumQualifyingRating: 9,
      maximumQualifyingRating: 5 }) })).toBeNull();
  });
  it('flags two distinct verified Hard KSBs rated 5–9 without needing complete assessment', () => {
    const record = module(['40', '20']);
    record.ksbs.push({ ...record.ksbs[0], id: 'local-2', code: 'K3' });
    expect(screenModule(record, answers(['consistently', 'mastery', null]), ids, rule()).status).toBe('potential');
    expect(screenModule(record, answers(['consistently', 'never', null]), ids, rule()).status).toBe('none');
    expect(screenModule(record, answers(['rarely', 'mastery', null]), ids, rule()).status).toBe('none');
    expect(screenModule(record, answers(['champion', 'mastery', null]), ids, rule({ minimumHighRatedHardKsbs: 3 })).status).toBe('none');
    expect(screenModule(record, answers(['champion', 'mastery', null]), ids, rule()).status).toBe('potential');
    expect(screenModule({ ...record, status: 'unavailable' }, answers(['champion', 'mastery']), ids, rule()).status).toBe('unavailable');
  });
  it('counts a shared KSB independently per module and never counts duplicate activities twice', () => {
    const first = module(['40', '20']);
    first.ksbs[0].weight!.hard_count = 26;
    first.ksbs[0].weight!.mapping_count = 27;
    const second = { ...module(['20', '40']), id: 'second', name: 'Second module' };
    const result = screenedModules({ status: 'ready', modules: [first, second,
      { ...first, id: 'unmapped', status: 'unavailable' }], ...rule(),
      savedLevels: { 'st0596-v1-0-K1': 'champion', 'st0596-v1-0-K2': 'consistently' } });
    expect(result.map(item => [item.name, item.qualifyingCodes])).toEqual([
      ['Social Media', ['K1', 'K2']], ['Second module', ['K1', 'K2']]]);
    expect(isPotentialPriorLearning(screenModule(first, answers(['champion', 'consistently']), ids, rule()))).toBe(true);
    expect(screenModule(first, answers(['champion', 'never']), ids, rule()).status).toBe('none');
    const duplicateIdentity = { ...first, ksbs: [first.ksbs[0], { ...first.ksbs[0], code: 'K2' }] };
    expect(screenModule(duplicateIdentity, answers(['champion', 'consistently']), ids, rule())).toMatchObject({
      status: 'none', qualifyingCodes: ['K2'],
    });
  });
  it('uses module weights and the full 1–9 scale for a shared answer', () => {
    const ratings = answers(['champion', 'never']);
    expect(weightedReadiness(module(['40', '20']), ratings, ids).score).toBe(67);
    expect(weightedReadiness(module(['20', '40']), ratings, ids).score).toBe(33);
  });
  it('uses Hard weights only while retaining Soft/Possible-only KSBs for completion', () => {
    const mixed = module(['100', '40']);
    mixed.ksbs[0].weight = { total_weight: '100', hard_weight: '60', hard_count: 1, mapping_count: 3 };
    mixed.ksbs[1].weight = { total_weight: '40', hard_weight: '0', hard_count: 0, mapping_count: 2 };
    expect(weightedReadiness(mixed, answers(['champion', 'never']), ids)).toMatchObject({
      count: 2, answered: 2, hardKsbCount: 1, coveredOnlyCount: 1,
      availableWeight: 60, score: 100,
    });
    expect(weightedReadiness(mixed, answers(['never', 'champion']), ids).score).toBe(0);
    expect(screenModule(mixed, answers(['champion', null]), ids, rule()).status).toBe('none');
  });
  it('keeps incomplete assessments separate from the answered-only score', () => {
    const result = weightedReadiness(module(['40', '20']), answers(['mastery', null]), ids);
    expect(result).toMatchObject({ answered: 1, count: 2, reason: 'incomplete', score: 88 });
  });
  it('distinguishes missing weights, zero weights and no answers', () => {
    expect(weightedReadiness(module(['40', undefined]), answers(['champion', 'never']), ids).reason).toBe('hard_weight_missing');
    expect(weightedReadiness(module(['0', '0']), answers(['champion', 'never']), ids).reason).toBe('hard_weight_unavailable');
    const historical = module(['40']);
    historical.ksbs[0].weight = { total_weight: '40', mapping_count: 1 };
    expect(weightedReadiness(historical, answers(['champion']), ids).reason).toBe('hard_weight_missing');
    const softOnly = module(['40']);
    softOnly.ksbs[0].weight = { total_weight: '40', hard_weight: '0', hard_count: 0, mapping_count: 1 };
    expect(weightedReadiness(softOnly, answers(['champion']), ids).reason).toBe('hard_weight_unavailable');
    expect(screenModule(softOnly, answers(['champion']), ids, rule()).status).toBe('none');
    softOnly.ksbs[0].weight!.hard_weight = '20';
    expect(weightedReadiness(softOnly, answers(['champion']), ids).reason).toBe('hard_weight_invalid');
    expect(weightedReadiness(module(['40', '20']), answers([null, null]), ids).reason).toBe('not_answered');
  });
  it('uses presentation bands without making RPL decisions', () => {
    expect([readinessBand(80), readinessBand(50), readinessBand(10)]).toEqual(['strong', 'mixed', 'learning']);
  });
  it('requires the question standard and version to match the local revision', () => {
    expect(matchesQuestionStandard([{ id: 'st0596-v1-0-K1' }], '', module(['40']).ksbs.map((item) => ({ ...item, standard: 'ST0596' })))).toBe(true);
    expect(matchesQuestionStandard([{ id: 'st0596-v0-9-K1' }], '', module(['40']).ksbs.map((item) => ({ ...item, standard: 'ST0596' })))).toBe(false);
  });
  it('binds only selected module identities from the matching programme', () => {
    const question: Ksb = { id: 'st0596-v1-0-K1', codes: ['K1'], title: 'Knowledge one',
      kind: 'Knowledge', theme: 'Knowledge' };
    const selected = [{ id: 'selected', title: 'Selected module', questions: [question] }];
    const record = { ...module(['40']), id: 'selected', name: 'Selected module' };
    const impact = { status: 'ready' as const, programmeId: 'ME_L4', modules: [record,
      { ...record, id: 'unrelated', name: 'Unrelated module' }] };
    const bound = bindImpactModules(selected, [question], impact, 'ME_L4', 'standard:st0596-v1-0');
    expect(bound).toHaveLength(1);
    expect(bound[0].impact?.id).toBe('selected');
    const wrongProgramme = bindImpactModules(selected, [question], impact, 'MM_L6', 'standard:st0596-v1-0');
    expect(wrongProgramme).toHaveLength(1);
    expect(wrongProgramme[0].impact).toBeUndefined();
  });
  it('uses the verified delivery KSBs while retaining an unmapped selected module', () => {
    const questions: Ksb[] = ['K1', 'K2', 'K3'].map((code) => ({
      id: `st0596-v1-0-${code}`, codes: [code], title: code, kind: 'Knowledge', theme: 'Marketing',
    }));
    const social = module(['40', '20']);
    const technology: ImpactModule = { ...module(['60']), id: 'martech', name: 'Marketing Technology (MarTech)',
      ksbs: [{ ...module(['60']).ksbs[0], code: 'K3' }] };
    const selected = [
      { id: 'marketing-impact', title: 'Marketing Impact and Planning', questions: [questions[0]] },
      { id: 'social-media', title: 'Social Media', questions: [questions[1]] },
      { id: 'martech', title: 'Marketing Technology (MarTech)', questions: [questions[2]] },
    ];
    const bound = bindImpactModules(selected, questions, { status: 'ready', programmeId: 'ME_L4', modules: [
      { id: 'marketing-impact', name: selected[0].title, status: 'unavailable', reason: 'module_mapping_unavailable', ksbs: [] },
      social, technology,
    ] }, 'ME_L4', '');
    expect(bound.map((item) => [item.id, item.impact?.status, item.questions.map((q) => q.codes[0])])).toEqual([
      ['marketing-impact', 'unavailable', ['K1']],
      ['social-media', 'ready', ['K1', 'K2']],
      ['martech', 'ready', ['K3']],
    ]);
    expect(weightedReadiness(bound[1].impact!, answers(['champion', 'never']), ids).score).toBe(67);
    expect(assessmentQuestionIds(questions, bound)).toEqual(questions.map((question) => question.id));
  });
  it('does not shrink the 33 published answers to 27 locally covered KSBs', () => {
    const questions: Ksb[] = Array.from({ length: 33 }, (_, index) => ({
      id: `st0596-v1-0-K${index + 1}`, codes: [`K${index + 1}`],
      title: `Knowledge ${index + 1}`, kind: 'Knowledge', theme: 'Marketing',
    }));
    const local = [{ id: 'social-media', title: 'Social Media', questions: questions.slice(0, 22) },
      { id: 'martech', title: 'Marketing Technology (MarTech)', questions: questions.slice(16, 27) }];
    expect(new Set(local.flatMap((item) => item.questions.map((question) => question.id))).size).toBe(27);
    expect(assessmentQuestionIds(questions, local)).toHaveLength(33);
  });
});
