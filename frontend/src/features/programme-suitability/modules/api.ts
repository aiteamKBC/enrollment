import { request } from '../api';
import type { LearnerKind } from '@/api/extendedIlr';
import type { ModulesConfiguration, ModulesState, ProgrammeModuleSelection, ModuleSelectionCommit } from './configuration';

const base = '/enrolment_api/programme-suitability';
export const getModules = (kind: LearnerKind, id: string) => request<ModulesState>(`${base}/${kind}/${id}/modules/`);
export const saveModuleSelection = (kind: LearnerKind, id: string, selection: ProgrammeModuleSelection, expectedRevision: number) =>
  request<ModuleSelectionCommit>(`${base}/${kind}/${id}/modules/`, { ...selection, expectedRevision });
export const getModuleDefaults = () => request<{ configuration: ModulesConfiguration }>(`${base}/modules/defaults/`);
export const getModulesDraft = () => request<{ configuration: ModulesConfiguration | null; baseVersion: number | null }>(`${base}/modules/draft/`);
export const saveModulesDraft = (configuration: ModulesConfiguration, baseVersion: number | null) =>
  request<{ configuration: ModulesConfiguration; baseVersion: number | null }>(`${base}/modules/draft/`, { configuration, baseVersion });
