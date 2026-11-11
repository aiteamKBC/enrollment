import data from '../seed.json';
import type { DocumentKey, DocumentTemplate, IssuedTemplate, PreviewResponse, TemplateIndex } from '../types';

export const keys = Object.keys(data.templates) as DocumentKey[];
export const contentFor = (key: DocumentKey) => structuredClone(data.templates[key]) as DocumentTemplate;
export function versionFor(key: DocumentKey, edited = false): IssuedTemplate {
  const content = contentFor(key);
  if (edited) content.blocks.push({ id: 'synthetic-notice', type: 'paragraph', text: 'Approved changed wording for {{learner.fullName}}.' });
  return { id: `${key}-version-1`, number: 1, content, contentHash: 'synthetic', publishedAt: '2030-01-01T10:00:00Z',
    publishedBy: 'Synthetic administrator', approvalNote: '', legacyEquivalent: !edited,
    fields: { 'learner.fullName': 'SYNTHETIC Learner', 'programme.name': 'Marketing Executive Level 4' } };
}
export function payloadFor(key: DocumentKey, edited = false): PreviewResponse {
  const template = versionFor(key, edited);
  const signatures = Object.fromEntries(['learner', 'apprentice', 'provider', 'admin', 'employer'].map((role) => [role,
    { signed: false, signature: '', name: '', signedAt: null, position: '', required: true }]));
  const sections = template.content.blocks.filter((b) => b.source?.startsWith('review:')).map((b) => b.source!.slice(7));
  return { template, previewToken: 'synthetic-preview-token', missingFields: [], sample: true,
    notice: 'SAMPLE DATA — this preview does not issue a document or request signatures.',
    data: { template, signatures, marks: {}, particulars: { apprenticeName: 'SYNTHETIC Learner', mainProvider: 'Original Provider' },
      learnerDetails: { givenNames: 'SYNTHETIC', familyName: 'Learner' }, answers: {},
      programme: { apprenticeName: 'SYNTHETIC Learner', programme: 'Marketing Executive Level 4' }, employment: {},
      learningPlan: [], otjh: {}, epa: {}, contacts: {}, delivery: {}, costs: { items: [] },
      eventKey: 'synthetic-review', reviewLabel: template.content.title, reviewType: template.content.reviewType,
      sections, sectionStatus: {}, learnerInformation: { name: 'SYNTHETIC Learner', programmeName: 'Marketing Executive Level 4',
        programmeStartDate: '2030-01-01', plannedEndDate: '2032-01-01', programmeStatus: 'Onboarding', employer: 'SYNTHETIC Employer', manager: '', mentor: '' },
      completed: false, completedAt: null, skillsRadar: { items: [], answered: 0, total: 0 }, plrRecords: [], uln: '' } };
}
export const indexFor = (): TemplateIndex => ({ setupRequired: false,
  programmes: { PCP_L6: 'Project Controls Professional Level 6', MM_L6: 'Marketing Manager Level 6', ME_L4: 'Marketing Executive Level 4', APM_L4: 'Associate Project Manager Level 4' },
  results: keys.map((key) => { const publishedVersion = versionFor(key); return { templateId: key, documentKey: key,
    title: publishedVersion.content.title, category: publishedVersion.content.category, programmes: [], publishedVersion, draft: null, updatedAt: null }; }) });
