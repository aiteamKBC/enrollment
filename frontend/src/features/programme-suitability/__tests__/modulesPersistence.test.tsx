import { modulesConfiguration } from './modulesFixtures';
vi.mock('../modules/api', () => ({ getModules: vi.fn(), saveModuleSelection: vi.fn() }));
import { getModules, saveModuleSelection } from '../modules/api';
import rules from './pcp-rules-defaults.json';
import { moduleSelectionComplete, type ModulesConfiguration, type ProgrammeModuleSelection } from '../modules/configuration';
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
let serverSelection: ProgrammeModuleSelection | undefined;
let revision: number;
let publishedModules: ModulesConfiguration;
async function persist(_kind: string, _id: string, selection: ProgrammeModuleSelection, expected: number) {
  if (expected !== revision) throw Object.assign(new Error('Module choices changed in another session. Review your choices and retry.'), { status: 409 });
  serverSelection = structuredClone(selection); revision += 1;
  return { selection: serverSelection, selectionRevision: revision, selectionComplete: moduleSelectionComplete(publishedModules, serverSelection, 3) };
}
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; });
beforeEach(() => {
  vi.clearAllMocks(); revision = 0; serverSelection = undefined; publishedModules = modulesConfiguration;
  vi.mocked(getModules).mockImplementation(async () => ({ configuration: publishedModules, configurationVersion: 3, selectedProgramme: 'PCP_L6',
    savedSelection: serverSelection, selectionRevision: revision, selectionComplete: moduleSelectionComplete(publishedModules, serverSelection, 3) }));
  vi.mocked(saveModuleSelection).mockImplementation(persist);
  stored = { cvJob: { experienceText: 'Saved CV text', highestQualification: 'Saved qualification' } as WizardDraft['cvJob'] };
  mocks.read.mockImplementation(async () => ({ answers: null, draft: structuredClone(stored), meta: { updatedAt: 'saved' } }));
  mocks.write.mockImplementation(async (_kind, _id, _answers, draft) => {
    stored = structuredClone(draft); return { draft: stored, meta: { updatedAt: '2026-11-01T12:00:00Z' } };
  });
});
const page = () => <WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /></WizardProvider>;

it('persists PCP choices through only the modules API and restores them on reopening without losing CV data', async () => {
  const first = render(page());
  await userEvent.click(await screen.findByRole('radio', { name: 'Chartered Pathway' }));
  const card = screen.getByRole('region', { name: 'Chartered Pathway' });
  await userEvent.click(within(card).getByRole('checkbox', { name: 'Stakeholder Engagement and Communication' }));
  await screen.findByText('All changes saved.');
  expect(serverSelection).toEqual({ programme: 'PCP_L6', configurationVersion: 3, pathway: 'chartered', modulesByPathway: { chartered: ['pmo-stakeholders'] } });
  expect(stored.cvJob?.experienceText).toBe('Saved CV text');
  expect(stored.cvJob?.highestQualification).toBe('Saved qualification');
  expect(stored.programmeSuitability).toBeUndefined();
  first.unmount(); const writes = vi.mocked(saveModuleSelection).mock.calls.length;
  render(page());
  await waitFor(() => expect(screen.getByRole('radio', { name: 'Chartered Pathway' })).toBeChecked());
  expect(within(screen.getByRole('region', { name: 'Chartered Pathway' })).getByRole('checkbox', { name: 'Stakeholder Engagement and Communication' })).toBeChecked();
  expect(screen.getAllByRole('region', { name: 'Programme Modules' })).toHaveLength(1);
  expect(saveModuleSelection).toHaveBeenCalledTimes(writes);
  expect(mocks.write).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Preview Assessment' }));
  expect(screen.getByRole('dialog')).toBeVisible();
  expect(screen.getAllByRole('region', { name: 'Programme Modules' })).toHaveLength(1);
});

it('serialises overlapping autosaves so a slow older response cannot replace the latest module choices', async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.mocked(saveModuleSelection).mockImplementationOnce(async (...args) => { await gate; return persist(...args); });
  render(page());
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  await waitFor(() => expect(saveModuleSelection).toHaveBeenCalledTimes(1));
  const card = screen.getByRole('region', { name: 'Operational Pathway' });
  await userEvent.click(within(card).getByRole('checkbox', { name: 'Project Management Professional (PMP)' }));
  await userEvent.click(within(card).getByRole('checkbox', { name: 'Risk Management (APM)' }));
  await new Promise((resolve) => setTimeout(resolve, 250));
  expect(saveModuleSelection).toHaveBeenCalledTimes(1);
  release();
  await screen.findByText('All changes saved.');
  expect(serverSelection?.modulesByPathway.operational).toEqual(['pmp', 'risk-apm']);
  expect(stored.cvJob?.experienceText).toBe('Saved CV text');
});


function ModuleCompletion() {
  const { draft } = useWizard();
  return <output data-testid="module-completion">{draft.programmeModulesCompletion?.complete ? 'complete' : 'incomplete'}</output>;
}
it('marks Step 6 complete only after the valid new PCP selection is saved and restores completion on reopening', async () => {
  publishedModules = rules as ModulesConfiguration;
  const first = render(<WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  await screen.findByText('All changes saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  const card = within(screen.getByRole('region', { name: 'Operational Pathway' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'Project Planning and Control (APMG Qualification)' }));
  await screen.findByText('All changes saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.mocked(saveModuleSelection).mockImplementationOnce(async (...args) => { await gate; return persist(...args); });
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  release(); await screen.findByText('All changes saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  expect(serverSelection?.modulesByPathway.operational).toHaveLength(5);
  expect(stored.programmeModulesCompletion).toBeUndefined();
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  await screen.findByText('All changes saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  first.unmount();
  render(<WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  await waitFor(() => expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/));
});

it('lets a learner review refreshed rules and save existing options with the authoritative mandatory IDs', async () => {
  publishedModules = rules as ModulesConfiguration;
  serverSelection = { programme: 'PCP_L6', pathway: 'operational', configurationVersion: 2,
    modulesByPathway: { operational: ['planning-control', 'pmi-sp'] } };
  render(<WizardProvider userId="41" board={board} isCommercial={false}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  await userEvent.click(await screen.findByRole('button', { name: 'Save reviewed module selection' }));
  await screen.findByText('All changes saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  expect(serverSelection?.configurationVersion).toBe(3);
  expect(new Set(serverSelection?.modulesByPathway.operational)).toEqual(new Set(['planning-control', 'pmi-sp', 'pmp', 'ai-project-control', 'risk-apm']));
});

function SaveOtherSteps() {
  const { saveIlr } = useWizard();
  return <button onClick={() => { void saveIlr(); }}>Save other steps</button>;
}

it('combines rapid selections into one module request and navigation saving does not rewrite ILR for module-only changes', async () => {
  publishedModules = rules as ModulesConfiguration;
  render(<WizardProvider userId="41" board={board}><ProgrammeSuitabilityStep /><ModuleCompletion /><SaveOtherSteps /></WizardProvider>);
  await userEvent.click(await screen.findByRole('radio', { name: 'Operational Pathway' }));
  const card = within(screen.getByRole('region', { name: 'Operational Pathway' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'Project Planning and Control (APMG Qualification)' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'PMI-SP' }));
  expect(card.getByText('Selected credits: 6')).toBeVisible();
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  await screen.findByText('All changes saved.');
  expect(saveModuleSelection).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  await userEvent.click(screen.getByRole('button', { name: 'Save other steps' }));
  expect(mocks.write).not.toHaveBeenCalled();
});

it('keeps unsaved selections visible after failure and only completes Step 6 after a successful retry', async () => {
  publishedModules = rules as ModulesConfiguration;
  vi.mocked(saveModuleSelection).mockRejectedValueOnce(new Error('Unable to save module changes. Please retry.'));
  render(<WizardProvider userId="41" board={board}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  await userEvent.click(await screen.findByRole('radio', { name: 'Chartered Pathway' }));
  const card = within(screen.getByRole('region', { name: 'Chartered Pathway' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' }));
  await screen.findByRole('alert');
  expect(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' })).toBeChecked();
  expect(card.getByText('Selected credits: 6')).toBeVisible();
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  expect(screen.queryByText('All changes saved.')).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Retry saving module choices' }));
  await screen.findByText('All changes saved.');
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  expect(mocks.write).not.toHaveBeenCalled();
});

it('preserves local choices on a two-session conflict and retries against the refreshed revision', async () => {
  publishedModules = rules as ModulesConfiguration;
  vi.mocked(saveModuleSelection).mockImplementationOnce(async (_kind, _id, selection) => {
    revision = 1;
    serverSelection = { ...structuredClone(selection), modulesByPathway: { chartered: ['pmo-planning-control', 'pmo-stakeholders', 'pmo-risk-quality', 'pmo-office', 'ai-project-management', 'earned-value'] } };
    throw Object.assign(new Error('Module choices changed in another session. Review your choices and retry.'), { status: 409 });
  });
  render(<WizardProvider userId="41" board={board}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  await userEvent.click(await screen.findByRole('radio', { name: 'Chartered Pathway' }));
  const card = within(screen.getByRole('region', { name: 'Chartered Pathway' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' }));
  await screen.findByRole('alert');
  await waitFor(() => expect(getModules).toHaveBeenCalledTimes(2));
  expect(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' })).toBeChecked();
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  await userEvent.click(screen.getByRole('button', { name: 'Retry saving module choices' }));
  await screen.findByText('All changes saved.');
  expect(vi.mocked(saveModuleSelection).mock.calls[1][3]).toBe(1);
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
  expect(mocks.write).not.toHaveBeenCalled();
});

it('refreshes changed publication settings after a rejected save and preserves choices for explicit review', async () => {
  publishedModules = rules as ModulesConfiguration;
  vi.mocked(getModules).mockResolvedValueOnce({ configuration: publishedModules, configurationVersion: 3, selectedProgramme: 'PCP_L6', selectionRevision: 0 })
    .mockResolvedValue({ configuration: publishedModules, configurationVersion: 4, selectedProgramme: 'PCP_L6', selectionRevision: 0 });
  vi.mocked(saveModuleSelection).mockRejectedValueOnce(Object.assign(new Error('Module configuration version does not match your saved enrolment.'), { status: 400 }))
    .mockImplementation(async (_kind, _id, selection) => ({ selection, selectionRevision: 1, selectionComplete: moduleSelectionComplete(publishedModules, selection, 4) }));
  render(<WizardProvider userId="41" board={board}><ProgrammeSuitabilityStep /><ModuleCompletion /></WizardProvider>);
  await userEvent.click(await screen.findByRole('radio', { name: 'Chartered Pathway' }));
  const card = within(screen.getByRole('region', { name: 'Chartered Pathway' }));
  await userEvent.click(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' }));
  const review = await screen.findByRole('button', { name: 'Save reviewed module selection' });
  expect(card.getByRole('checkbox', { name: 'Portfolio Management (APMG)' })).toBeChecked();
  expect(screen.getByTestId('module-completion')).toHaveTextContent('incomplete');
  await userEvent.click(review);
  await screen.findByText('All changes saved.');
  expect(vi.mocked(saveModuleSelection).mock.lastCall![2].configurationVersion).toBe(4);
  expect(screen.getByTestId('module-completion')).toHaveTextContent(/^complete$/);
});
