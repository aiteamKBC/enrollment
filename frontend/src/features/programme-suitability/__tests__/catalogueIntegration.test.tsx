import { afterAll, beforeEach, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardProvider } from '@/pages/users/wizard/WizardContext';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import { BUILTIN_ITEMS } from '@/pages/users/wizard/layout/registry';
import type { EnrolmentBoard, KsbAssessment } from '@/pages/users/types';
import type { ProgrammeId } from '../types';
import { addedCatalogue, catalogueIndex, catalogues } from './catalogueFixtures';
import { confirmedMe } from './meRadarFixtures';
import { confirmedMm } from './mmRadarFixtures';
import { confirmedApm } from './apmRadarFixtures';
import { confirmedPcp, pcpModules } from './pcpRadarFixtures';

const api = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn(), profile: vi.fn(), layout: vi.fn(), assessment: vi.fn(), radar: vi.fn(), modules: vi.fn() }));
vi.mock('@/api/extendedIlr', () => ({ fetchExtendedIlr: api.read, peekExtendedIlr: () => undefined, saveExtendedIlr: api.write }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: api.profile, peekKsbProfile: () => undefined }));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/wizardInclusion', () => ({ fetchInclusionStatus: vi.fn(async () => ({ applicable: false, completed: false })) }));
vi.mock('@/api/wizardLayout', () => ({ peekWizardLayout: () => undefined, fetchWizardLayout: api.layout }));
vi.mock('../api', () => ({ getAssessment: api.assessment, peekAssessment: () => undefined, primeAssessment: vi.fn(), prefetchAssessmentSecurity: async () => ({ csrfToken: 'synthetic' }) }));
vi.mock('../modules/api', () => ({ getModules: api.modules, saveModuleSelection: vi.fn() }));
vi.mock('../skills-radar/catalogue/api', () => ({ getLearnerCatalogue: api.radar }));
const board = { user: { id: '41', name: 'Synthetic learner' }, contact: { email: '', phone: '', dob: '' },
  programme: { name: 'Marketing Executive Level 4', onboardingStatus: 'In progress' } } as EnrolmentBoard;
const confirmed = { ME_L4: confirmedMe, MM_L6: confirmedMm, APM_L4: confirmedApm, PCP_L6: confirmedPcp };
let latest: ReturnType<typeof useWizard>;
function Observe() { latest = useWizard(); return <button onClick={() => { void latest.saveIlr(); }}>Save answers</button>; }
const page = (readOnly = false) => <WizardProvider userId="41" board={board} readOnlyLearnerSteps={readOnly}><SkillsRadar /><Observe /></WizardProvider>;
function setup(pid: ProgrammeId, updated = true) {
  const catalogue = updated ? addedCatalogue(pid) : catalogues()[pid];
  api.assessment.mockResolvedValue(confirmed[pid]);
  api.modules.mockResolvedValue({ ...pcpModules(), selectedProgramme: pid });
  api.radar.mockResolvedValue({ programmeId: pid, catalogue, token: `signed:${pid}`, pinned: !updated });
  return catalogue;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('AppIcon', ({ className }: { className?: string }) => <i className={className} />);
  const index = catalogueIndex(); index.catalogues.ME_L4 = addedCatalogue();
  api.layout.mockResolvedValue({ version: 11, layout: { steps: [], programmeModules: index.modules, skillsRadarCatalogues: index.catalogues, programmeSuitability: confirmedMe.configuration } });
  api.read.mockResolvedValue({ answers: null, draft: null, meta: { updatedAt: '' } });
  api.write.mockResolvedValue({ meta: { updatedAt: 'saved' } });
  setup('ME_L4');
});
afterAll(() => vi.unstubAllGlobals());

it.each(['ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'] as ProgrammeId[])('renders a new imported %s catalogue with dynamic module/global progress and no curriculum fallback', async (pid) => {
  const catalogue = setup(pid);
  render(page());
  await screen.findByRole('navigation', { name: 'Selected modules' });
  await waitFor(() => expect(latest.draft.skillsRadar.programmeKsbIds).toHaveLength(catalogue.records.length));
  expect(screen.getByText(`0 of ${catalogue.records.length} answered`)).toBeVisible();
  expect(BUILTIN_ITEMS['block.skillsRadar'].missing!(latest.draft, true)).toEqual([`${catalogue.records.length} competencies not yet rated`]);
  const cards = within(screen.getByRole('navigation', { name: 'Selected modules' })).getAllByText(/0 of \d+ answered/);
  const total = cards.reduce((count, card) => count + Number(card.textContent!.match(/0 of (\d+) answered/)![1]), 0);
  expect(total).toBe(catalogue.records.length);
  expect(screen.getAllByRole('button', { name: /^Answer [KSB]\d+$/ })).toHaveLength(catalogue.records.length);
  expect(api.profile).not.toHaveBeenCalled(); expect(api.write).not.toHaveBeenCalled();
});

it('keeps pinned 33-question history after a 34-question publication, refresh and staff read-only View', async () => {
  const old = setup('ME_L4', false);
  const ratings: Record<string, KsbAssessment> = Object.fromEntries(old.records.map((r) => [r.id, { ksbId: r.id, level: 'rarely', note: 'Historical note', evidenceFiles: ['old-proof.pdf'], actionPlan: { text: 'Keep' } }]));
  api.read.mockResolvedValue({ answers: null, draft: { skillsRadar: { standardId: '', catalogueVersions: { ME_L4: old.version }, assessments: ratings } }, meta: { updatedAt: 'old-save' } });
  const first = render(page());
  await screen.findByText('33 of 33 answered');
  await waitFor(() => expect(latest.draft.skillsRadar.programmeKsbIds).toHaveLength(33));
  expect(BUILTIN_ITEMS['block.skillsRadar'].missing!(latest.draft, true)).toEqual([]);
  expect(latest.draft.skillsRadar.assessments).toEqual(ratings);
  first.unmount();
  render(page(true));
  await screen.findByText('33 of 33 answered');
  await userEvent.click(screen.getAllByRole('button', { name: /^View [KSB]\d+$/ })[0]);
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByLabelText(/Add a note/)).toHaveValue('Historical note');
  expect(dialog.getByText('old-proof.pdf')).toBeVisible();
  expect(dialog.getAllByRole('radio')).toHaveLength(9);
  dialog.getAllByRole('radio').forEach((r) => expect(r).toBeDisabled());
  expect(api.write).not.toHaveBeenCalled();
});

it('uses the existing Answer modal and saves signed version metadata without changing prior evidence or other wizard answers', async () => {
  const catalogue = setup('ME_L4'); const existing = catalogue.records[0];
  const answer = { ksbId: existing.id, level: 'rarely', note: 'Keep prior note', evidenceFiles: ['keep.pdf'], actionPlan: { text: 'Keep plan' } };
  api.read.mockResolvedValue({ answers: null, draft: { skillsRadar: { standardId: '', assessments: { [existing.id]: answer } }, cvJob: { experienceText: 'Saved CV' } }, meta: { updatedAt: 'old' } });
  render(page());
  await screen.findByText('1 of 34 answered');
  const next = catalogue.records[1];
  act(() => latest.setSection('skillsRadar', { ...latest.draft.skillsRadar, assessments: { ...latest.draft.skillsRadar.assessments, [next.id]: { ...answer, ksbId: next.id, level: 'occasionally' } } }));
  await userEvent.click(screen.getByRole('button', { name: 'Save answers' }));
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1));
  const saved = api.write.mock.calls[0][3];
  expect(saved.skillsRadar.catalogueToken).toBe('signed:ME_L4');
  expect(saved.skillsRadar.assessments[existing.id]).toEqual(answer);
  expect(saved.cvJob.experienceText).toBe('Saved CV');
  expect(saved.skillsRadar).not.toHaveProperty('programmeKsbIds');
  await userEvent.click(screen.getByRole('button', { name: 'Continue assessment' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getAllByRole('radio')).toHaveLength(9);
  await userEvent.click(dialog.getAllByRole('radio')[0]);
  await userEvent.click(dialog.getByRole('button', { name: 'Confirm & next' }));
  expect(screen.getByRole('dialog')).toBeVisible();
});

it('failed version loads block completion, rather than silently accepting a legacy list with the wrong denominator', async () => {
  api.radar.mockRejectedValue(new Error('Could not load the saved Skills Radar version.'));
  render(page());
  await screen.findByText('Could not load the saved Skills Radar version.');
  expect(BUILTIN_ITEMS['block.skillsRadar'].missing!(latest.draft, true)).toEqual(['Could not load the saved Skills Radar version.']);
  expect(api.profile).not.toHaveBeenCalled(); expect(api.write).not.toHaveBeenCalled();
});
