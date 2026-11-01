import { modulesConfiguration } from './modulesFixtures';
vi.mock('../modules/api', () => ({ getModules: vi.fn(), saveModuleSelection: vi.fn(async (_kind, _id, selection) => ({ selection, selectionComplete: true })) }));
import { getModules } from '../modules/api';
import rules from './pcp-rules-defaults.json';
import type { ModulesConfiguration } from '../modules/configuration';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import { beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardProvider } from '@/pages/users/wizard/WizardContext';
import type { EnrolmentBoard, WizardDraft } from '@/pages/users/types';
import ProgrammeSuitabilityStep from '../ProgrammeSuitabilityStep';
import { fresh, result, configuration } from './fixtures';

const mocks = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn() }));
vi.mock('@/api/extendedIlr', () => ({ fetchExtendedIlr: mocks.read, saveExtendedIlr: mocks.write, peekExtendedIlr: () => undefined }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: vi.fn(async () => ({ results: [] })), peekKsbProfile: () => undefined }));
vi.mock('@/api/wizardInclusion', () => ({ fetchInclusionStatus: vi.fn(async () => ({ applicable: false, completed: false })) }));
vi.mock('@/api/wizardLayout', () => ({ fetchWizardLayout: async () => ({ layout: { steps: [], programmeSuitability: configuration }, version: 3 }), peekWizardLayout: () => undefined }));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/hooks/useToast.helpers', () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }));
vi.mock('../api', () => ({ getAssessment: async () => ({ ...fresh(), pathway: 'project-controls',
  result: { ...result, outcome: 'RECOMMENDED_PCP_L6', recommendedProgramme: 'PCP_L6' }, selectedProgramme: 'PCP_L6', completionStatus: 'SUBMITTED',
  confirmedAt: '2026-11-01T12:00:00Z', selectionStatus: 'SELECTION_CONFIRMED', selectionConfirmedAt: '2026-11-01T12:01:00Z' }),
  peekAssessment: () => undefined, primeAssessment: vi.fn(), prefetchAssessmentSecurity: async () => ({ csrfToken: 'synthetic' }) }));

const board = { user: { id: '41', name: 'Test Learner' }, contact: { email: 'learner@example.test', dob: '', phone: '' },
  programme: { name: 'Project Controls Professional Level 6', onboardingStatus: 'In progress' } } as EnrolmentBoard;
let stored: Partial<WizardDraft>;
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; });
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getModules).mockResolvedValue({ configuration: modulesConfiguration, configurationVersion: 3, selectedProgramme: 'PCP_L6' });
  stored = { cvJob: { experienceText: 'Saved CV text', highestQualification: 'Saved qualification' } as WizardDraft['cvJob'] };
  mocks.read.mockImplementation(async () => ({ answers: null, draft: structuredClone(stored), meta: { updatedAt: 'saved' } }));
  mocks.write.mockImplementation(async (_kind, _id, _answers, draft) => {
    stored = structuredClone(draft); return { draft: stored, meta: { updatedAt: '2026-11-01T12:00:00Z' } };
  });
});
const page = () => <WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /></WizardProvider>;

it('persists PCP choices through the existing wizard API and restores them on reopening without losing CV data', async () => {
  const first = render(page());
  await userEvent.click(await screen.findByRole('radio', { name: 'Chartered Pathway' }));
  const card = screen.getByRole('region', { name: 'Chartered Pathway' });
  await userEvent.click(within(card).getByRole('checkbox', { name: 'Stakeholder Engagement and Communication' }));
  await screen.findByText('Module choices saved.');
  expect(stored.programmeModules).toEqual({ programme: 'PCP_L6', configurationVersion: 3, pathway: 'chartered', modulesByPathway: { chartered: ['pmo-stakeholders'] } });
  expect(stored.cvJob?.experienceText).toBe('Saved CV text');
  expect(stored.cvJob?.highestQualification).toBe('Saved qualification');
  expect(stored.programmeSuitability).toBeUndefined();
  first.unmount(); const writes = mocks.write.mock.calls.length;
  render(page());
  await waitFor(() => expect(screen.getByRole('radio', { name: 'Chartered Pathway' })).toBeChecked());
  expect(within(screen.getByRole('region', { name: 'Chartered Pathway' })).getByRole('checkbox', { name: 'Stakeholder Engagement and Communication' })).toBeChecked();
  expect(screen.getAllByRole('region', { name: 'Programme Modules' })).toHaveLength(1);
  expect(mocks.write).toHaveBeenCalledTimes(writes);
  await userEvent.click(screen.getByRole('button', { name: 'Preview Assessment' }));
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(screen.getAllByRole('region', { name: 'Programme Modules' })).toHaveLength(1);
});

it('serialises overlapping autosaves so a slow older response cannot replace the latest module choices', async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  mocks.write.mockImplementationOnce(async (_kind, _id, _answers, draft) => {
    await gate; stored = structuredClone(draft); return { draft: stored, meta: { updatedAt: 'first-save' } };
  });
  render(page());
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  await waitFor(() => expect(mocks.write).toHaveBeenCalledTimes(1));
  const card = screen.getByRole('region', { name: 'Operational Pathway' });
  await userEvent.click(within(card).getByRole('checkbox', { name: 'Project Management Professional (PMP)' }));
  await userEvent.click(within(card).getByRole('checkbox', { name: 'Risk Management (APM)' }));
  await new Promise((resolve) => setTimeout(resolve, 250));
  expect(mocks.write).toHaveBeenCalledTimes(1);
  release();
  await screen.findByText('Module choices saved.');
  expect(stored.programmeModules?.modulesByPathway.operational).toEqual(['pmp', 'risk-apm']);
  expect(stored.cvJob?.experienceText).toBe('Saved CV text');
});


function ModuleCompletion() {
  const { draft } = useWizard();
  return <output data-testid="module-completion">{draft.programmeModulesCompletion?.complete ? 'complete' : 'incomplete'}</output>;
}
it('marks Step 6 complete only after the valid new PCP selection is saved and restores completion on reopening', async () => {
  vi.mocked(getModules).mockResolvedValue({ configuration: rules as ModulesConfiguration, configurationVersion: 3, selectedProgramme: 'PCP_L6' });
  const first = render(<WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  await screen.findByText('Module choices saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  const card = within(screen.getByRole('region', { name: 'Operational Pathway' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'Project Planning and Control (APMG Qualification)' }));
  await screen.findByText('Module choices saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  mocks.write.mockImplementationOnce(async (_kind, _id, _answers, draft) => {
    await gate; stored = structuredClone(draft); return { draft: stored, meta: { updatedAt: 'final-save' } };
  });
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  release(); await screen.findByText('Module choices saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  expect(stored.programmeModules?.modulesByPathway.operational).toHaveLength(5);
  expect(stored.programmeModulesCompletion).toBeUndefined();
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  await screen.findByText('Module choices saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  first.unmount();
  render(<WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  await waitFor(() => expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/));
});

it('lets a learner review refreshed rules and save existing options with the authoritative mandatory IDs', async () => {
  vi.mocked(getModules).mockResolvedValue({ configuration: rules as ModulesConfiguration, configurationVersion: 3, selectedProgramme: 'PCP_L6' });
  stored.programmeModules = { programme: 'PCP_L6', pathway: 'operational', configurationVersion: 2,
    modulesByPathway: { operational: ['planning-control', 'pmi-sp'] } };
  render(<WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  await userEvent.click(await screen.findByRole('button', { name: 'Save reviewed module selection' }));
  await screen.findByText('Module choices saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  expect(stored.programmeModules?.configurationVersion).toBe(3);
  expect(new Set(stored.programmeModules?.modulesByPathway.operational)).toEqual(new Set(['planning-control', 'pmi-sp', 'pmp', 'ai-project-control', 'risk-apm']));
});
