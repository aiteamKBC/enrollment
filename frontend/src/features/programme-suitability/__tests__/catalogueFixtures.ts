import raw from '../../../../../backend/enrolment_api/skills_radar/defaults.json';
import type { ProgrammeId } from '../types';
import type { CatalogueDraft, CatalogueIndex, Comparison, RadarCatalogues, RadarCatalogue } from '../skills-radar/catalogue/types';
import { pcpConfiguration } from './pcpRadarFixtures';

export function catalogues(): RadarCatalogues {
  return Object.fromEntries(Object.entries(raw).map(([pid, value]) => [pid, { ...structuredClone(value), version: `${pid}-initial-version`, publishedAt: null }])) as RadarCatalogues;
}
export function catalogueIndex(): CatalogueIndex { return { catalogues: catalogues(), modules: structuredClone(pcpConfiguration), baseVersion: 11 }; }
export function addedCatalogue(pid: ProgrammeId = 'ME_L4'): RadarCatalogue {
  const value = catalogues()[pid];
  value.records.push({ ...structuredClone(value.records[0]), id: `radar:${pid}:K99`, code: 'K99', description: 'A new full description, with commas and a learner’s apostrophe.\nSecond line.' });
  value.records.sort((a, b) => 'KSB'.indexOf(a.code[0]) - 'KSB'.indexOf(b.code[0]) || Number(a.code.slice(1)) - Number(b.code.slice(1)));
  value.version = `${pid}-updated-version`;
  return value;
}
export function comparison(before: RadarCatalogue, after: RadarCatalogue): Comparison {
  const added = after.records.filter((r) => !before.records.some((old) => old.code === r.code));
  const changes = [...before.records.map((record) => ({ code: record.code, changes: ['unchanged'] as const, before: record, after: record })),
    ...added.map((r) => ({ code: r.code, changes: ['added'] as const, before: null, after: r }))];
  return { currentTotal: before.records.length, newTotal: after.records.length, unchanged: before.records.length, added: added.length, removed: 0,
    descriptions: 0, categories: 0, mappings: 0, removedCodes: [], categoryCodes: [], largeRemoval: false, changes: changes as unknown as Comparison['changes'] };
}
export function draftFor(pid: ProgrammeId = 'ME_L4'): CatalogueDraft {
  const all = catalogues(); const next = addedCatalogue(pid);
  return { skillsRadarCatalogues: { ...all, [pid]: next }, skillsRadarImportApprovals: { [pid]: { againstVersion: all[pid].version, completeSnapshot: true, removedCodes: [], categoryCodes: [] } } };
}
