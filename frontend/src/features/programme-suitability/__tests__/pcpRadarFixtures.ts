import type { Ksb } from '@/pages/users/types';
import type { ModulesConfiguration, ModulesState } from '../modules/configuration';
import { mandatoryIds } from '../modules/configuration';
import { configuration, fresh, result } from './fixtures';
import moduleData from './pcp-rules-defaults.json';
import source from '../skills-radar/pcp_l6_ksb_full_descriptions_and_mapping.json';

export const pcpConfiguration = moduleData as ModulesConfiguration;
export const pcpProfile: Ksb[] = source.ksbs.map((item) => ({ id: `st0845-v1-1-${item.code}`, codes: [item.code],
  title: item.code, kind: item.type as Ksb['kind'], theme: item.type }));
export const confirmedPcp = { ...fresh(), configuration, selectedProgramme: 'PCP_L6' as const,
  pathway: 'project-controls' as const, result: { ...result, outcome: 'RECOMMENDED_PCP_L6', recommendedProgramme: 'PCP_L6' as const },
  completionStatus: 'SUBMITTED' as const, selectionStatus: 'SELECTION_CONFIRMED' as const, confirmedAt: 'confirmed', selectionConfirmedAt: 'confirmed' };

export function pcpModules(pathway = 'operational', optional = ['planning-control', 'pmi-sp']): ModulesState {
  const path = pcpConfiguration.programmes[3].pathways.find((p) => p.id === pathway)!;
  return { configuration: structuredClone(pcpConfiguration), configurationVersion: 11, selectedProgramme: 'PCP_L6', selectionComplete: true,
    selectionRevision: 1, savedSelection: { programme: 'PCP_L6', pathway, configurationVersion: 11,
      modulesByPathway: { [pathway]: [...mandatoryIds(path), ...optional] } } };
}
