import { beforeEach, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardProvider } from '@/pages/users/wizard/WizardContext';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import { BUILTIN_ITEMS } from '@/pages/users/wizard/layout/registry';
import type { EnrolmentBoard, KsbAssessment, WizardDraft } from '@/pages/users/types';
import { confirmedMe, meModules, meProfile } from './meRadarFixtures';

const api = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn(), profile: vi.fn(), modules: vi.fn() }));
vi.mock('@/api/extendedIlr', () => ({ fetchExtendedIlr: api.read, peekExtendedIlr: () => undefined, saveExtendedIlr: api.write }));
vi.mock('@/api/curriculum', () => ({ fetchKsbProfile: api.profile, peekKsbProfile: () => undefined }));
vi.mock('@/api/enrolmentDocuments', () => ({ uploadEnrolmentDocument: vi.fn() }));
vi.mock('@/api/wizardInclusion', () => ({ fetchInclusionStatus: vi.fn(async () => ({ applicable: false, completed: false })) }));
vi.mock('@/api/wizardLayout', () => ({ peekWizardLayout: () => undefined, fetchWizardLayout: async () => ({ version: 11,
  layout: { steps: [], programmeSuitability: confirmedMe.configuration } }) }));
vi.mock('../api', () => ({ getAssessment: async () => confirmedMe, peekAssessment: () => undefined,
  prefetchAssessmentSecurity: async () => ({ csrfToken: 'synthetic' }), primeAssessment: vi.fn() }));
vi.mock('../modules/api', () => ({ getModules: api.modules, saveModuleSelection: vi.fn() }));

const board = { user: { id: '41', name: 'Learner' }, contact: { email: '', phone: '', dob: '' },
  programme: { name: 'Marketing Manager Level 6', onboardingStatus: 'In progress' } } as EnrolmentBoard;
let latest: ReturnType<typeof useWizard>;
function Observe() {
  latest = useWizard();
  return <button onClick={() => { void latest.saveIlr(); }}>Save existing answers</button>;
}
const page = () => <WizardProvider userId="41" board={board}><SkillsRadar /><Observe /></WizardProvider>;
const answer: KsbAssessment = { ksbId: 'st0596-v1-0-K1', level: 'frequently', note: 'Existing note', evidenceFiles: ['existing.pdf'], actionPlan: { text: 'Existing plan' } };
beforeEach(() => {
  vi.clearAllMocks();
  api.profile.mockResolvedValue({ results: meProfile });
  api.modules.mockResolvedValue(meModules());
  api.read.mockResolvedValue({ answers: null, draft: { skillsRadar: { standardId: 'standard:st0596-v1-0', assessments: {
    [answer.ksbId]: structuredClone(answer), historical: { ...answer, ksbId: 'historical' } } } }, meta: { updatedAt: 'saved' } });
  api.write.mockResolvedValue({ meta: { updatedAt: 'saved-again' } });
});

it('uses confirmed ME with a stale board and filters categories without changing saved answers', async () => {
  render(page());
  await screen.findByLabelText('Selected module skills assessments');
  await waitFor(() => expect(api.profile).toHaveBeenCalledWith('Marketing Executive Level 4'));
  await waitFor(() => expect(latest.draft.skillsRadar.programmeKsbIds).toHaveLength(33));
  expect(latest.draft.skillsRadar.assessments[answer.ksbId]).toEqual(answer);
  const calls = api.profile.mock.calls.length;
  const knowledge = screen.getByRole('button', { name: /^Knowledge / });
  await userEvent.click(knowledge);
  expect(knowledge).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(knowledge);
  expect(knowledge).toHaveAttribute('aria-pressed', 'false');
  expect(api.profile).toHaveBeenCalledTimes(calls);
  expect(api.write).not.toHaveBeenCalled();
  expect(BUILTIN_ITEMS['block.skillsRadar'].missing!(latest.draft, true)).toEqual(['32 competencies not yet rated']);
});

it('keeps the existing answer persistence envelope and never stores grouping/derived validation metadata', async () => {
  render(page());
  await screen.findByLabelText('Selected module skills assessments');
  act(() => latest.setSection('skillsRadar', { ...latest.draft.skillsRadar, assessments: { ...latest.draft.skillsRadar.assessments,
    'st0596-v1-0-K2': { ...answer, ksbId: 'st0596-v1-0-K2', level: 'rarely', note: 'New answer' } } }));
  await userEvent.click(screen.getByRole('button', { name: 'Save existing answers' }));
  await waitFor(() => expect(api.write).toHaveBeenCalledTimes(1));
  const saved = api.write.mock.calls[0][3] as WizardDraft;
  expect(saved.skillsRadar.assessments[answer.ksbId]).toEqual(answer);
  expect(saved.skillsRadar.assessments.historical).toMatchObject({ note: 'Existing note', evidenceFiles: ['existing.pdf'] });
  expect(saved.skillsRadar).not.toHaveProperty('programmeKsbIds');
  expect(saved.skillsRadar).not.toHaveProperty('configurationError');
  expect(saved.skillsRadar).not.toHaveProperty('modules');
  expect(saved).not.toHaveProperty('programmeModules');
});

it('blocks completion on a missing profile code even when all returned questions were already answered', async () => {
  const incomplete = meProfile.filter((item) => item.codes[0] !== 'K6');
  api.profile.mockResolvedValue({ results: incomplete });
  api.read.mockResolvedValue({ answers: null, draft: { skillsRadar: { standardId: '', assessments: Object.fromEntries(incomplete.map((item) => [item.id, { ...answer, ksbId: item.id }])) } }, meta: { updatedAt: 'saved' } });
  render(page());
  await waitFor(() => expect(latest.draft.skillsRadar.configurationError).toContain('K6'));
  expect(BUILTIN_ITEMS['block.skillsRadar'].missing!(latest.draft, true)[0]).toContain('K6');
  expect(screen.getByRole('alert')).toHaveTextContent('K6');
  expect(api.write).not.toHaveBeenCalled();
});

it('requires all 33 distinct current answers, ignores historical ratings, and completes only after the final response', async () => {
  const assessments: Record<string, KsbAssessment> = Object.fromEntries(meProfile.map((ksb) => [ksb.id, { ...answer, ksbId: ksb.id }]));
  const final = meProfile.at(-1)!;
  delete assessments[final.id];
  assessments.historical = { ...answer, ksbId: 'historical' };
  api.read.mockResolvedValue({ answers: null, draft: { skillsRadar: { standardId: '', assessments } }, meta: { updatedAt: 'saved' } });
  render(page());
  await screen.findByLabelText('Selected module skills assessments');
  await waitFor(() => expect(latest.draft.skillsRadar.programmeKsbIds).toHaveLength(33));
  expect(screen.getByText('32 of 33 answered')).toBeVisible();
  expect(BUILTIN_ITEMS['block.skillsRadar'].missing!(latest.draft, true)).toEqual(['1 competency not yet rated']);
  act(() => latest.setSection('skillsRadar', { ...latest.draft.skillsRadar, assessments: { ...latest.draft.skillsRadar.assessments,
    [final.id]: { ...answer, ksbId: final.id } } }));
  expect(screen.getByText('33 of 33 answered')).toBeVisible();
  expect(BUILTIN_ITEMS['block.skillsRadar'].missing!(latest.draft, true)).toEqual([]);
  for (const card of within(screen.getByRole('navigation', { name: 'Selected modules' })).getAllByText(/\d+ of \d+ answered/)) {
    const counts = card.textContent!.match(/(\d+) of (\d+) answered/)!;
    expect(counts[1]).toBe(counts[2]);
  }
  expect(latest.draft.skillsRadar.assessments.historical).toEqual(assessments.historical);
  expect(api.write).not.toHaveBeenCalled();
});
