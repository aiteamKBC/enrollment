import type { Ksb } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import type { ModulesState } from '../modules/configuration';
import { modulesConfiguration } from './modulesFixtures';
import { configuration, fresh, result } from './fixtures';
import source from '../skills-radar/marketing_executive_l4_ksb_full_mapping.json';

export const confirmedMe: AssessmentState = { ...fresh(), configuration, selectedProgramme: 'ME_L4', pathway: 'marketing', result: { ...result, outcome: 'RECOMMENDED_ME_L4', recommendedProgramme: 'ME_L4' },
  completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', confirmedAt: 'confirmed', selectionConfirmedAt: 'confirmed' };
export const meProfile: Ksb[] = source.ksbs.map((item) => ({ id: `st0596-v1-0-${item.code}`, codes: [item.code],
  title: item.code, kind: item.category as Ksb['kind'], theme: item.category }));
export const meModules = (): ModulesState => ({ configuration: structuredClone(modulesConfiguration), configurationVersion: 11,
  selectedProgramme: 'ME_L4', savedSelection: null, selectionComplete: false, selectionRevision: 0 });
