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
const defaultRule = { minimumQualifyingRating: 5, maximumQualifyingRating: 9, minimumHighRatedHardKsbs: 2 };
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
it('keeps the original table and saved answers while filtering one category at a time', () => {
  const writes = vi.fn(); render(<Harness writes={writes} />);
  expect(screen.getByText('1 of 3')).toBeVisible();
  expect(screen.getByText('KSBs answered')).toBeVisible();
  expect(screen.queryByRole('searchbox')).toBeNull();
  expect(screen.queryByText(/All KSBs/)).toBeNull();
  expect(screen.getByRole('tab', { name: 'Knowledge' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: 'Edit K1' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Assess S1' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Assess B1' })).toBeNull();
  const table = within(screen.getByRole('table', { name: 'Skills assessments' }));
  expect(table.getAllByRole('columnheader')).toHaveLength(3);
  expect(table.getAllByRole('row')).toHaveLength(2);
  const coverage = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  expect(coverage.queryByRole('button')).toBeNull();
  expect(coverage.getAllByText('Not available yet')).toHaveLength(2);
  expect(within(table.getByRole('button', { name: 'Edit K1' }).closest('[role=row]')!).getByText('Synthetic module A')).toBeVisible();
  expect(within(table.getByRole('button', { name: 'Edit K1' }).closest('[role=row]')!).getByText('Synthetic module B')).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(table.getAllByRole('row')).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Assess S1' })).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Behaviours' }));
  expect(screen.getByRole('button', { name: 'Assess B1' })).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Knowledge' }));
  expect(screen.getByText('1 of 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Edit K1' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.queryByLabelText(/Add a note/)).toBeNull();
  expect(dialog.queryByText('saved.pdf')).toBeNull();
  expect(dialog.queryByText(/Upload evidence|Select file/)).toBeNull();
  fireEvent.click(dialog.getByRole('button', { name: 'Confirm' }));
  expect(writes).toHaveBeenCalledTimes(1);
  expect(writes.mock.calls[0][0].assessments[k.id]).toMatchObject({ level: 'never', note: 'Saved note', evidenceFiles: ['saved.pdf'], actionPlan: { text: 'Keep plan' } });
  expect(Object.keys(writes.mock.calls[0][0].assessments)).toEqual([k.id]);
});
it('explains screening without exposing internal weights and supports keyboard category navigation', async () => {
  const writes = vi.fn();
  render(<Harness writes={writes} />);
  expect(screen.getByText('The Skills Radar helps us understand the knowledge, skills and behaviours (KSBs) you already have from previous education, training and work experience. Your answers help us identify prior learning and tailor your apprenticeship training plan.')).toBeVisible();
  expect(screen.getByText('Your answers may indicate prior learning for an assessor to review. This is not an RPL decision.')).toBeVisible();
  expect(screen.queryByText(/Hard KSB mappings|weight|threshold|%/i)).toBeNull();
  const control = screen.getByRole('tab', { name: 'Knowledge' });
  expect(control).toHaveAttribute('aria-selected', 'true');
  const user = userEvent.setup();
  control.focus();
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('tab', { name: 'Skills' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: 'Assess S1' })).toBeVisible();
  await user.keyboard('{End}');
  expect(screen.getByRole('tab', { name: 'Behaviours' })).toHaveAttribute('aria-selected', 'true');
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

it('sorts each category numerically while keeping global numbers, answers and malformed codes attached', () => {
  const item = (code: string, kind: string) => ({ id: `question-${code}`, codes: [code],
    title: `${code} full description`, kind, theme: 'Published KSB' });
  const questions = [item('S10', 'Skill'), item('K10', 'Knowledge'), item('B10', 'Behaviour'),
    item('K2', 'Knowledge'), item('S2', 'Skill'), item('B2', 'Behaviour'), k,
    s, b, item('KX', 'Knowledge'), { id: 'missing-code', codes: [], title: 'Missing code',
      kind: 'Knowledge', theme: 'Published KSB' }];
  const writes = vi.fn();
  render(<Harness writes={writes} questions={questions}
    moduleGroups={[{ id: 'published', title: 'Published module', questions }]} />);
  const table = screen.getByRole('table', { name: 'Skills assessments' });
  const descriptions = () => within(table).getAllByRole('row').slice(1)
    .map((row) => within(row).getAllByRole('cell')[0].textContent ?? '');
  expect(descriptions().map((text) => text.match(/^\d+\./)?.[0])).toEqual(['7.', '4.', '2.', '10.', '11.']);
  expect(descriptions()).toEqual(expect.arrayContaining([
    expect.stringContaining('K1 Shared knowledge question'),
    expect.stringContaining('K2 K2 full description'),
    expect.stringContaining('K10 K10 full description'),
  ]));
  expect(descriptions()[3]).toContain('KX KX full description');
  expect(descriptions()[4]).toContain('Missing code');
  const savedRow = within(table).getByRole('button', { name: 'Edit K1' }).closest<HTMLElement>('[role=row]')!;
  expect(savedRow).toHaveTextContent('Published module');
  expect(savedRow).toHaveTextContent('1');
  fireEvent.click(within(savedRow).getByRole('button', { name: 'Edit K1' }));
  expect(screen.getByRole('dialog')).toHaveTextContent('Question 7 of 11');
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm & next' }));
  expect(screen.getByRole('dialog')).toHaveTextContent('Question 4 of 11');
  expect(writes).toHaveBeenCalledTimes(1);
  expect(writes.mock.calls[0][0].assessments[k.id]).toMatchObject({
    ksbId: k.id, level: 'never', note: 'Saved note', evidenceFiles: ['saved.pdf'],
  });
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(descriptions().map((text) => text.match(/^\d+\./)?.[0])).toEqual(['8.', '5.', '1.']);
  expect(descriptions().map((text) => text.match(/S\d+/)?.[0])).toEqual(['S1', 'S2', 'S10']);
  fireEvent.click(screen.getByRole('tab', { name: 'Behaviours' }));
  expect(descriptions().map((text) => text.match(/^\d+\./)?.[0])).toEqual(['9.', '6.', '3.']);
  expect(descriptions().map((text) => text.match(/B\d+/)?.[0])).toEqual(['B1', 'B2', 'B10']);
  fireEvent.click(screen.getByRole('tab', { name: 'Knowledge' }));
  expect(within(table).getByRole('button', { name: 'Edit K1' })).toBeVisible();
  expect(writes).toHaveBeenCalledTimes(1);
});

it('renders zero, one or many selected modules without decorative card icons', () => {
  for (const count of [0, 1, 3, 5, 7]) {
    const selected = Array.from({ length: count }, (_, index) => ({
      id: `selected-${index}`, title: `Selected module ${index + 1}`, questions: [k],
    }));
    const view = render(<Harness writes={vi.fn()} moduleGroups={selected} />);
    const cards = screen.queryAllByRole('article');
    expect(cards).toHaveLength(count);
    expect(screen.getByRole('tab', { name: 'Knowledge' })).toBeVisible();
    expect(screen.getByRole('table', { name: 'Skills assessments' })).toBeVisible();
    cards.forEach((card, index) => {
      expect(within(card).getByText(selected[index].title)).toBeVisible();
      expect(card.querySelector('svg')).toBeNull();
    });
    view.unmount();
  }
});

it('shows count-based statuses for a KSB shared by two modules without learner percentages', async () => {
  const entry = (id: string, code: string, weight: string) => ({ id, code, description: code,
    kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: weight, hard_weight: weight, hard_count: 1, mapping_count: 1 } });
  const impact = { status: 'ready', programmeId: 'PCP_L6', ...defaultRule,
    savedLevels: { [k.id]: 'champion', [s.id]: 'consistently', [b.id]: 'never' }, modules: [
      { id: 'a', name: 'Synthetic module A', status: 'ready', revisionId: 'revision-a', ksbs: [entry('k', 'K1', '40'), entry('s', 'S1', '20')] },
      { id: 'b', name: 'Synthetic module B', status: 'ready', revisionId: 'revision-b', ksbs: [entry('k', 'K1', '20'), entry('b', 'B1', '40')] },
    ] };
  render(<Harness writes={vi.fn()} impact={impact} />);
  const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  expect(await cards.findByText('Potential Prior Learning')).toBeVisible();
  expect(cards.getByText('No indication yet')).toBeVisible();
  expect(cards.queryByRole('meter')).toBeNull();
  expect(cards.queryByText(/\d+%|Hard|weight|KSBs assessed/)).toBeNull();
  expect(screen.getAllByRole('row')).toHaveLength(2);
  expect(cards.queryByText(/Any reduction in learning \(RPL\)/)).toBeNull();
  const sharedKsbRow = screen.getByRole('button', { name: 'Edit K1' }).closest<HTMLElement>('[role=row]')!;
  expect(within(sharedKsbRow).getByText('Synthetic module A')).toBeVisible();
  expect(within(sharedKsbRow).getByText('Synthetic module B')).toBeVisible();
  expect(sharedKsbRow).not.toHaveTextContent(/module weight|weight unavailable|\d+%/i);
  expect(sharedKsbRow.querySelector('[title*="weight"]')).toBeNull();
});
it('recalculates the indication when saved ratings change without counting the same KSB twice', () => {
  const entry = (id: string, code: string) => ({ id, code, description: code,
    kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: '80', hard_weight: '80', hard_count: 2, mapping_count: 2 } });
  const module = { id: 'a', name: 'Synthetic module A', status: 'ready', revisionId: 'revision-a',
    ksbs: [entry('k', 'K1'), entry('s', 'S1')] };
  const base = { status: 'ready', programmeId: 'PCP_L6', ...defaultRule,
    modules: [module] };
  const view = render(<Harness writes={vi.fn()} impact={{ ...base,
    savedLevels: { [k.id]: 'champion', [s.id]: 'rarely' } }} />);
  const card = within(screen.getByRole('region', { name: 'Your prior knowledge by module' })).getAllByRole('article')[0];
  expect(card).toHaveTextContent('No indication yet');
  view.rerender(<Harness writes={vi.fn()} impact={{ ...base,
    savedLevels: { [k.id]: 'champion', [s.id]: 'consistently' } }} />);
  expect(card).toHaveTextContent('Potential Prior Learning');
  view.rerender(<Harness writes={vi.fn()} impact={{ ...base,
    savedLevels: { [k.id]: 'champion', [s.id]: 'rarely' } }} />);
  expect(card).toHaveTextContent('No indication yet');
});
it('keeps Soft-only KSBs assessed without exposing classification in learner cards', () => {
  const entry = (id: string, code: string, hard: string, total: string) => ({
    id, code, description: code, kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: total, hard_weight: hard, hard_count: Number(hard) > 0 ? 1 : 0, mapping_count: 1 },
  });
  const impact = { status: 'ready', programmeId: 'PCP_L6', ...defaultRule, savedLevels: { [k.id]: 'never' }, modules: [
    { id: 'a', name: 'Synthetic module A', status: 'ready', revisionId: 'revision-a',
      ksbs: [entry('k', 'K1', '40', '40'), entry('s', 'S1', '0', '20')] },
    { id: 'b', name: 'Synthetic module B', status: 'unavailable', ksbs: [] },
  ] };
  render(<Harness writes={vi.fn()} impact={impact} />);
  const first = within(screen.getByRole('region', { name: 'Your prior knowledge by module' })).getAllByRole('article')[0];
  expect(first).toHaveTextContent('No indication yet');
  expect(first).not.toHaveTextContent(/Hard|Soft|weight|\d+%/);
  expect(within(screen.getByRole('table', { name: 'Skills assessments' })).getAllByRole('row')).toHaveLength(2);
  fireEvent.click(screen.getByRole('tab', { name: 'Skills' }));
  expect(screen.getByRole('button', { name: 'Assess S1' })).toBeVisible();
});
it('keeps an unmapped module and its answers visible without inventing a score', () => {
  const impact = { status: 'ready', programmeId: 'PCP_L6', ...defaultRule, modules: [
    { id: 'a', name: 'Synthetic module A', status: 'unavailable', reason: 'module_mapping_unavailable', ksbs: [] },
    { id: 'b', name: 'Synthetic module B', status: 'unavailable', reason: 'module_mapping_unavailable', ksbs: [] },
  ] };
  render(<Harness writes={vi.fn()} impact={impact} />);
  const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  expect(cards.getAllByText('Not available yet')).toHaveLength(2);
  expect(cards.queryByRole('meter')).toBeNull();
  expect(within(screen.getByRole('table', { name: 'Skills assessments' })).getAllByRole('row')).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Edit K1' })).toBeVisible();
});
it('shows one non-decisional flag at the configured count in learner and staff cards', () => {
  const entry = (id: string, code: string, weight: string) => ({ id, code, description: code,
    kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: weight, hard_weight: weight, hard_count: 1, mapping_count: 1 } });
  const base = { status: 'ready', programmeId: 'PCP_L6', ...defaultRule,
    savedLevels: { [k.id]: 'champion', [s.id]: 'consistently', [b.id]: 'never' }, modules: [
      { id: 'a', name: 'Synthetic module A', status: 'ready', revisionId: 'revision-a', ksbs: [entry('k', 'K1', '40'), entry('s', 'S1', '20')] },
      { id: 'b', name: 'Synthetic module B', status: 'ready', revisionId: 'revision-b', ksbs: [entry('k', 'K1', '20'), entry('b', 'B1', '40')] },
    ] };
  for (const [threshold, count] of [[2, 1], [1, 2], [3, 0]]) {
    const view = render(<Harness writes={vi.fn()} impact={{ ...base, minimumHighRatedHardKsbs: threshold }} />);
    const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
    expect(cards.queryAllByText('Potential Prior Learning')).toHaveLength(count);
    expect(cards.queryByText(/\d+%|Hard|weight|threshold/)).toBeNull();
    view.unmount();
  }
  render(<Harness learner={false} writes={vi.fn()} impact={{ ...base, minimumHighRatedHardKsbs: 2 }} />);
  expect(screen.getByText('Potential Prior Learning')).toBeVisible();
  expect(screen.getByText(/This is not an RPL decision/)).toBeVisible();
});
it('uses the saved 6–8 bounds and count 3 in learner and staff cards without exposing the rule', () => {
  const entry = (id: string, code: string) => ({ id, code, description: code,
    kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: '40', hard_weight: '40', hard_count: 1, mapping_count: 1 } });
  const impact = { status: 'ready', programmeId: 'PCP_L6', minimumQualifyingRating: 6,
    maximumQualifyingRating: 8, minimumHighRatedHardKsbs: 3,
    savedLevels: { [k.id]: 'proficient', [s.id]: 'expert', [b.id]: 'mastery' },
    modules: [{ id: 'a', name: 'Synthetic module A', status: 'ready', revisionId: 'revision-a',
      ksbs: [entry('k', 'K1'), entry('s', 'S1'), entry('b', 'B1')] }] };
  const learner = render(<Harness writes={vi.fn()} impact={impact} />);
  const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  expect(cards.getByText('Potential Prior Learning')).toBeVisible();
  expect(cards.queryByText(/6–8|3 distinct|Hard KSB|threshold/)).toBeNull();
  learner.unmount();
  render(<Harness learner={false} writes={vi.fn()} impact={impact} />);
  expect(screen.getByText('Potential Prior Learning')).toBeVisible();
});
it('shows indicated modules before unavailable modules without inventing a percentage', () => {
  const entry = (id: string, code: string, weight: string) => ({ id, code, description: code,
    kind: 'Knowledge', standard: 'ST0001', standardVersion: '1.0',
    weight: { total_weight: weight, hard_weight: weight, hard_count: 1, mapping_count: 1 } });
  const impact = { status: 'ready', programmeId: 'PCP_L6', ...defaultRule,
    savedLevels: { [k.id]: 'champion', [b.id]: 'consistently' }, modules: [
      { id: 'a', name: 'Synthetic module A', status: 'unavailable', reason: 'module_mapping_unavailable', ksbs: [] },
      { id: 'b', name: 'Synthetic module B', status: 'ready', revisionId: 'revision-b', ksbs: [entry('k', 'K1', '20'), entry('b', 'B1', '40')] },
    ] };
  render(<Harness writes={vi.fn()} impact={impact} />);
  const cards = within(screen.getByRole('region', { name: 'Your prior knowledge by module' }));
  const articles = cards.getAllByRole('article');
  expect(within(articles[0]).getByText('Synthetic module B')).toBeVisible();
  expect(within(articles[0]).getByText('Potential Prior Learning')).toBeVisible();
  expect(within(articles[1]).getByText('Synthetic module A')).toBeVisible();
  expect(within(articles[1]).getByText('Not available yet')).toBeVisible();
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
  expect(screen.getByRole('tab', { name: 'Knowledge' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('1 of 3')).toBeVisible();
  expect(screen.getAllByRole('button', { name: /^View [KSB]1$/ })).toHaveLength(1);
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
