import { useState } from 'react';
import { afterAll, beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { KsbAssessment, SkillsRadarState } from '@/pages/users/types';
import type { WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import { COMPETENCE_LEVELS } from '@/mocks/enrolment-console';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import { allocateMmQuestions } from '../skills-radar/marketingManager';
import source from '../skills-radar/marketing_manager_l6_ksb_full_mapping.json';
import { confirmedMm, mmModules, mmProfile } from './mmRadarFixtures';

const mock = vi.hoisted(() => ({ context: {} as WizardContextValue, profile: [] as typeof mmProfile, write: vi.fn(), fetch: vi.fn() }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', () => ({ useWizard: () => mock.context }));
vi.mock('@/api/curriculum', () => ({ peekKsbProfile: () => ({ results: mock.profile }), fetchKsbProfile: (...args: unknown[]) => mock.fetch(...args) }));
function Harness({ modules = mmModules(), saved = {}, readOnly = false, programme = 'MM_L6' }: {
  modules?: ReturnType<typeof mmModules>; saved?: Record<string, KsbAssessment>; readOnly?: boolean; programme?: 'MM_L6' | 'ME_L4' | 'APM_L4';
}) {
  const [radar, setRadar] = useState<SkillsRadarState>({ standardId: 'standard:st0612-v1-0', assessments: saved });
  mock.context = { board: { programme: { name: programme === 'MM_L6' ? 'Marketing Manager Level 6' : programme } }, draft: { skillsRadar: radar },
    suitability: { state: { ...confirmedMm, selectedProgramme: programme } }, modules: { data: modules, error: '' }, readOnly,
    setSection: (_key: string, value: SkillsRadarState) => { mock.write(value); setRadar(value); } } as unknown as WizardContextValue;
  return <SkillsRadar />;
}
beforeEach(() => {
  vi.clearAllMocks(); mock.profile = structuredClone(mmProfile);
  vi.stubGlobal('AppIcon', ({ className }: { className?: string }) => <i className={className} />);
});
afterAll(() => vi.unstubAllGlobals());

it('shows all 42 KSBs once with four module coverage summaries and aligned table columns', () => {
  render(<Harness />);
  const nav = screen.getByRole('navigation', { name: 'Selected modules' });
  expect(within(nav).queryByRole('button')).toBeNull();
  expect(screen.queryByRole('radio')).toBeNull(); expect(screen.queryByRole('checkbox')).toBeNull();
  expect(document.querySelectorAll('details,summary')).toHaveLength(0);
  expect(screen.getByText('SKILL DESCRIPTION')).toBeVisible();
  expect(screen.getByText('COVERED BY MODULES')).toBeVisible();
  expect(screen.getByText('ANSWER')).toBeVisible();
  const groups = allocateMmQuestions(mmProfile, mmModules()); const codes = new Set<string>();
  for (const group of groups) {
    expect(within(nav).getByText(group.title)).toBeVisible();
    expect(within(nav).getByText(`0 of ${group.questions.length} answered`)).toBeVisible();
    for (const question of group.questions) {
      expect(screen.getByText(`${question.codes[0]} ${question.title}`)).toBeVisible();
      expect(screen.getByRole('button', { name: `Answer ${question.codes[0]}` })).toBeVisible();
      expect(within(nav).queryByText(`${question.codes[0]} ${question.title}`)).toBeNull(); codes.add(question.codes[0]);
    }
  }
  expect(screen.getAllByRole('button', { name: /^Answer [KSB]\d+$/ })).toHaveLength(42);
  expect(codes.size).toBe(42); expect(screen.getByText('0 of 42 answered')).toBeVisible();
  expect(mock.write).not.toHaveBeenCalled(); expect(mock.fetch).not.toHaveBeenCalled();
});

it('uses all nine ratings, notes and evidence and advances across question 16/17', async () => {
  render(<Harness />);
  const nav = screen.getByRole('navigation', { name: 'Selected modules' });
  await userEvent.click(screen.getByRole('button', { name: 'Answer B6' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText('Question 16 of 42')).toBeVisible(); expect(dialog.getAllByRole('radio')).toHaveLength(9);
  for (const level of COMPETENCE_LEVELS) {
    const radio = dialog.getByRole('radio', { name: new RegExp(level.label) });
    await userEvent.click(radio); expect(radio).toBeChecked();
  }
  await userEvent.click(dialog.getByRole('radio', { name: /Champion/ }));
  expect(dialog.getByRole('button', { name: /Confirm & next/i })).toBeDisabled();
  await userEvent.type(dialog.getByLabelText(/Add a note/), 'Original MM evidence workflow');
  await userEvent.upload(screen.getByRole('dialog').querySelector('input[type="file"]')!, new File(['evidence'], 'mm-evidence.txt', { type: 'text/plain' }));
  await userEvent.click(dialog.getByRole('button', { name: /Confirm & next/i }));
  expect(mock.write.mock.lastCall![0].assessments['st0612-v1-0-B6']).toMatchObject({ ksbId: 'st0612-v1-0-B6', level: 'champion', note: 'Original MM evidence workflow', evidenceFiles: ['mm-evidence.txt'] });
  expect(screen.getByText('Question 17 of 42')).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByText((text) => text.includes(source.ksbs.find((item) => item.code === 'K3')!.full_description))).toBeVisible();
  expect(within(nav).getByText('1 of 16 answered')).toBeVisible();
  expect(screen.getByText('1 of 42 answered')).toBeVisible();
});

it('restores existing flat-layout ratings/notes/evidence/action plans under Commercial Intelligence without changing source spelling', async () => {
  const answer: KsbAssessment = { ksbId: 'st0612-v1-0-S14', level: 'expert', note: 'Previous flat-layout note', evidenceFiles: ['old-mm.pdf'], actionPlan: { text: 'Keep plan' } };
  render(<Harness saved={{ [answer.ksbId]: answer, historical: { ...answer, ksbId: 'historical' } }} />);
  expect(within(screen.getByRole('navigation', { name: 'Selected modules' })).getByText('Commercial Intelligence')).toBeVisible();
  expect(screen.getByText('S14 Construct a robust buness case in support of marketing activities/plans')).toBeVisible();
  expect(screen.getByText('1 of 42 answered')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Edit S14' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByLabelText(/Add a note/)).toHaveValue(answer.note); expect(dialog.getByText('old-mm.pdf')).toBeVisible();
  expect(dialog.getByRole('radio', { name: /Expert/ })).toBeChecked();
  await userEvent.click(dialog.getByRole('button', { name: 'Cancel' })); expect(mock.write).not.toHaveBeenCalled();
});

it('reflects a published module name in the coverage summary and KSB cell', () => {
  const modules = mmModules(); modules.configuration.programmes.find((item) => item.id === 'MM_L6')!.modules[0].title = 'Published strategy name';
  modules.configuration.programmes.find((item) => item.id === 'MM_L6')!.modules[0].sessions = 22;
  render(<Harness modules={modules} />);
  expect(within(screen.getByRole('navigation', { name: 'Selected modules' })).getByText('Published strategy name')).toBeVisible();
  expect(screen.getAllByText('Published strategy name').length).toBeGreaterThan(1);
  expect(screen.getByRole('button', { name: 'Answer K1' })).toBeVisible();
});

it('reports exact missing KSB codes without rendering a partial or flat fallback', () => {
  mock.profile = mmProfile.filter((ksb) => ksb.codes[0] !== 'K6'); render(<Harness />);
  expect(screen.getByRole('alert')).toHaveTextContent('K6'); expect(screen.queryByRole('button', { name: /^Answer / })).toBeNull();
});

it('preserves read-only staff review', async () => {
  render(<Harness readOnly />); await userEvent.click(screen.getByRole('button', { name: 'View K1' }));
  const dialog = within(screen.getByRole('dialog'));
  for (const radio of dialog.getAllByRole('radio')) expect(radio).toBeDisabled();
  expect(dialog.getByLabelText(/Add a note/)).toHaveAttribute('readonly'); expect(mock.write).not.toHaveBeenCalled();
});

it('does not reuse MM profile KSBs when the confirmed programme is APM', () => {
  render(<Harness programme="APM_L4" modules={{ ...mmModules(), selectedProgramme: 'APM_L4' }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('APM KSB configuration');
  expect(screen.queryByRole('button', { name: /^Answer / })).toBeNull();
  expect(screen.queryByRole('navigation', { name: 'Selected modules' })).toBeNull();
});
