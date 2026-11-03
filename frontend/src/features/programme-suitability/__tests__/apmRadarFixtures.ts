import type { Ksb } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import type { ModulesState } from '../modules/configuration';
import { modulesConfiguration } from './modulesFixtures';
import { configuration, fresh, result } from './fixtures';
import source from '../skills-radar/associate_project_manager_l4_ksb_full_mapping.json';

export const confirmedApm: AssessmentState = { ...fresh(), configuration, selectedProgramme: 'APM_L4', pathway: 'project-controls', result: { ...result, outcome: 'RECOMMENDED_APM_L4', recommendedProgramme: 'APM_L4' },
  completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', confirmedAt: 'confirmed', selectionConfirmedAt: 'confirmed' };
export const apmProfile: Ksb[] = source.ksbs.map((item) => ({ id: `st0310-v1-5-${item.code}`, codes: [item.code],
  title: item.code, kind: item.type as Ksb['kind'], theme: item.type }));
export const apmModules = (): ModulesState => ({ configuration: structuredClone(modulesConfiguration), configurationVersion: 11,
  selectedProgramme: 'APM_L4', savedSelection: null, selectionComplete: false, selectionRevision: 0 });
