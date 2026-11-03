import { expect, it } from 'vitest';
import { resolveCatalogueRadar } from '../skills-radar/catalogue/runtime';
import type { ProgrammeId } from '../types';
import { addedCatalogue, catalogues } from './catalogueFixtures';
import { pcpModules } from './pcpRadarFixtures';

it.each(['MM_L6', 'ME_L4', 'APM_L4'] as ProgrammeId[])('uses imported dynamic ownership and counts for %s, preserving IDs and exact descriptions', (pid) => {
  const catalogue = addedCatalogue(pid);
  const state = { ...pcpModules(), selectedProgramme: pid };
  const value = resolveCatalogueRadar(catalogue, state);
  expect(value.status).toBe('ready'); expect(value.questions).toHaveLength(catalogue.records.length);
  expect(new Set(value.questions.map((q) => q.id)).size).toBe(catalogue.records.length);
  for (const record of catalogue.records) {
    const owner = value.modules.find((m) => m.questions.some((q) => q.id === record.id))!;
    expect(owner.id).toBe(record.moduleKey);
    expect(owner.questions.find((q) => q.id === record.id)).toMatchObject({ title: record.description, kind: record.kind });
  }
  const first = catalogue.records[0]; const otherModule = value.modules.find((m) => m.id !== first.moduleKey)!;
  first.moduleKey = otherModule.id; first.kind = 'Behaviour'; first.description = 'Approved changed text';
  state.configuration.programmes.find((p) => p.id === pid)!.modules.find((m) => m.id === otherModule.id)!.title = 'Renamed module';
  const changed = resolveCatalogueRadar(catalogue, state);
  expect(changed.modules.find((m) => m.title === 'Renamed module')!.questions.find((q) => q.id === first.id)).toMatchObject({ title: first.description, kind: 'Behaviour' });
});

it.each([
  ['operational', ['planning-control', 'earned-value']], ['operational', ['planning-control', 'pmi-sp']], ['operational', ['earned-value', 'pmi-sp']],
  ['strategic', ['pmi-pmo', 'portfolio']], ['strategic', ['pmi-pmo', 'successful-programmes']], ['strategic', ['portfolio', 'successful-programmes']],
  ['chartered', ['portfolio']], ['chartered', ['earned-value']],
] as [string, string[]][])('reuses PCP redistribution for imported questions in %s %j without duplication or parent ownership', (path, choices) => {
  const catalogue = addedCatalogue('PCP_L6');
  const extra = catalogue.records.find((r) => r.code === 'K99')!;
  extra.owners = { operational: 'FINANCE', strategic: 'FINANCE', chartered: 'FINANCE' };
  const state = pcpModules(path, choices);
  const resolved = resolveCatalogueRadar(catalogue, state);
  expect(resolved.status).toBe('ready'); expect(resolved.questions).toHaveLength(72);
  expect(new Set(resolved.questions.map((q) => q.id)).size).toBe(72);
  expect(resolved.modules.some((m) => m.id === 'certified-pmo')).toBe(false);
  expect(resolved.modules.filter((m) => m.questions.some((q) => q.id === extra.id))).toHaveLength(1);
  if (path === 'chartered') expect(resolved.modules.find((m) => m.questions.some((q) => q.id === extra.id))!.id).toBe(choices[0]);
  const original = resolveCatalogueRadar(catalogues().PCP_L6, state);
  for (const mandatory of state.configuration.programmes.find((p) => p.id === 'PCP_L6')!.pathways.find((p) => p.id === path)!.modules.filter((m) => m.isMandatory && !m.items)) {
    expect(resolved.modules.find((m) => m.id === mandatory.id)?.questions.filter((q) => q.id !== extra.id))
      .toEqual(original.modules.find((m) => m.id === mandatory.id)?.questions);
  }
});

it('removes only new-version questions and fails visibly for unavailable module keys', () => {
  const old = catalogues().ME_L4; const revised = structuredClone(old); revised.records.pop();
  const state = { ...pcpModules(), selectedProgramme: 'ME_L4' as const };
  expect(resolveCatalogueRadar(revised, state).questions).toHaveLength(32);
  expect(resolveCatalogueRadar(old, state).questions).toHaveLength(33);
  revised.records[0].moduleKey = 'missing';
  expect(resolveCatalogueRadar(revised, state)).toMatchObject({ status: 'error', questions: [] });
});
