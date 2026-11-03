import type { Ksb } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import type { ModulesState } from '../modules/configuration';
import { modulesConfiguration } from './modulesFixtures';
import { configuration, fresh, result } from './fixtures';
import source from '../skills-radar/marketing_manager_l6_ksb_full_mapping.json';

export const confirmedMm: AssessmentState = { ...fresh(), configuration, selectedProgramme: 'MM_L6', pathway: 'marketing', result,
  completionStatus: 'SUBMITTED', selectionStatus: 'SELECTION_CONFIRMED', confirmedAt: 'confirmed', selectionConfirmedAt: 'confirmed' };
export const mmProfile: Ksb[] = source.ksbs.map((item) => ({ id: `st0612-v1-0-${item.code}`, codes: [item.code],
  title: item.code, kind: item.category as Ksb['kind'], theme: item.category }));
export const mmModules = (): ModulesState => ({ configuration: structuredClone(modulesConfiguration), configurationVersion: 11,
  selectedProgramme: 'MM_L6', savedSelection: null, selectionComplete: false, selectionRevision: 0 });
