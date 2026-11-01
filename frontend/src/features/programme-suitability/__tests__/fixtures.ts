import type { AssessmentState, Configuration, Pathway, Result } from '../types';

export const configuration: Configuration = {
  schemaVersion: 1, enabled: true, introduction: 'Choose responsibilities you actually perform.',
  programmes: [
    { id: 'ME_L4', title: 'Marketing Executive', level: 4, pathway: 'marketing' },
    { id: 'MM_L6', title: 'Marketing Manager', level: 6, pathway: 'marketing' },
    { id: 'APM_L4', title: 'Associate Project Manager', level: 4, pathway: 'project-controls' },
    { id: 'PCP_L6', title: 'Project Controls Professional', level: 6, pathway: 'project-controls' },
  ].map((p, order) => ({ ...p, summary: 'Editable suitability guidance', description: 'Programme description',
    responsibilities: ['Relevant responsibility'], active: true, order, catalogueId: null })) as Configuration['programmes'],
  questions: (['marketing', 'project-controls'] as Pathway[]).map((pathway, order) => ({
    id: pathway, slug: pathway, title: `${pathway} responsibilities`, description: `Which ${pathway} tasks do you perform?`, pathway,
    weight: 10, order, active: true, required: true, multiple: true, critical: true,
    criticalProgramme: pathway === 'marketing' ? 'MM_L6' : 'PCP_L6',
    answers: ['A', 'B', 'C', 'D'].map((letter, index) => ({ id: `${pathway}_${letter}`, text: `${pathway} answer ${letter}`,
      active: true, order: index, affinities: { [pathway === 'marketing' ? index < 2 ? 'ME_L4' : 'MM_L6' : index < 2 ? 'APM_L4' : 'PCP_L6']: [0.4, 0.8, 0.6, 1][index] } })),
  })),
  rules: { minimumScore: 60, minimumDifference: 15, minimumCoverage: 80, minimumCriticalIndicators: 2,
    criticalStrength: 0.6, mixedQuestionThreshold: 50, mixedAffinityThreshold: 0.6 },
};
export const result: Result = { outcome: 'RECOMMENDED_MM_L6', recommendedProgramme: 'MM_L6', scores: { ME_L4: 0, MM_L6: 100 } as Result['scores'],
  coverage: 100, evidenceCoverage: 100, availableWeight: 10, criticalIndicators: { MM_L6: ['marketing'] }, reasons: ['CLEAR_RESPONSIBILITY_MATCH'],
  missingRequired: [], strongestEvidence: [], breakdown: [] };
export const fresh = (): AssessmentState => ({ available: true, configuration: structuredClone(configuration), configurationVersion: 3,
  pathway: '', responses: {}, result: null, revision: 0, selectedProgramme: '', reviewStatus: 'PRELIMINARY_RECOMMENDATION' });

