import { expect, it } from 'vitest';
import { allocateApmQuestions, isApmRadar, apmRadarProfileProgramme, resolveApmRadar, validateApmCatalogue } from '../skills-radar/associateProjectManager';
import source from '../skills-radar/associate_project_manager_l4_ksb_full_mapping.json';
import { confirmedApm, apmModules, apmProfile } from './apmRadarFixtures';

const assignments = {
  pmp: 'K1 K2 K3 K4 K5 K6 K7 K8 K9 K10 K11 K12 K13 K14 K16 K17 K18 K21 K22 K23 K24 K25 K28 K29 S2 S3 S4 S5 S6 S9 S10 S11 S12 S14 S15 S17 S18 S21 S26 B1 B2 B3',
  'ai-project-management': 'K15 K19 K20 K26 K27 K30 K31 S1 S7 S8 S13 S16 S19 S20 S22 S23 S24 S25 B4 B5',
};

it('allocates exactly 42/20 fixed KSBs with all 62 existing IDs and complete original descriptions', () => {
  const state = apmModules(); const before = JSON.stringify([state, apmProfile, source]);
  const groups = allocateApmQuestions(apmProfile, state);
  expect(groups.map((group) => group.questions.length)).toEqual([42, 20]);
  expect(groups.map((group) => group.sessions)).toEqual([28, 14]);
  for (const group of groups) expect(group.questions.map((ksb) => ksb.codes[0])).toEqual(assignments[group.id as keyof typeof assignments].split(' '));
  const all = groups.flatMap((group) => group.questions);
  expect(all).toHaveLength(62); expect(new Set(all.map((ksb) => ksb.id)).size).toBe(62); expect(new Set(all.map((ksb) => ksb.codes[0])).size).toBe(62);
  for (const question of all) {
    expect(question.id).toBe(apmProfile.find((ksb) => ksb.codes[0] === question.codes[0])!.id);
    expect(question.title).toBe(source.ksbs.find((ksb) => ksb.code === question.codes[0])!.full_description);
  }
  expect(JSON.stringify([state, apmProfile, source])).toBe(before);
  expect(allocateApmQuestions(structuredClone(apmProfile), JSON.parse(JSON.stringify(state)))).toEqual(groups);
});

it('preserves all 31 Knowledge, 26 Skill and 5 Behaviour and the exact K1/K4/S1/B5 text', () => {
  const { questions } = validateApmCatalogue();
  expect(['Knowledge', 'Skill', 'Behaviour'].map((kind) => questions.filter((item) => item.type === kind).length)).toEqual([31, 26, 5]);
  expect(questions[0].full_description).toBe('The differences between projects and business as usual.');
  expect(questions.find((item) => item.code === 'K4')!.full_description).toBe('Techniques used to understand the project context, such as PESTLE (political, economic, social, technological, legal, and environmental), SWOT (strength, weakness, opportunities, threats) or VUCA (velocity, uncertainty, complexity, ambiguity).');
  expect(questions.find((item) => item.code === 'S1')!.full_description).toBe('Use project monitoring and reporting techniques to track, interpret and report on performance.');
  expect(questions.find((item) => item.code === 'B5')!.full_description).toBe('Seeks learning opportunities and continuous professional development.');
  const groups = allocateApmQuestions(apmProfile, apmModules());
  expect(groups.map((group) => ['Knowledge', 'Skill', 'Behaviour'].map((kind) => group.questions.filter((item) => item.kind === kind).length))).toEqual([[24, 15, 3], [7, 11, 2]]);
});

it('uses stable source keys and published IDs after renaming, session edits, source and display reordering', () => {
  const state = apmModules();
  state.configuration.programmes.find((item) => item.id === 'APM_L4')!.modules.forEach((item, index) => {
    item.title = `Renamed ${item.id}`; item.sessions = 30 + index; item.order = 10 - index;
  });
  const shuffled = { ...source, modules: [...source.modules].reverse(), ksbs: [...source.ksbs].reverse() };
  const groups = allocateApmQuestions([...apmProfile].reverse(), state, shuffled);
  expect(groups.map((group) => group.id)).toEqual(Object.keys(assignments).reverse());
  expect(groups[0]).toMatchObject({ title: 'Renamed ai-project-management', sessions: 31 });
  for (const group of groups) expect(group.questions.map((ksb) => ksb.codes[0])).toEqual(assignments[group.id as keyof typeof assignments].split(' '));
});

it('retains arbitrary existing programme-specific database IDs rather than generating code-only answer keys', () => {
  const profile = apmProfile.map((ksb, index) => ({ ...ksb, id: `existing-apm-row-${index}` }));
  for (const item of allocateApmQuestions(profile, apmModules()).flatMap((group) => group.questions)) {
    expect(item.id).toBe(profile.find((ksb) => ksb.codes[0] === item.codes[0])!.id);
  }
});

it.each(['', 'K6', 'Knowledge 6', 'KSB Question'])('reports K6 instead of displaying invalid description %s', (text) => {
  const broken = structuredClone(source); broken.ksbs.find((item) => item.code === 'K6')!.full_description = text;
  expect(() => validateApmCatalogue(broken)).toThrow(/K6/);
});

it('identifies duplicate/missing codes, omitted ownership, and inconsistent reference keys', () => {
  expect(() => validateApmCatalogue({ ...source, ksbs: source.ksbs.slice(1) })).toThrow(/K1/);
  expect(() => validateApmCatalogue({ ...source, ksbs: [...source.ksbs, source.ksbs[0]] })).toThrow(/K1/);
  const duplicate = structuredClone(source); duplicate.modules[1].ksb_codes[0] = 'K1';
  expect(() => validateApmCatalogue(duplicate)).toThrow(/K1/);
  const unassigned = structuredClone(source); unassigned.modules[0].ksb_codes.pop();
  expect(() => validateApmCatalogue(unassigned)).toThrow(/missing module assignments for B3/);
  const wrongOwner = structuredClone(source); wrongOwner.ksbs[0].module_mapping_key = 'apm_l4_ai';
  expect(() => validateApmCatalogue(wrongOwner)).toThrow(/K1/);
  const missingGroup = structuredClone(source); missingGroup.modules[0].mapping_key = 'unknown-group';
  expect(() => validateApmCatalogue(missingGroup)).toThrow(/apm_l4_pmp.*K1.*B3/);
});

it('reports missing/ambiguous profile records and absent APM modules without a partial assessment', () => {
  expect(() => allocateApmQuestions(apmProfile.slice(1), apmModules())).toThrow(/K1/);
  expect(() => allocateApmQuestions([...apmProfile, apmProfile[0]], apmModules())).toThrow(/K1/);
  const ambiguous = structuredClone(apmProfile); ambiguous[1].id = ambiguous[0].id;
  expect(() => allocateApmQuestions(ambiguous, apmModules())).toThrow(/K2/);
  const state = apmModules(); state.configuration.programmes.find((item) => item.id === 'APM_L4')!.modules[1].active = false;
  expect(() => allocateApmQuestions(apmProfile, state)).toThrow(/ai-project-management.*K15.*S1.*B5/);
});

it('uses confirmed APM identity and never applies its mapping to PCP/MM/ME', () => {
  expect(isApmRadar(confirmedApm)).toBe(true);
  for (const selectedProgramme of ['PCP_L6', 'MM_L6', 'ME_L4'] as const) expect(isApmRadar({ ...confirmedApm, selectedProgramme })).toBe(false);
  expect(isApmRadar({ ...confirmedApm, selectionStatus: 'SUBMITTED_PENDING_CONFIRMATION' })).toBe(false);
  expect(apmRadarProfileProgramme(confirmedApm)).toBe('Associate Project Manager Level 4');
  const state = structuredClone(confirmedApm); state.configuration!.programmes.find((item) => item.id === 'APM_L4')!.catalogueId = 'PROG-APM-L4';
  expect(apmRadarProfileProgramme(state)).toBe('PROG-APM-L4');
  expect(resolveApmRadar(null, null).status).toBe('loading');
  expect(resolveApmRadar(apmProfile, apmModules(), 'Modules unavailable')).toMatchObject({ status: 'error', message: 'Modules unavailable', questions: [] });
  expect(resolveApmRadar(apmProfile, { ...apmModules(), selectedProgramme: 'PCP_L6' }).status).toBe('error');
});
