import { useState } from 'react';
import { afterAll, beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { KsbAssessment, SkillsRadarState } from '@/pages/users/types';
import type { WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import { allocatePcpQuestions } from '../skills-radar/allocation';
import source from '../skills-radar/pcp_l6_ksb_full_descriptions_and_mapping.json';
import { confirmedPcp, pcpModules, pcpProfile } from './pcpRadarFixtures';

const mock = vi.hoisted(() => ({ context: {} as WizardContextValue, profile: [] as typeof pcpProfile, write: vi.fn(), fetch: vi.fn() }));
vi.mock('@/pages/users/wizard/WizardContext.helpers', () => ({ useWizard: () => mock.context }));
vi.mock('@/api/curriculum', () => ({ peekKsbProfile: () => ({ results: mock.profile }), fetchKsbProfile: (...args: unknown[]) => mock.fetch(...args) }));

function Harness({ modules = pcpModules(), saved = {}, readOnly = false }: { modules?: ReturnType<typeof pcpModules>; saved?: Record<string, KsbAssessment>; readOnly?: boolean }) {
  const [radar, setRadar] = useState<SkillsRadarState>({ standardId: 'standard:st0845-v1-1', assessments: saved });
  mock.context = { board: { programme: { name: 'Project Controls Professional Level 6' } }, draft: { skillsRadar: radar,
    programmeModules: { programme: 'PCP_L6', pathway: 'chartered' } }, suitability: { state: confirmedPcp }, modules: { data: modules, error: '' }, readOnly,
    setSection: (_key: string, value: SkillsRadarState) => { mock.write(value); setRadar(value); } } as unknown as WizardContextValue;
  return <SkillsRadar />;
}

beforeEach(() => {
  vi.clearAllMocks(); mock.profile = structuredClone(pcpProfile);
  vi.stubGlobal('AppIcon', ({ className }: { className?: string }) => <i className={className} />);
});
afterAll(() => vi.unstubAllGlobals());

it('selects the first module and replaces one detail panel without moving navigation or writing/fetching', async () => {
  render(<Harness />);
  const nav = screen.getByRole('navigation', { name: 'Selected modules' });
  const buttons = within(nav).getAllByRole('button');
  const groups = allocatePcpQuestions(pcpProfile, pcpModules());
  expect(buttons).toHaveLength(5);
  expect(buttons[0]).toHaveAttribute('aria-pressed', 'true');
  expect(document.querySelectorAll('details, summary')).toHaveLength(0);
  expect(screen.queryByText('Earned Value Management (EVM)')).toBeNull();
  const displayedCodes = new Set<string>();
  for (const [index, button] of buttons.entries()) {
    await userEvent.click(button);
    expect(within(nav).getAllByRole('button', { pressed: true })).toEqual([button]);
    expect(within(nav).queryByRole('button', { name: /^Answer / })).toBeNull();
    expect(within(nav).getAllByRole('button')).toEqual(buttons);
    const panel = screen.getByRole('region', { name: `${groups[index].title} KSB questions` });
    expect(screen.getAllByRole('region', { name: /KSB questions$/ })).toHaveLength(1);
    expect(within(panel).getByText(`${groups[index].questions.length} KSBs`)).toBeVisible();
    expect(within(panel).getAllByRole('button', { name: /^Answer [KSB]\d+$/ })).toHaveLength(groups[index].questions.length);
    for (const question of groups[index].questions) {
      const original = source.ksbs.find((ksb) => ksb.code === question.codes[0])!;
      expect(within(panel).getByText(`${original.code} ${original.full_description}`)).toBeVisible();
      expect(within(nav).queryByText(`${original.code} ${original.full_description}`)).toBeNull();
      displayedCodes.add(original.code);
    }
  }
  expect(displayedCodes.size).toBe(71);
  expect(screen.getByText('0 of 71 answered')).toBeVisible();
  expect(mock.write).not.toHaveBeenCalled();
  expect(mock.fetch).not.toHaveBeenCalled();
});

it('reuses the existing 9-option popup with full text, note/evidence and a stable sequential next question', async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole('button', { name: 'Answer K1' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText('Question 1 of 71')).toBeVisible();
  expect(dialog.getByText(new RegExp(source.ksbs[0].full_description))).toBeVisible();
  expect(dialog.getAllByRole('radio')).toHaveLength(9);
  await userEvent.click(dialog.getByRole('radio', { name: /Champion/ }));
  expect(dialog.getByRole('button', { name: /Confirm & next/i })).toBeDisabled();
  await userEvent.type(dialog.getByLabelText(/Add a note/), 'Existing rating workflow');
  await userEvent.upload(screen.getByRole('dialog').querySelector('input[type="file"]')!, new File(['evidence'], 'evidence.txt', { type: 'text/plain' }));
  await userEvent.click(dialog.getByRole('button', { name: /Confirm & next/i }));
  expect(mock.write.mock.lastCall![0].assessments['st0845-v1-1-K1']).toMatchObject({ level: 'champion', note: 'Existing rating workflow', evidenceFiles: ['evidence.txt'] });
  expect(screen.getByText('Question 2 of 71')).toBeVisible();
});

it('keeps an answered redistributed KSB attached to its profile ID across module changes and restores per-module progress', async () => {
  const finance = source.ksbs.find((k) => k.original_owner_group_by_pathway.operational === 'FINANCE')!;
  const id = `st0845-v1-1-${finance.code}`;
  const answer: KsbAssessment = { ksbId: id, level: 'expert', note: 'Saved before regrouping', evidenceFiles: ['historical.pdf'], actionPlan: { text: 'Keep plan' } };
  const saved = { [id]: answer, 'old-profile-id': { ...answer, ksbId: 'old-profile-id' } };
  const firstState = pcpModules();
  const view = render(<Harness saved={saved} modules={firstState} />);
  const owner = allocatePcpQuestions(pcpProfile, firstState).find((group) => group.questions.some((ksb) => ksb.id === id))!;
  const moduleButton = within(screen.getByRole('navigation', { name: 'Selected modules' })).getByRole('button', { name: owner.title });
  await userEvent.click(moduleButton);
  expect(moduleButton).toHaveTextContent(`1 of ${owner.questions.length} answered`);
  await userEvent.click(screen.getByRole('button', { name: `Edit ${finance.code}` }));
  expect(screen.getByLabelText(/Add a note/)).toHaveValue(answer.note);
  expect(within(screen.getByRole('dialog')).getByText('historical.pdf')).toBeVisible();
  view.rerender(<Harness saved={saved} modules={pcpModules('operational', ['earned-value', 'pmi-sp'])} />);
  expect(within(screen.getByRole('dialog')).getByLabelText(/Add a note/)).toHaveValue(answer.note);
  expect(within(screen.getByRole('dialog')).getByText((text) => text.includes(finance.full_description))).toBeVisible();
  expect(screen.getByText('1 of 71 answered')).toBeVisible();
  await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
  expect(mock.write).not.toHaveBeenCalled();
});

it.each(['portfolio', 'earned-value'])('renders the four Chartered child modules and only selected %s optional', (optional) => {
  render(<Harness modules={pcpModules('chartered', [optional])} />);
  expect(within(screen.getByRole('navigation', { name: 'Selected modules' })).getAllByRole('button')).toHaveLength(6);
  expect(screen.queryByText('Certified PMO Professional Level 6')).toBeNull();
  expect(screen.getByText(optional === 'portfolio' ? 'Portfolio Management (APMG)' : 'Earned Value Management (APMG)')).toBeVisible();
  expect(screen.queryByText(optional === 'portfolio' ? 'Earned Value Management (APMG)' : 'Portfolio Management (APMG)')).toBeNull();
});

it('reports missing profile codes and never falls back to bare-code questions or permits answers before saved module selection', () => {
  mock.profile = pcpProfile.filter((ksb) => ksb.codes[0] !== 'K6');
  const view = render(<Harness />);
  expect(screen.getByRole('alert')).toHaveTextContent('K6');
  expect(screen.queryByRole('button', { name: /Answer K/ })).toBeNull();
  view.rerender(<Harness modules={{ ...pcpModules(), selectionComplete: false }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Complete and save your PCP module selection');
});

it('keeps the learner assessment read-only for staff review', async () => {
  render(<Harness readOnly />);
  await userEvent.click(screen.getByRole('button', { name: 'View K1' }));
  const dialog = within(screen.getByRole('dialog'));
  for (const radio of dialog.getAllByRole('radio')) expect(radio).toBeDisabled();
  expect(dialog.getByLabelText(/Add a note/)).toHaveAttribute('readonly');
  expect(mock.write).not.toHaveBeenCalled();
});

it('highlights the next module when Confirm & Next crosses its boundary and updates both progress counts', async () => {
  const groups = allocatePcpQuestions(pcpProfile, pcpModules());
  const last = groups[0].questions.at(-1)!;
  const next = groups[1].questions[0];
  render(<Harness />);
  const nav = screen.getByRole('navigation', { name: 'Selected modules' });
  const firstButton = within(nav).getByRole('button', { name: groups[0].title });
  const secondButton = within(nav).getByRole('button', { name: groups[1].title });
  await userEvent.click(screen.getByRole('button', { name: `Answer ${last.codes[0]}` }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByText('Question 16 of 71')).toBeVisible();
  await userEvent.click(dialog.getByRole('radio', { name: /Never/ }));
  await userEvent.click(dialog.getByRole('button', { name: /Confirm & next/i }));
  expect(screen.getByText('Question 17 of 71')).toBeVisible();
  expect(within(screen.getByRole('dialog')).getByText((text) => text.includes(next.title))).toBeVisible();
  expect(secondButton).toHaveAttribute('aria-pressed', 'true');
  expect(firstButton).toHaveAttribute('aria-pressed', 'false');
  expect(firstButton).toHaveTextContent('1 of 16 answered');
  expect(screen.getByText('1 of 71 answered')).toBeVisible();
  expect(mock.write).toHaveBeenCalledTimes(1);
  expect(mock.write.mock.lastCall![0].assessments[last.id]).toMatchObject({ ksbId: last.id, level: 'never' });
  await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('region', { name: `${groups[1].title} KSB questions` })).toBeVisible();
});

it('continues at the first unanswered KSB and selects its module without an answer write', async () => {
  const groups = allocatePcpQuestions(pcpProfile, pcpModules());
  const saved = Object.fromEntries(groups[0].questions.map((ksb) => [ksb.id, { ksbId: ksb.id, level: 'never' as const, note: '', evidenceFiles: [], actionPlan: null }]));
  render(<Harness saved={saved} />);
  const button = within(screen.getByRole('navigation', { name: 'Selected modules' })).getByRole('button', { name: groups[1].title });
  await userEvent.click(screen.getByRole('button', { name: 'Continue assessment' }));
  expect(screen.getByText('Question 17 of 71')).toBeVisible();
  expect(button).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('16 of 71 answered')).toBeVisible();
  expect(mock.write).not.toHaveBeenCalled();
});
