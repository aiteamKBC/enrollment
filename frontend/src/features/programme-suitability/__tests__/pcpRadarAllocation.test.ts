import { expect, it } from 'vitest';
import { allocatePcpQuestions, bindPcpQuestions, radarProfileProgramme, resolvePcpRadar, validatePcpCatalogue } from '../skills-radar/allocation';
import source from '../skills-radar/pcp_l6_ksb_full_descriptions_and_mapping.json';
import { confirmedPcp, pcpModules, pcpProfile } from './pcpRadarFixtures';

const combinations = [
  ['operational', ['planning-control', 'earned-value'], [16, 12, 9, 20, 14]],
  ['operational', ['planning-control', 'pmi-sp'], [16, 12, 9, 23, 11]],
  ['operational', ['earned-value', 'pmi-sp'], [16, 12, 9, 20, 14]],
  ['strategic', ['pmi-pmo', 'portfolio'], [16, 12, 9, 20, 14]],
  ['strategic', ['pmi-pmo', 'successful-programmes'], [16, 12, 9, 23, 11]],
  ['strategic', ['portfolio', 'successful-programmes'], [16, 12, 9, 20, 14]],
  ['chartered', ['portfolio'], [21, 7, 11, 9, 12, 11]],
  ['chartered', ['earned-value'], [21, 7, 11, 9, 12, 11]],
] as const;

it('loads exactly 31 Knowledge, 29 Skill and 11 Behaviour full descriptions, including the long source text', () => {
  const data = validatePcpCatalogue();
  expect(data).toHaveLength(71);
  expect(['Knowledge', 'Skill', 'Behaviour'].map((type) => data.filter((k) => k.type === type).length)).toEqual([31, 29, 11]);
  expect(data[0].full_description).toBe('Organisational and business strategies: and how these impact on the strategy for project control and its execution');
  expect(data.find((k) => k.code === 'K6')!.full_description).toHaveLength(400);
  expect(data.find((k) => k.code === 'S1')!.full_description).toHaveLength(272);
  expect(data.find((k) => k.code === 'S29')!.full_description).toHaveLength(122);
  expect(data.find((k) => k.code === 'B11')!.full_description).toBe('Adaptable: adapts to evolving circumstances.');
  const bound = bindPcpQuestions(pcpProfile);
  expect(bound.map((k) => k.id)).toEqual(pcpProfile.map((k) => k.id));
  expect(bound.map((k) => k.title)).toEqual(source.ksbs.map((k) => k.full_description));
});

it.each(combinations)('allocates all 71 descriptions once for %s with %s', (path, optionals, counts) => {
  const state = pcpModules(path, [...optionals]);
  const before = JSON.stringify([source, state, pcpProfile]);
  const groups = allocatePcpQuestions(pcpProfile, state);
  expect(groups.map((module) => module.questions.length)).toEqual(counts);
  const assigned = groups.flatMap((module) => module.questions);
  expect(assigned).toHaveLength(71);
  expect(new Set(assigned.map((k) => k.id)).size).toBe(71);
  expect(new Set(assigned.map((k) => k.codes[0])).size).toBe(71);
  expect(groups.some((module) => module.id === 'certified-pmo')).toBe(false);
  expect(groups.map((module) => module.id).sort()).toEqual([...state.savedSelection!.modulesByPathway[path]].sort());
  for (const question of assigned) expect(question.title).toBe(source.ksbs.find((k) => k.code === question.codes[0])!.full_description);
  expect(JSON.stringify([source, state, pcpProfile])).toBe(before);
  expect(allocatePcpQuestions(structuredClone(pcpProfile), JSON.parse(JSON.stringify(state)))).toEqual(groups);
});

it('keeps redistribution stable when selected ID arrays, source rows and module display order change', () => {
  const state = pcpModules();
  const ownership = (groups: ReturnType<typeof allocatePcpQuestions>) => Object.fromEntries(groups.flatMap((g) => g.questions.map((k) => [k.codes[0], g.id])));
  const initial = ownership(allocatePcpQuestions(pcpProfile, state));
  state.savedSelection!.modulesByPathway.operational.reverse();
  state.configuration.programmes[3].pathways[0].modules.forEach((m, i) => { m.order = 50 - i; m.title = `Renamed ${m.id}`; });
  expect(ownership(allocatePcpQuestions([...pcpProfile].reverse(), state, { ...source, ksbs: [...source.ksbs].reverse() }))).toEqual(initial);
});

it.each(['', 'K6', 'Knowledge 6', 'KSB Question'])('reports the affected code instead of showing invalid description %s', (text) => {
  const broken = structuredClone(source);
  broken.ksbs.find((k) => k.code === 'K6')!.full_description = text;
  expect(() => validatePcpCatalogue(broken)).toThrow(/K6/);
});

it('rejects missing descriptions, duplicate profile IDs/codes and unsupported selected module IDs', () => {
  expect(() => validatePcpCatalogue({ ...source, ksbs: source.ksbs.slice(1) })).toThrow(/K1/);
  expect(() => bindPcpQuestions(pcpProfile.slice(1))).toThrow(/K1/);
  expect(() => bindPcpQuestions([...pcpProfile, pcpProfile[0]])).toThrow(/K1/);
  const state = pcpModules();
  state.configuration.programmes[3].pathways[0].modules.find((m) => m.id === 'pmi-sp')!.id = 'custom-schedule';
  state.savedSelection!.modulesByPathway.operational = state.savedSelection!.modulesByPathway.operational.map((id) => id === 'pmi-sp' ? 'custom-schedule' : id);
  expect(() => allocatePcpQuestions(pcpProfile, state)).toThrow(/custom-schedule/);
});

it('requires saved complete version-matched modules and uses the confirmed programme instead of a stale board', () => {
  expect(resolvePcpRadar(null, null).status).toBe('loading');
  expect(resolvePcpRadar(pcpProfile, { ...pcpModules(), selectionComplete: false }).status).toBe('waiting');
  const state = pcpModules(); state.savedSelection!.configurationVersion = 9;
  expect(resolvePcpRadar(pcpProfile, state).status).toBe('error');
  expect(radarProfileProgramme('Old programme', confirmedPcp)).toBe('Project Controls Professional Level 6');
  expect(radarProfileProgramme('Marketing Executive Level 4')).toBe('Marketing Executive Level 4');
});
