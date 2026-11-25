import { describe, expect, it } from 'vitest';
import { assessmentQuestionIds, bindImpactModules, matchesQuestionStandard, readinessBand, weightedReadiness, type ImpactModule } from '../skills-radar/moduleImpact';
import type { KsbAssessment } from '@/pages/users/types';
import type { Ksb } from '@/pages/users/types';

const module = (weights: (string | undefined)[]): ImpactModule => ({
  id: 'social-media', name: 'Social Media', status: 'ready', revisionId: 'verified-revision', ksbs: weights.map((weight, index) => ({
    id: `local-${index}`, code: `K${index + 1}`, description: `Knowledge ${index + 1}`,
    kind: 'Knowledge', standard: 'ST0596', standardVersion: '1.0',
    ...(weight === undefined ? {} : { weight: { total_weight: weight, mapping_count: 1 } }),
  })),
});
const answers = (levels: (KsbAssessment['level'] | null)[]) => Object.fromEntries(levels.map((level, index) =>
  [`profile-${index}`, { ksbId: `profile-${index}`, level, evidenceFiles: [], actionPlan: null }]));
const ids = new Map([['K1', 'profile-0'], ['K2', 'profile-1']]);

describe('weighted module readiness', () => {
  it('uses module weights and the full 1–9 scale for a shared answer', () => {
    const ratings = answers(['champion', 'never']);
    expect(weightedReadiness(module(['40', '20']), ratings, ids).score).toBe(67);
    expect(weightedReadiness(module(['20', '40']), ratings, ids).score).toBe(33);
  });
  it('keeps incomplete assessments separate from the answered-only score', () => {
    const result = weightedReadiness(module(['40', '20']), answers(['mastery', null]), ids);
    expect(result).toMatchObject({ answered: 1, count: 2, reason: 'incomplete', score: 88 });
  });
  it('distinguishes missing weights, zero weights and no answers', () => {
    expect(weightedReadiness(module(['40', undefined]), answers(['champion', 'never']), ids).reason).toBe('weight_missing');
    expect(weightedReadiness(module(['0', '0']), answers(['champion', 'never']), ids).reason).toBe('weight_zero');
    expect(weightedReadiness(module(['40', '20']), answers([null, null]), ids).reason).toBe('not_answered');
  });
  it('uses presentation bands without making RPL decisions', () => {
    expect([readinessBand(80), readinessBand(50), readinessBand(10)]).toEqual(['strong', 'mixed', 'learning']);
  });
  it('requires the question standard and version to match the local revision', () => {
    expect(matchesQuestionStandard([{ id: 'st0596-v1-0-K1' }], '', module(['40']).ksbs.map((item) => ({ ...item, standard: 'ST0596' })))).toBe(true);
    expect(matchesQuestionStandard([{ id: 'st0596-v0-9-K1' }], '', module(['40']).ksbs.map((item) => ({ ...item, standard: 'ST0596' })))).toBe(false);
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
