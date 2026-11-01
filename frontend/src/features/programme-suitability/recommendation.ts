import type { AssessmentState, Programme } from './types';

export function recommendedProgramme(state: AssessmentState): Programme | undefined {
  const id = state.result?.recommendedProgramme;
  if (!id || state.result?.outcome !== `RECOMMENDED_${id}`) return undefined;
  return state.configuration?.programmes.find((p) => p.id === id && p.active && p.pathway === state.pathway);
}

export function alternativeProgramme(state: AssessmentState): Programme | undefined {
  const recommended = recommendedProgramme(state);
  if (!recommended) return undefined;
  const level = recommended.level === 6 ? 4 : 6;
  return state.configuration?.programmes.find((p) => p.active && p.pathway === state.pathway && p.level === level
    && (level === 6 || !state.allowedSelections || state.allowedSelections.includes(p.id)));
}
