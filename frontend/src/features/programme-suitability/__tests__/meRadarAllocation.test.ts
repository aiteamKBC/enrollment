import { expect, it } from 'vitest';
import { allocateMeQuestions, isMeRadar, meRadarProfileProgramme, resolveMeRadar, validateMeCatalogue } from '../skills-radar/marketingExecutive';
import source from '../skills-radar/marketing_executive_l4_ksb_full_mapping.json';
import { confirmedMe, meModules, meProfile } from './meRadarFixtures';

const assignments = {
  'marketing-impact': 'K1 K2 K4 K5 K6 K7 K8 K9 K10 S2 S6 S9 B6 B8',
  'social-media': 'K11 S1 S3 S4 S5 S7 S8 B1 B3 B7',
  martech: 'K3 S10 S11 S12 S13 S14 B2 B4 B5',
};

it('binds the exact fixed 14/10/9 mapping to all 33 existing IDs and full original descriptions without choices or redistribution', () => {
  const state = meModules(); const before = JSON.stringify([state, meProfile, source]);
  const groups = allocateMeQuestions(meProfile, state);
  expect(groups.map((group) => group.questions.length)).toEqual([14, 10, 9]);
  expect(groups.map((group) => group.sessions)).toEqual([16, 14, 14]);
  for (const group of groups) expect(group.questions.map((ksb) => ksb.codes[0])).toEqual(assignments[group.id as keyof typeof assignments].split(' '));
  const all = groups.flatMap((group) => group.questions);
  expect(all).toHaveLength(33); expect(new Set(all.map((ksb) => ksb.id)).size).toBe(33); expect(new Set(all.map((ksb) => ksb.codes[0])).size).toBe(33);
  for (const question of all) {
    expect(question.id).toBe(meProfile.find((ksb) => ksb.codes[0] === question.codes[0])!.id);
    expect(question.title).toBe(source.ksbs.find((ksb) => ksb.code === question.codes[0])!.full_description);
  }
  expect(JSON.stringify([state, meProfile, source])).toBe(before);
  expect(allocateMeQuestions(structuredClone(meProfile), JSON.parse(JSON.stringify(state)))).toEqual(groups);
});

it('keeps 11 Knowledge, 14 Skill, 8 Behaviour and exact punctuation, including K1, S2 and B8', () => {
  const { questions } = validateMeCatalogue();
  expect(['Knowledge', 'Skill', 'Behaviour'].map((kind) => questions.filter((item) => item.category === kind).length)).toEqual([11, 14, 8]);
  expect(questions[0].full_description).toBe('The fundamentals of marketing theory that support the marketing process e.g. the extended marketing mix (7P’S: Product, Price, Place, Promotion, Physical environment, Process, People), product development, and segmentation');
  expect(questions.find((item) => item.code === 'S2')!.full_description).toBe('Plan and deliver tactical campaigns against SMART (Specific, Measurable, Achievable, Realistic, Time-bound) objectives');
  expect(questions.find((item) => item.code === 'B8')!.full_description).toBe('Ethical behaviour in the way they approach marketing activities and their work, valuing equality & diversity.');
});

it('keeps ownership by stable ID after published module renames, session changes and display reordering', () => {
  const state = meModules();
  state.configuration.programmes.find((item) => item.id === 'ME_L4')!.modules.forEach((item, index) => {
    item.title = `Renamed ${item.id}`; item.sessions = 30 + index; item.order = 10 - index;
  });
  const groups = allocateMeQuestions([...meProfile].reverse(), state);
  expect(groups.map((group) => group.id)).toEqual(Object.keys(assignments).reverse());
  expect(groups[0]).toMatchObject({ title: 'Renamed martech', sessions: 32 });
  for (const group of groups) expect(group.questions.map((ksb) => ksb.codes[0])).toEqual(assignments[group.id as keyof typeof assignments].split(' '));
});

it('retains arbitrary existing database IDs without generating new answer identities', () => {
  const profile = meProfile.map((ksb, index) => ({ ...ksb, id: `existing-row-${index}` }));
  for (const item of allocateMeQuestions(profile, meModules()).flatMap((group) => group.questions)) {
    expect(item.id).toBe(profile.find((ksb) => ksb.codes[0] === item.codes[0])!.id);
  }
});

it.each(['', 'K6', 'Knowledge 6', 'KSB Question'])('identifies K6 rather than rendering invalid description %s', (text) => {
  const broken = structuredClone(source); broken.ksbs.find((item) => item.code === 'K6')!.full_description = text;
  expect(() => validateMeCatalogue(broken)).toThrow(/K6/);
});

it('identifies missing/duplicate codes, missing ownership and inconsistent assignments', () => {
  expect(() => validateMeCatalogue({ ...source, ksbs: source.ksbs.slice(1) })).toThrow(/K1/);
  expect(() => validateMeCatalogue({ ...source, ksbs: [...source.ksbs, source.ksbs[0]] })).toThrow(/K1/);
  const duplicate = structuredClone(source); duplicate.modules[1].ksb_codes[0] = 'K1';
  expect(() => validateMeCatalogue(duplicate)).toThrow(/K1/);
  const unassigned = structuredClone(source); unassigned.modules[0].ksb_codes.pop();
  expect(() => validateMeCatalogue(unassigned)).toThrow(/missing module assignments for B8/);
  const wrongOwner = structuredClone(source); wrongOwner.ksbs[0].module_name = 'Social Media';
  expect(() => validateMeCatalogue(wrongOwner)).toThrow(/K1/);
});

it('reports missing/ambiguous profile records and absent published modules without losing questions', () => {
  expect(() => allocateMeQuestions(meProfile.slice(1), meModules())).toThrow(/K1/);
  expect(() => allocateMeQuestions([...meProfile, meProfile[0]], meModules())).toThrow(/K1/);
  const ambiguous = structuredClone(meProfile); ambiguous[1].id = ambiguous[0].id;
  expect(() => allocateMeQuestions(ambiguous, meModules())).toThrow(/K2/);
  const state = meModules(); state.configuration.programmes.find((item) => item.id === 'ME_L4')!.modules[2].active = false;
  expect(() => allocateMeQuestions(meProfile, state)).toThrow(/martech.*K3.*S10.*B5/);
});

it('uses the confirmed programme identity and keeps MM/APM/PCP separate from ME mapping', () => {
  expect(isMeRadar(confirmedMe)).toBe(true);
  for (const selectedProgramme of ['MM_L6', 'APM_L4', 'PCP_L6'] as const) expect(isMeRadar({ ...confirmedMe, selectedProgramme })).toBe(false);
  expect(isMeRadar({ ...confirmedMe, selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION' })).toBe(false);
  expect(meRadarProfileProgramme(confirmedMe)).toBe('Marketing Executive Level 4');
  const state = structuredClone(confirmedMe); state.configuration!.programmes.find((item) => item.id === 'ME_L4')!.catalogueId = 'PROG-ME-L4';
  expect(meRadarProfileProgramme(state)).toBe('PROG-ME-L4');
  expect(resolveMeRadar(null, null).status).toBe('loading');
  expect(resolveMeRadar(meProfile, meModules(), 'Modules unavailable')).toMatchObject({ status: 'error', message: 'Modules unavailable', questions: [] });
  expect(resolveMeRadar(meProfile, { ...meModules(), selectedProgramme: 'MM_L6' }).status).toBe('error');
});
