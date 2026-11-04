import { expect, it } from 'vitest';
import { allocateMmQuestions, isMmRadar, mmRadarProfileProgramme, resolveMmRadar, validateMmCatalogue } from '../skills-radar/marketingManager';
import source from '../skills-radar/marketing_manager_l6_ksb_full_mapping.json';
import { confirmedMm, mmModules, mmProfile } from './mmRadarFixtures';

const assignments = {
  'marketing-strategy': 'K1 K2 K5 K6 K7 K9 K13 S1 S3 S4 S5 S7 S10 S17 B2 B6',
  'customer-journey': 'K3 K4 K14 S2 S8 S15 B5 B7 B8',
  'commercial-intelligence': 'K8 K10 K11 S6 S9 S11 S12 S13 S14 S16 B4',
  'ai-marketing': 'K12 S18 S19 B1 B3 B9',
};

it('binds the exact 16/9/11/6 fixed allocation to existing profile IDs and all 42 original descriptions', () => {
  const state = mmModules(); const before = JSON.stringify([state, mmProfile, source]);
  const groups = allocateMmQuestions(mmProfile, state);
  expect(groups.map((group) => group.questions.length)).toEqual([16, 9, 11, 6]);
  expect(groups.map((group) => group.sessions)).toEqual([16, 14, 14, 14]);
  for (const group of groups) expect(group.questions.map((ksb) => ksb.codes[0])).toEqual(assignments[group.id as keyof typeof assignments].split(' '));
  const all = groups.flatMap((group) => group.questions);
  expect(all).toHaveLength(42);
  expect(new Set(all.map((ksb) => ksb.id)).size).toBe(42);
  expect(new Set(all.map((ksb) => ksb.codes[0])).size).toBe(42);
  for (const question of all) {
    expect(question.id).toBe(mmProfile.find((ksb) => ksb.codes[0] === question.codes[0])!.id);
    expect(question.title).toBe(source.ksbs.find((ksb) => ksb.code === question.codes[0])!.full_description);
  }
  expect(JSON.stringify([state, mmProfile, source])).toBe(before);
  expect(allocateMmQuestions(structuredClone(mmProfile), JSON.parse(JSON.stringify(state)))).toEqual(groups);
});

it('keeps 14 Knowledge, 19 Skill and 9 Behaviour records and preserves original punctuation and S14 spelling', () => {
  const { questions } = validateMmCatalogue();
  expect(['Knowledge', 'Skill', 'Behaviour'].map((kind) => questions.filter((item) => item.category === kind).length)).toEqual([14, 19, 9]);
  expect(questions.find((item) => item.code === 'S14')!.full_description).toBe('Construct a robust buness case in support of marketing activities/plans');
  expect(questions[0].full_description).toContain('7P’S: Product, Price, Place, Promotion, Physical environment, Process, People');
  expect(questions.find((item) => item.code === 'K12')!.full_description).toBe('New and emerging trends and themes in marketing.');
});

it('uses published titles/sessions by stable IDs after admin renaming and reordering, without any selection or redistribution', () => {
  const state = mmModules();
  state.configuration.programmes.find((item) => item.id === 'MM_L6')!.modules.forEach((item, index) => {
    item.title = `Renamed ${item.id}`; item.sessions = 30 + index; item.order = 10 - index;
  });
  const groups = allocateMmQuestions([...mmProfile].reverse(), state);
  expect(groups.map((group) => group.id)).toEqual(Object.keys(assignments).reverse());
  expect(groups[0]).toMatchObject({ title: 'Renamed ai-marketing', sessions: 33 });
  for (const group of groups) expect(group.questions.map((ksb) => ksb.codes[0])).toEqual(assignments[group.id as keyof typeof assignments].split(' '));
});

it('keeps custom existing profile identifiers rather than deriving IDs from code or module membership', () => {
  const profile = mmProfile.map((ksb, index) => ({ ...ksb, id: `existing-db-row-${index}` }));
  const all = allocateMmQuestions(profile, mmModules()).flatMap((group) => group.questions);
  for (const item of all) expect(item.id).toBe(profile.find((ksb) => ksb.codes[0] === item.codes[0])!.id);
});

it.each(['', 'K6', 'Knowledge 6', 'KSB Question'])('reports K6 for an invalid full description %s', (text) => {
  const broken = structuredClone(source); broken.ksbs.find((item) => item.code === 'K6')!.full_description = text;
  expect(() => validateMmCatalogue(broken)).toThrow(/K6/);
});

it('identifies missing/duplicate codes and inconsistent source assignments', () => {
  expect(() => validateMmCatalogue({ ...source, ksbs: source.ksbs.slice(1) })).toThrow(/K1/);
  expect(() => validateMmCatalogue({ ...source, ksbs: [...source.ksbs, source.ksbs[0]] })).toThrow(/K1/);
  const duplicate = structuredClone(source); duplicate.modules[1].ksb_codes[0] = 'K1';
  expect(() => validateMmCatalogue(duplicate)).toThrow(/K1/);
  const wrongOwner = structuredClone(source); wrongOwner.ksbs[0].module_name = 'AI in Marketing';
  expect(() => validateMmCatalogue(wrongOwner)).toThrow(/K1/);
  const unassigned = structuredClone(source); unassigned.modules[0].ksb_codes.pop();
  expect(() => validateMmCatalogue(unassigned)).toThrow(/missing module assignments for B6/);
});

it('identifies missing/ambiguous profile records and missing published modules without dropping questions', () => {
  expect(() => allocateMmQuestions(mmProfile.slice(1), mmModules())).toThrow(/K1/);
  expect(() => allocateMmQuestions([...mmProfile, mmProfile[0]], mmModules())).toThrow(/K1/);
  const ambiguous = structuredClone(mmProfile); ambiguous[1].id = ambiguous[0].id;
  expect(() => allocateMmQuestions(ambiguous, mmModules())).toThrow(/K2/);
  const state = mmModules(); state.configuration.programmes.find((item) => item.id === 'MM_L6')!.modules[3].active = false;
  expect(() => allocateMmQuestions(mmProfile, state)).toThrow(/ai-marketing.*K12.*S18.*B9/);
});

it('does not group a recommendation-only or alternative ME selection and uses the confirmed catalogue identity', () => {
  expect(isMmRadar(confirmedMm)).toBe(true);
  expect(isMmRadar({ ...confirmedMm, selectedProgramme: 'ME_L4' })).toBe(false);
  expect(isMmRadar({ ...confirmedMm, selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION' })).toBe(false);
  expect(mmRadarProfileProgramme(confirmedMm)).toBe('Marketing Manager Level 6');
  const state = structuredClone(confirmedMm); state.configuration!.programmes.find((item) => item.id === 'MM_L6')!.catalogueId = 'PROG-MM-L6';
  expect(mmRadarProfileProgramme(state)).toBe('PROG-MM-L6');
  expect(resolveMmRadar(null, null).status).toBe('loading');
  expect(resolveMmRadar(mmProfile, mmModules(), 'Published modules unavailable')).toMatchObject({ status: 'error', message: 'Published modules unavailable', questions: [] });
  expect(resolveMmRadar(mmProfile, { ...mmModules(), selectedProgramme: 'PCP_L6' }).status).toBe('error');
});
