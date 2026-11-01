import data from './modules-defaults.json';
import type { ModulesConfiguration, ProgrammeModules } from '../modules/configuration';
import type { ProgrammeId } from '../types';

export const modulesConfiguration = data as ModulesConfiguration;
export const PROGRAMME_MODULES = Object.fromEntries(modulesConfiguration.programmes.map((p) => [p.id, p])) as Record<ProgrammeId, ProgrammeModules>;
export const PCP_PATHWAYS = PROGRAMME_MODULES.PCP_L6.pathways;
