import type { Ksb } from '@/pages/users/types';
import { isModuleGroup, ordered, type ModulesState, type ProgrammeModule } from '../../modules/configuration';
import { allocatePcpQuestions, type PcpRadar } from '../allocation';
import type { RadarCatalogue } from './types';

export const catalogueQuestions = (catalogue: RadarCatalogue): Ksb[] => catalogue.records.map((r) => ({
  id: r.id, codes: [r.code], title: r.description, kind: r.kind, theme: r.kind,
}));

function leaves(items: ProgrammeModule[]): ProgrammeModule[] {
  return ordered(items).filter((m) => m.active).flatMap((m) => isModuleGroup(m) ? leaves(m.items ?? []) : [m]);
}

/** Catalogue size and ownership come from the applicable published snapshot. */
export function resolveCatalogueRadar(catalogue: RadarCatalogue | null, state: ModulesState | null | undefined, error = ''): PcpRadar {
  const empty = { modules: [], questions: [] };
  if (error) return { ...empty, status: 'error', message: error };
  if (!catalogue || !state) return { ...empty, status: 'loading', message: 'Loading saved Skills Radar catalogue and modules…' };
  try {
    const questions = catalogueQuestions(catalogue);
    if (!questions.length || new Set(questions.map((q) => q.id)).size !== questions.length) throw new Error('The Skills Radar catalogue is empty or contains duplicate identifiers.');
    if (catalogue.programmeId === 'PCP_L6') {
      if (!state.selectionComplete || !state.savedSelection) return { ...empty, status: 'waiting', message: 'Complete and save your PCP module selection in Programme Suitability & Modules before answering Skills Radar.' };
      const modules = allocatePcpQuestions(questions, state, { ksbs: catalogue.records.map((r) => ({
        code: r.code, type: r.kind, full_description: r.description, original_owner_group_by_pathway: r.owners,
      })) }, true);
      return { status: 'ready', message: '', modules, questions: modules.flatMap((m) => m.questions) };
    }
    const definition = state.configuration.programmes.find((p) => p.id === catalogue.programmeId);
    if (!definition) throw new Error('Published programme modules are unavailable.');
    const modules = leaves(definition.modules).map((m) => ({ id: m.id, title: m.title, sessions: m.sessions, questions: [] as Ksb[] }));
    catalogue.records.forEach((r, index) => {
      const module = modules.find((m) => m.id === r.moduleKey);
      if (!module) throw new Error(`KSB ${r.code} references an unavailable module. Please contact admissions.`);
      module.questions.push(questions[index]);
    });
    return { status: 'ready', message: '', modules, questions: modules.flatMap((m) => m.questions) };
  } catch (failure) {
    return { ...empty, status: 'error', message: failure instanceof Error ? failure.message : 'Invalid Skills Radar catalogue.' };
  }
}
