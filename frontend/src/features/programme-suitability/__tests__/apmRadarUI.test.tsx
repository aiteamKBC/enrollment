import { useState } from 'react';
import { afterAll, beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { KsbAssessment, SkillsRadarState } from '@/pages/users/types';
import type { WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import { COMPETENCE_LEVELS } from '@/mocks/enrolment-console';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import { allocateApmQuestions } from '../skills-radar/associateProjectManager';
import source from '../skills-radar/associate_project_manager_l4_ksb_full_mapping.json';
import { confirmedApm, apmModules, apmProfile } from './apmRadarFixtures';

const mock = vi.hoisted(() => ({ context: {} as WizardContextValue, profile: [] as typeof apmProfile, write: vi.fn(), fetch: vi.fn() }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', () => ({ useWizard: () => mock.context }));
vi.mock('@/api/curriculum', () => ({ peekKsbProfile: () => ({ results: mock.profile }), fetchKsbProfile: (...args: unknown[]) => mock.fetch(...args) }));
function Harness({ modules = apmModules(), saved = {}, readOnly = false, programme = 'APM_L4' }: {
  modules?: ReturnType<typeof apmModules>; saved?: Record<string, KsbAssessment>; readOnly?: boolean; programme?: 'APM_L4';
}) {
  const [radar, setRadar] = useState<SkillsRadarState>({ standardId: 'standard:st0310-v1-5', assessments: saved });
  mock.context = { board: { programme: { name: programme === 'APM_L4' ? 'Associate Project Manager Level 4' : programme } }, draft: { skillsRadar: radar },
    suitability: { state: { ...confirmedApm, selectedProgramme: programme } }, modules: { data: modules, error: '' }, readOnly,
    setSection: (_key: string, value: SkillsRadarState) => { mock.write(value); setRadar(value); } } as unknown as WizardContextValue;
  return <SkillsRadar />;
}
beforeEach(() => {
  vi.clearAllMocks(); mock.profile = structuredClone(apmProfile);
  vi.stubGlobal('AppIcon', ({ className }: { className?: string }) => <i className={className} />);
});
afterAll(() => vi.unstubAllGlobals());

it('shows the two fixed modules and all exact 42/20 descriptions in one panel, without selectors or navigation writes', async () => {
  render(<Harness />);
  const nav = screen.getByRole('navigation', { name: 'Selected modules' });
  const buttons = within(nav).getAllByRole('button');
  expect(buttons).toHaveLength(2); expect(buttons[0]).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('radio')).toBeNull(); expect(screen.queryByRole('checkbox')).toBeNull();
  expect(document.querySelectorAll('details,summary')).toHaveLength(0);
  const groups = allocateApmQuestions(apmProfile, apmModules()); const codes = new Set<string>();
  for (const [index, group] of groups.entries()) {
    await userEvent.click(buttons[index]);
    expect(within(nav).getAllByRole('button', { pressed: true })).toEqual([buttons[index]]);
    expect(within(nav).getAllByRole('button')).toEqual(buttons);
    expect(buttons[index]).toHaveTextContent(`0 of ${group.questions.length} answered`);
    expect(buttons[index]).toHaveTextContent(`${group.sessions} Sessions`);
    expect(screen.getAllByRole('region', { name: /KSB questions$/ })).toHaveLength(1);
    const panel = within(screen.getByRole('region', { name: `${group.title} KSB questions` }));
    expect(panel.getAllByRole('button', { name: /^Answer [KSB]\d+$/ })).toHaveLength(group.questions.length);
    for (const question of group.questions) {
      expect(panel.getByText(`${question.codes[0]} ${question.title}`)).toBeVisible();
      expect(within(nav).queryByText(`${question.codes[0]} ${question.title}`)).toBeNull(); codes.add(question.codes[0]);
    }
  }
  expect(codes.size).toBe(62); expect(screen.getByText('0 of 62 answered')).toBeVisible();
  expect(mock.write).not.toHaveBeenCalled(); expect(mock.fetch).not.toHaveBeenCalled();
});

it('uses all nine original ratings, notes and evidence and selects the next module across question 42/43', async () => {
  render(<Harness />);
  const nav = screen.getByRole('navigation', { name: 'Selected modules' });
  const buttons = within(nav).getAllByRole('button');
  await userEvent.click(screen.getByRole('button', { name: 'Answer B3' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText('Question 42 of 62')).toBeVisible(); expect(dialog.getAllByRole('radio')).toHaveLength(9);
  for (const level of COMPETENCE_LEVELS) {
    const radio = dialog.getByRole('radio', { name: new RegExp(level.label) });
    await userEvent.click(radio); expect(radio).toBeChecked();
  }
  await userEvent.click(dialog.getByRole('radio', { name: /Champion/ }));
  expect(dialog.getByRole('button', { name: /Confirm & next/i })).toBeDisabled();
  await userEvent.type(dialog.getByLabelText(/Add a note/), 'Original APM evidence workflow');
  await userEvent.upload(screen.getByRole('dialog').querySelector('input[type="file"]')!, new File(['evidence'], 'apm-evidence.txt', { type: 'text/plain' }));
  await userEvent.click(dialog.getByRole('button', { name: /Confirm & next/i }));
  expect(mock.write.mock.lastCall![0].assessments['st0310-v1-5-B3']).toMatchObject({ ksbId: 'st0310-v1-5-B3', level: 'champion', note: 'Original APM evidence workflow', evidenceFiles: ['apm-evidence.txt'] });
  expect(screen.getByText('Question 43 of 62')).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByText((text) => text.includes(source.ksbs.find((item) => item.code === 'K15')!.full_description))).toBeVisible();
  expect(buttons[1]).toHaveAttribute('aria-pressed', 'true'); expect(buttons[0]).toHaveAttribute('aria-pressed', 'false');
  expect(buttons[0]).toHaveTextContent('1 of 42 answered'); expect(screen.getByText('1 of 62 answered')).toBeVisible();
});

it('restores existing flat-layout ratings/notes/evidence/action plans under Project Management Professional without changing source wording', async () => {
  const answer: KsbAssessment = { ksbId: 'st0310-v1-5-S14', level: 'expert', note: 'Previous flat-layout note', evidenceFiles: ['old-apm.pdf'], actionPlan: { text: 'Keep plan' } };
  render(<Harness saved={{ [answer.ksbId]: answer, historical: { ...answer, ksbId: 'historical' } }} />);
  await userEvent.click(within(screen.getByRole('navigation', { name: 'Selected modules' })).getByRole('button', { name: 'Project Management Professional' }));
  expect(screen.getByText('S14 Identify and monitor project risks and issues; and plan and implement responses to them.')).toBeVisible();
  expect(screen.getByText('1 of 62 answered')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Edit S14' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByLabelText(/Add a note/)).toHaveValue(answer.note); expect(dialog.getByText('old-apm.pdf')).toBeVisible();
  expect(dialog.getByRole('radio', { name: /Expert/ })).toBeChecked();
  await userEvent.click(dialog.getByRole('button', { name: 'Cancel' })); expect(mock.write).not.toHaveBeenCalled();
});

it('reflects published admin names and sessions while retaining all fixed ownership', async () => {
  const modules = apmModules(); modules.configuration.programmes.find((item) => item.id === 'APM_L4')!.modules[0].title = 'Published PMP name';
  modules.configuration.programmes.find((item) => item.id === 'APM_L4')!.modules[0].sessions = 22;
  render(<Harness modules={modules} />);
  expect(within(screen.getByRole('navigation', { name: 'Selected modules' })).getByRole('button', { name: 'Published PMP name' })).toHaveTextContent('22 Sessions');
  expect(screen.getByRole('region', { name: 'Published PMP name KSB questions' })).toHaveTextContent('42 KSBs');
  expect(screen.getByRole('button', { name: 'Answer K1' })).toBeVisible();
});

it('reports exact missing KSB codes without rendering a partial or flat fallback', () => {
  mock.profile = apmProfile.filter((ksb) => ksb.codes[0] !== 'K6'); render(<Harness />);
  expect(screen.getByRole('alert')).toHaveTextContent('K6'); expect(screen.queryByRole('button', { name: /^Answer / })).toBeNull();
});

it('preserves read-only staff review', async () => {
  render(<Harness readOnly />); await userEvent.click(screen.getByRole('button', { name: 'View K1' }));
  const dialog = within(screen.getByRole('dialog'));
  for (const radio of dialog.getAllByRole('radio')) expect(radio).toBeDisabled();
  expect(dialog.getByLabelText(/Add a note/)).toHaveAttribute('readonly'); expect(mock.write).not.toHaveBeenCalled();
});

it('keeps overlapping K1 responses scoped to the APM profile and preserves a historical PCP answer', async () => {
  const own: KsbAssessment = { ksbId: 'st0310-v1-5-K1', level: 'rarely', note: 'APM K1 note', evidenceFiles: ['apm.pdf'], actionPlan: null };
  const foreign: KsbAssessment = { ksbId: 'st0845-v1-1-K1', level: 'expert', note: 'Historical PCP K1', evidenceFiles: ['pcp.pdf'], actionPlan: { text: 'Keep historical plan' } };
  render(<Harness saved={{ [own.ksbId]: own, [foreign.ksbId]: foreign }} />);
  expect(screen.getByText('1 of 62 answered')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Edit K1' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByLabelText(/Add a note/)).toHaveValue('APM K1 note');
  expect(dialog.getByRole('radio', { name: /Rarely/ })).toBeChecked();
  expect(dialog.queryByText('pcp.pdf')).toBeNull();
  await userEvent.click(dialog.getByRole('radio', { name: /Frequently/ }));
  await userEvent.click(dialog.getByRole('button', { name: /Confirm & next/i }));
  const saved = mock.write.mock.lastCall![0].assessments;
  expect(saved[own.ksbId]).toMatchObject({ level: 'frequently', note: 'APM K1 note', evidenceFiles: ['apm.pdf'] });
  expect(saved[foreign.ksbId]).toEqual(foreign);
});
