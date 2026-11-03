import { afterAll, beforeEach, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardProvider } from '@/pages/users/wizard/WizardContext';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import type { EnrolmentBoard, KsbAssessment } from '@/pages/users/types';
import type { AssessmentState } from '../types';
import { fresh } from './fixtures';
import { confirmedApm, apmModules, apmProfile } from './apmRadarFixtures';

const api = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn(), profile: vi.fn(), modules: vi.fn(), layout: vi.fn(), assessment: vi.fn() }));
vi.mock('@/api/extendedIlr', () => ({ fetchExtendedIlr: api.read, peekExtendedIlr: () => undefined, saveExtendedIlr: api.write }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: api.profile, peekKsbProfile: () => undefined }));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/wizardInclusion', () => ({ fetchInclusionStatus: vi.fn(async () => ({ applicable: false, completed: false })) }));
vi.mock('@/api/wizardLayout', () => ({ peekWizardLayout: () => undefined, fetchWizardLayout: api.layout }));
vi.mock('../api', () => ({ getAssessment: api.assessment, peekAssessment: () => undefined,
  prefetchAssessmentSecurity: async () => ({ csrfToken: 'synthetic' }), primeAssessment: vi.fn() }));
vi.mock('../modules/api', () => ({ getModules: api.modules, saveModuleSelection: vi.fn() }));

const board = { user: { id: '41', name: 'Assigned APM learner' }, contact: { email: '', phone: '', dob: '' },
  programme: { name: 'Associate Project Manager Level 4', onboardingStatus: 'In progress' } } as EnrolmentBoard;
const answer: KsbAssessment = { ksbId: 'st0310-v1-5-K1', level: 'rarely', note: 'Learner APM note', evidenceFiles: ['saved.pdf'], actionPlan: { text: 'Keep plan' } };
let latest: ReturnType<typeof useWizard>;
function Observe() { latest = useWizard(); return null; }
const page = (readOnly = true) => <WizardProvider userId="41" board={board} readOnlyLearnerSteps={readOnly}><SkillsRadar /><Observe /></WizardProvider>;

beforeEach(() => {
  vi.clearAllMocks();
  api.profile.mockResolvedValue({ results: apmProfile });
  api.modules.mockResolvedValue(apmModules());
  api.assessment.mockResolvedValue(fresh());
  api.layout.mockResolvedValue({ version: 11, layout: { steps: [], programmeSuitability: confirmedApm.configuration,
    programmeModules: apmModules().configuration } });
  api.read.mockResolvedValue({ answers: null, draft: { skillsRadar: { standardId: 'standard:st0310-v1-5', assessments: {
    [answer.ksbId]: structuredClone(answer), historical: { ...answer, ksbId: 'historical' } } } }, meta: { updatedAt: 'saved' } });
  vi.stubGlobal('AppIcon', ({ className }: { className?: string }) => <i className={className} />);
});
afterAll(() => vi.unstubAllGlobals());

it('shows published 42/20 modules for staff before Step 6, preserving read-only answers without confirmation or writes', async () => {
  render(page());
  const nav = await screen.findByRole('navigation', { name: 'Selected modules' });
  const buttons = within(nav).getAllByRole('button');
  expect(buttons).toHaveLength(2);
  expect(buttons[0]).toHaveTextContent('1 of 42 answered'); expect(buttons[0]).toHaveTextContent('28 Sessions');
  expect(screen.getByText('1 of 62 answered')).toBeVisible();
  expect(screen.getAllByRole('button', { name: /^View [KSB]\d+$/ })).toHaveLength(42);
  const calls = api.profile.mock.calls.length;
  await userEvent.click(buttons[1]);
  expect(screen.getAllByRole('button', { name: /^View [KSB]\d+$/ })).toHaveLength(20);
  await userEvent.click(buttons[0]); await userEvent.click(screen.getByRole('button', { name: 'View K1' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getAllByRole('radio')).toHaveLength(9);
  for (const radio of dialog.getAllByRole('radio')) expect(radio).toBeDisabled();
  expect(dialog.getByLabelText(/Add a note/)).toHaveValue(answer.note);
  expect(dialog.getByLabelText(/Add a note/)).toHaveAttribute('readonly');
  expect(dialog.getByText('saved.pdf')).toBeVisible();
  expect(dialog.queryByRole('button', { name: /Confirm/ })).toBeNull();
  expect(latest.draft.skillsRadar.assessments[answer.ksbId]).toEqual(answer);
  expect(Object.keys(latest.draft.skillsRadar.assessments)).toEqual([answer.ksbId, 'historical']);
  expect(latest.suitability?.selectionConfirmed).toBe(false);
  expect(api.profile).toHaveBeenCalledTimes(calls);
  expect(api.modules).not.toHaveBeenCalled(); expect(api.write).not.toHaveBeenCalled();
});

it('does not enable the unconfirmed staff fallback for a learner', async () => {
  render(page(false));
  await waitFor(() => expect(latest.ready).toBe(true));
  expect(screen.queryByRole('navigation', { name: 'Selected modules' })).toBeNull();
  expect(screen.getByText('Skill description')).toBeVisible();
  expect(latest.suitability?.selectionConfirmed).toBe(false); expect(api.modules).not.toHaveBeenCalled(); expect(api.write).not.toHaveBeenCalled();
});

it('uses the pinned confirmed APM snapshot for staff instead of the newer published metadata', async () => {
  let resolve!: (state: AssessmentState) => void;
  api.assessment.mockReturnValue(new Promise<AssessmentState>((done) => { resolve = done; }));
  const pinned = apmModules(); pinned.configuration.programmes.find((programme) => programme.id === 'APM_L4')!.modules[0].title = 'Pinned PMP title';
  api.modules.mockResolvedValue(pinned);
  render(page());
  await waitFor(() => expect(api.assessment).toHaveBeenCalled());
  expect(screen.queryByRole('navigation', { name: 'Selected modules' })).toBeNull();
  expect(screen.queryByText('Skill description')).toBeNull();
  await act(async () => resolve(confirmedApm));
  await screen.findByRole('button', { name: 'Pinned PMP title' });
  expect(api.modules).toHaveBeenCalled(); expect(api.write).not.toHaveBeenCalled();
});

it('reviews an assigned APM programme when the published wizard has no assessment configuration', async () => {
  api.layout.mockResolvedValue({ version: 11, layout: { steps: [], programmeModules: apmModules().configuration } });
  render(page());
  const nav = await screen.findByRole('navigation', { name: 'Selected modules' });
  expect(within(nav).getAllByRole('button')).toHaveLength(2);
  expect(api.assessment).not.toHaveBeenCalled();
  expect(api.modules).not.toHaveBeenCalled(); expect(api.write).not.toHaveBeenCalled();
});

it('never overrides a confirmed alternative programme with the stale assigned APM board', async () => {
  api.assessment.mockResolvedValue({ ...confirmedApm, selectedProgramme: 'ME_L4' } satisfies AssessmentState);
  api.modules.mockResolvedValue({ ...apmModules(), selectedProgramme: 'ME_L4' });
  api.profile.mockResolvedValue({ results: [] });
  render(page());
  await waitFor(() => expect(latest.suitability?.selectionConfirmed).toBe(true));
  await waitFor(() => expect(api.profile).toHaveBeenCalledWith('Marketing Executive Level 4'));
  expect(latest.modules?.data?.selectedProgramme).toBe('ME_L4');
  expect(screen.queryByRole('button', { name: 'Project Management Professional' })).toBeNull();
});

it('reports missing published modules clearly instead of silently showing the old flat staff view', async () => {
  api.layout.mockResolvedValue({ version: 11, layout: { steps: [], programmeSuitability: confirmedApm.configuration } });
  render(page());
  expect(await screen.findByRole('alert')).toHaveTextContent('Published APM modules are unavailable');
  expect(screen.queryByText('Skill description')).toBeNull();
  expect(api.modules).not.toHaveBeenCalled(); expect(api.write).not.toHaveBeenCalled();
});
