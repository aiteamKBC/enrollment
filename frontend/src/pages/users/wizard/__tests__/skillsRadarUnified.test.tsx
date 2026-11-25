import { useState } from 'react';
import { render, screen, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { WizardContext } from '@/pages/users/wizard/WizardContext.helpers';
import { LearnerEnrolmentVisualContext } from '@/features/learner-enrolment/enrolmentPresentation';
import { LearnerStepContent } from '@/features/learner-enrolment/LearnerStepContent';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import { AppIcon } from '@/components/feature/AppIcon';
vi.stubGlobal('AppIcon', AppIcon);
vi.mock('@/api/curriculum', () => ({ peekKsbProfile: () => null, fetchKsbProfile: vi.fn() }));
const k = { id: 'shared-k1', codes: ['K1'], title: 'Shared knowledge question', kind: 'Knowledge', theme: 'Existing knowledge' };
const s = { id: 'unique-s1', codes: ['S1'], title: 'Module A skill', kind: 'Skill', theme: 'Existing skills' };
const b = { id: 'unique-b1', codes: ['B1'], title: 'Module B behaviour', kind: 'Behaviour', theme: 'Existing behaviours' };
const groups = [{ id: 'a', title: 'Synthetic module A', questions: [k, s] }, { id: 'b', title: 'Synthetic module B', questions: [k, b] }];
function Harness({ learner = true, writes, impact, impactError, retry, questions, moduleGroups }: {
  learner?: boolean; writes: (data: unknown) => void; impact?: any; impactError?: string; retry?: () => void;
  questions?: Array<typeof k>; moduleGroups?: typeof groups;
}) {
  const [draft, update] = useState({ skillsRadar: { standardId: 'standard:st0001-v1-0', assessments: { [k.id]: { ksbId: k.id, level: 'never', note: 'Saved note', evidenceFiles: ['saved.pdf'], actionPlan: { text: 'Keep plan' } } } } });
  const value: any = { draft, readOnly: !learner, userId: '41', isCommercial: false, ilrSavedAt: '', radarImpact: impact,
    radarImpactError: impactError, retryRadarImpact: retry,
    modules: { data: { selectedProgramme: 'PCP_L6', configuration: { programmes: [] } } }, board: { programme: { name: 'Synthetic Programme' } },
    radarCatalogue: { enabled: true, loading: false, data: { programmeId: 'PCP_L6', catalogue: {} }, grouping: { status: 'ready', modules: moduleGroups ?? groups, questions: questions ?? [k, s, k, b] } },
    setSection: (section, data) => { writes(data); update(old => ({ ...old, [section]: data })); },
  };
  return <WizardContext.Provider value={value}><LearnerEnrolmentVisualContext.Provider value={learner}><LearnerStepContent slug="skills-radar"><SkillsRadar /></LearnerStepContent></LearnerEnrolmentVisualContext.Provider></WizardContext.Provider>;
}
it('keeps the full learner list and stable saved answers without search or category filters', () => {
  const writes = vi.fn(); render(<Harness writes={writes} />);
  expect(screen.getByText('1 of 3')).toBeVisible();
  expect(screen.getByText('KSBs answered')).toBeVisible();
  expect(screen.queryByRole('searchbox')).toBeNull();
  expect(screen.queryByText(/All KSBs/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Edit K1' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Assess S1' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Assess B1' })).toBeVisible();
  const table = within(screen.getByRole('table', { name: 'Skills assessments' }));
  expect(table.getAllByRole('columnheader')).toHaveLength(3);
  expect(table.getAllByRole('row')).toHaveLength(4);
  const coverage = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  expect(coverage.getAllByRole('button')).toHaveLength(1);
  expect(coverage.getByRole('button', { name: 'About your scores' })).toBeVisible();
  expect(coverage.getAllByText('1 of 2 KSBs assessed')).toHaveLength(2);
  expect(within(table.getByRole('button', { name: 'Edit K1' }).closest('[role=row]')!).getByText('Synthetic module A')).toBeVisible();
  expect(within(table.getByRole('button', { name: 'Edit K1' }).closest('[role=row]')!).getByText('Synthetic module B')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Edit K1' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.queryByLabelText(/Add a note/)).toBeNull();
  expect(dialog.queryByText('saved.pdf')).toBeNull();
  expect(dialog.queryByText(/Upload evidence|Select file/)).toBeNull();
  fireEvent.click(dialog.getByRole('button', { name: /Confirm & next/ }));
  expect(writes).toHaveBeenCalledTimes(1);
  expect(writes.mock.calls[0][0].assessments[k.id]).toMatchObject({ level: 'never', note: 'Saved note', evidenceFiles: ['saved.pdf'], actionPlan: { text: 'Keep plan' } });
  expect(Object.keys(writes.mock.calls[0][0].assessments)).toEqual([k.id]);
});
it('explains the learner assessment and offers a keyboard-accessible, optional score explanation', async () => {
  const writes = vi.fn();
  render(<Harness writes={writes} />);
  expect(screen.getByText('The Skills Radar helps us understand the knowledge, skills and behaviours (KSBs) you already have from previous education, training and work experience. Your answers help us identify prior learning and tailor your apprenticeship training plan.')).toBeVisible();
  expect(screen.getByText('Module scores reflect self-assessed prior knowledge, weighted by the KSBs covered in each module.')).toBeVisible();
  const control = screen.getByRole('button', { name: 'About your scores' });
  const explanation = screen.getByText(/Module scores are indicative and based on self-assessment/);
  expect(control).toHaveAttribute('aria-controls', 'skills-radar-score-help');
  expect(control).toHaveAttribute('aria-expanded', 'false');
  expect(explanation).not.toBeVisible();
  const user = userEvent.setup();
  control.focus();
  await user.keyboard('{Enter}');
  expect(control).toHaveAttribute('aria-expanded', 'true');
  expect(explanation).toBeVisible();
  expect(explanation).toHaveTextContent('They do not represent the percentage of training that can automatically be removed.');
  await user.keyboard(' ');
  expect(control).toHaveAttribute('aria-expanded', 'false');
  expect(explanation).not.toBeVisible();
  expect(writes).not.toHaveBeenCalled();
});
it('renders all 33 published KSB rows in their original order', () => {
  const questions = [k, ...Array.from({ length: 32 }, (_, index) => ({
    id: `profile-K${index + 2}`, codes: [`K${index + 2}`],
    title: `Knowledge ${index + 2}`, kind: 'Knowledge', theme: 'Existing knowledge',
  }))];
  render(<Harness writes={vi.fn()} questions={questions}
    moduleGroups={[{ id: 'many', title: 'Assessment module', questions }]} />);
  const table = within(screen.getByRole('table', { name: 'Skills assessments' }));
  expect(table.getAllByRole('row')).toHaveLength(34);
  expect(table.getAllByRole('row')[1]).toHaveTextContent('K1 Shared knowledge question');
  expect(table.getAllByRole('row')[33]).toHaveTextContent('K33 Knowledge 33');
  expect(screen.getByText('1 of 33')).toBeVisible();
  expect(screen.queryByRole('searchbox')).toBeNull();
});

it('renders the selected module count dynamically without decorative card icons', () => {
  for (const count of [2, 3, 4, 5]) {
    const selected = Array.from({ length: count }, (_, index) => ({
      id: `selected-${index}`, title: `Selected module ${index + 1}`, questions: [k],
    }));
    const view = render(<Harness writes={vi.fn()} moduleGroups={selected} />);
    const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' })).getAllByRole('article');
    expect(cards).toHaveLength(count);
    cards.forEach((card, index) => {
      expect(within(card).getByText(selected[index].title)).toBeVisible();
      expect(card.querySelector('svg')).toBeNull();
    });
    view.unmount();
  }
});

it('shows different saved weighted readiness for a KSB shared by two modules', async () => {
  const entry = (id: string, code: string, weight: string) => ({ id, code, description: code,
    kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: weight, mapping_count: 1 } });
  const impact = { status: 'ready', programmeId: 'PCP_L6',
    savedLevels: { [k.id]: 'champion', [s.id]: 'never', [b.id]: 'never' }, modules: [
      { id: 'a', name: 'Synthetic module A', status: 'ready', revisionId: 'revision-a', ksbs: [entry('k', 'K1', '40'), entry('s', 'S1', '20')] },
      { id: 'b', name: 'Synthetic module B', status: 'ready', revisionId: 'revision-b', ksbs: [entry('k', 'K1', '20'), entry('b', 'B1', '40')] },
    ] };
  render(<Harness writes={vi.fn()} impact={impact} />);
  const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  expect(await cards.findByLabelText('Prior knowledge 67 percent')).toBeVisible();
  expect(cards.getByLabelText('Prior knowledge 33 percent')).toBeVisible();
  expect(cards.getAllByRole('meter')).toHaveLength(2);
  expect(screen.getAllByRole('row')).toHaveLength(4);
  expect(cards.getAllByText('Prior knowledge')).toHaveLength(2);
  expect(cards.queryByText(/Any reduction in learning \(RPL\)/)).toBeNull();
  const sharedKsbRow = screen.getByRole('button', { name: 'Edit K1' }).closest<HTMLElement>('[role=row]')!;
  expect(within(sharedKsbRow).getByText('Synthetic module A')).toBeVisible();
  expect(within(sharedKsbRow).getByText('Synthetic module B')).toBeVisible();
  expect(sharedKsbRow).not.toHaveTextContent(/module weight|weight unavailable|\d+%/i);
  expect(sharedKsbRow.querySelector('[title*="weight"]')).toBeNull();
});
it('keeps an unmapped module and its answers visible without inventing a score', () => {
  const impact = { status: 'ready', programmeId: 'PCP_L6', modules: [
    { id: 'a', name: 'Synthetic module A', status: 'unavailable', reason: 'module_mapping_unavailable', ksbs: [] },
    { id: 'b', name: 'Synthetic module B', status: 'unavailable', reason: 'module_mapping_unavailable', ksbs: [] },
  ] };
  render(<Harness writes={vi.fn()} impact={impact} />);
  const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  expect(cards.getAllByText('Score unavailable')).toHaveLength(2);
  expect(cards.getAllByText('1 of 2 KSBs assessed')).toHaveLength(2);
  expect(cards.queryByText('Prior knowledge')).toBeNull();
  expect(cards.queryByRole('meter')).toBeNull();
  expect(within(screen.getByRole('table', { name: 'Skills assessments' })).getAllByRole('row')).toHaveLength(4);
  expect(screen.getByRole('button', { name: 'Edit K1' })).toBeVisible();
});
it('shows scored modules before unavailable modules without changing their scores', () => {
  const entry = (id: string, code: string, weight: string) => ({ id, code, description: code,
    kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: weight, mapping_count: 1 } });
  const impact = { status: 'ready', programmeId: 'PCP_L6',
    savedLevels: { [k.id]: 'champion', [b.id]: 'never' }, modules: [
      { id: 'a', name: 'Synthetic module A', status: 'unavailable', reason: 'module_mapping_unavailable', ksbs: [] },
      { id: 'b', name: 'Synthetic module B', status: 'ready', ksbs: [entry('k', 'K1', '20'), entry('b', 'B1', '40')] },
    ] };
  render(<Harness writes={vi.fn()} impact={impact} />);
  const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  const articles = cards.getAllByRole('article');
  expect(within(articles[0]).getByText('Synthetic module B')).toBeVisible();
  expect(within(articles[0]).getByLabelText('Prior knowledge 33 percent')).toBeVisible();
  expect(within(articles[1]).getByText('Synthetic module A')).toBeVisible();
  expect(within(articles[1]).getByText('Score unavailable')).toBeVisible();
});
it('puts a failed mapping request in one retryable error state', () => {
  const retry = vi.fn();
  render(<Harness writes={vi.fn()} impactError="The assessment request failed (404)." retry={retry} />);
  expect(screen.getByRole('alert')).toHaveTextContent('We couldn’t load module results right now. Your answers are still saved.');
  expect(screen.queryByText(/404/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(retry).toHaveBeenCalledOnce();
});
it('keeps the staff all-KSB coverage table unique and read-only', () => {
  const writes = vi.fn(); render(<Harness learner={false} writes={writes} />);
  expect(screen.getByText("The Skills Radar records the learner's self-assessed knowledge, skills and behaviours (KSBs). Their responses help inform the initial assessment, identify potential prior learning and support an individualised training plan.")).toBeVisible();
  expect(screen.getByRole('button', { name: 'About your scores' })).toBeVisible();
  expect(screen.getByText('1 of 3')).toBeVisible();
  expect(screen.getAllByRole('button', { name: /^View [KSB]1$/ })).toHaveLength(3);
  fireEvent.click(screen.getByRole('button', { name: 'View K1' }));
  within(screen.getByRole('dialog')).getAllByRole('radio').forEach(input => expect(input).toBeDisabled());
  expect(writes).not.toHaveBeenCalled();
});

it('restores saved ratings and notes when the learner list remounts without writing', () => {
  const writes = vi.fn();
  const first = render(<Harness writes={writes} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit K1' }));
  expect(within(screen.getByRole('dialog')).getByRole('radio', { name: /Never/ })).toBeChecked();
  first.unmount();
  render(<Harness writes={writes} />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit K1' }));
  expect(within(screen.getByRole('dialog')).queryByLabelText(/Add a note/)).toBeNull();
  expect(writes).not.toHaveBeenCalled();
});
