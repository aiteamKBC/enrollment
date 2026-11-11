import { useState } from 'react';
import { render, screen, within, fireEvent } from '@testing-library/react';
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
function Harness({ learner = true, writes }: { learner?: boolean; writes: (data: unknown) => void }) {
  const [draft, update] = useState({ skillsRadar: { assessments: { [k.id]: { ksbId: k.id, level: 'never', note: 'Saved note', evidenceFiles: ['saved.pdf'], actionPlan: { text: 'Keep plan' } } } } });
  const value: any = { draft, readOnly: !learner, board: { programme: { name: 'Synthetic Programme' } },
    radarCatalogue: { enabled: true, loading: false, data: { programmeId: 'PCP_L6', catalogue: {} }, grouping: { status: 'ready', modules: groups, questions: [k, s, k, b] } },
    setSection: (section, data) => { writes(data); update(old => ({ ...old, [section]: data })); },
  };
  return <WizardContext.Provider value={value}><LearnerEnrolmentVisualContext.Provider value={learner}><LearnerStepContent slug="skills-radar"><SkillsRadar /></LearnerStepContent></LearnerEnrolmentVisualContext.Provider></WizardContext.Provider>;
}
it('keeps a unified learner list with deduplicated coverage, category filters and stable saved answers', () => {
  const writes = vi.fn(); render(<Harness writes={writes} />);
  expect(screen.getByText('1 of 3 answered')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Edit K1' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Assess S1' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Assess B1' })).toBeVisible();
  const table = within(screen.getByRole('table', { name: 'Skills assessments' }));
  expect(table.getAllByRole('columnheader')).toHaveLength(3);
  expect(table.getAllByRole('row')).toHaveLength(4);
  const coverage = within(screen.getByRole('navigation', { name: 'Selected modules' }));
  expect(coverage.queryByRole('button')).toBeNull();
  expect(coverage.getAllByText('1 of 2 answered')).toHaveLength(2);
  expect(within(table.getByRole('button', { name: 'Edit K1' }).closest('[role=row]')!).getByText('Synthetic module A')).toBeVisible();
  expect(within(table.getByRole('button', { name: 'Edit K1' }).closest('[role=row]')!).getByText('Synthetic module B')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /^Knowledge/ }));
  expect(screen.queryByRole('button', { name: 'Assess S1' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Edit K1' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Assess B1' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Edit K1' }));
  const dialog = within(screen.getByRole('dialog'));
  expect(dialog.getByLabelText(/Add a note/)).toHaveValue('Saved note');
  expect(dialog.getByText('saved.pdf')).toBeVisible();
  fireEvent.click(dialog.getByRole('button', { name: /Confirm & next/ }));
  expect(writes).toHaveBeenCalledTimes(1);
  expect(writes.mock.calls[0][0].assessments[k.id]).toMatchObject({ level: 'never', note: 'Saved note', evidenceFiles: ['saved.pdf'], actionPlan: { text: 'Keep plan' } });
  expect(Object.keys(writes.mock.calls[0][0].assessments)).toEqual([k.id]);
});
it('keeps the staff all-KSB coverage table unique and read-only', () => {
  const writes = vi.fn(); render(<Harness learner={false} writes={writes} />);
  expect(screen.getByText('1 of 3 answered')).toBeVisible();
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
  expect(within(screen.getByRole('dialog')).getByLabelText(/Add a note/)).toHaveValue('Saved note');
  expect(writes).not.toHaveBeenCalled();
});
