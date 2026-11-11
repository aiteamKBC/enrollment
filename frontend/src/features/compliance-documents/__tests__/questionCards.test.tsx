import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QuestionCards } from '../QuestionCards';
import { newQuestion, questionFields } from '../questionFields';
import { definitions } from '../fields';
import { contentFor } from './fixtures';
import type { DocumentBlock, DocumentKey, DocumentQuestion } from '../types';

function Harness({ initial, documentKey = 'ilr' }: { initial: DocumentBlock; documentKey?: DocumentKey }) {
  const [block, update] = useState(initial);
  return <><QuestionCards block={block} documentKey={documentKey} registry={Object.values(definitions(documentKey))} onChange={update} /><output data-testid="configuration">{JSON.stringify(block)}</output></>;
}
const saved = () => JSON.parse(screen.getByTestId('configuration').textContent!);
const custom = (fields: DocumentQuestion[] = [newQuestion()]): DocumentBlock => ({ id: 'custom-section', type: 'questions', fields });
const extended = () => contentFor('review-eligibility').blocks.find((b) => b.source === 'review:extendedIlr')!;
const article = (label: string) => screen.getByRole('article', { name: new RegExp(`Question \d+: ${label}`) });
beforeEach(() => vi.spyOn(window, 'confirm').mockReturnValue(true));

describe('configurable document question cards', () => {
  it('loads every existing Extended ILR question with its actual type and stable binding', async () => {
    const user = userEvent.setup(); const original = extended(); render(<Harness initial={original} documentKey="review-eligibility" />);
    expect(screen.getAllByRole('article')).toHaveLength(6); expect(screen.queryByText('Question wording 1')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /What evidence has been seen/ }));
    expect(screen.getByLabelText('Field type')).toHaveValue('multi_line'); expect(screen.getByLabelText('Field type')).toBeDisabled();
    expect(screen.getByLabelText('Question / Label')).toHaveValue(original.labels!.eligibilityEvidence);
    expect(saved()).toEqual(original); // Opening and expanding do not migrate storage.
  });
  it('edits wording and help with dynamic placeholders while preserving system source and answer identity', async () => {
    const user = userEvent.setup(); render(<Harness initial={extended()} documentKey="review-eligibility" />);
    await user.click(screen.getByRole('button', { name: /Does the apprentice have an eligible residency/ }));
    fireEvent.change(screen.getByLabelText('Question / Label'), { target: { value: 'Question for ' } });
    await user.selectOptions(screen.getByLabelText('Dynamic field for Question / Label'), 'learner.fullName');
    const inserts = screen.getAllByRole('button', { name: 'Insert field' }); await user.click(inserts[0]);
    fireEvent.change(screen.getByLabelText('Help text'), { target: { value: 'Supporting information' } });
    const field = saved().fields[0]; expect(field.label).toBe('Question for {{learner.fullName}}');
    expect(field.binding).toEqual({ kind: 'review', source: 'review:extendedIlr', key: 'eligibleResidency' });
    expect(field.helpText).toBe('Supporting information'); expect(saved().labels.eligibleResidency).toBe(field.label);
  });
  it('moves questions immediately with first/last boundaries and unchanged bindings', async () => {
    const user = userEvent.setup(); const initial = extended(); const original = questionFields(initial, 'review-eligibility');
    render(<Harness initial={initial} documentKey="review-eligibility" />);
    expect(screen.getByLabelText('Move question 1 up')).toBeDisabled(); expect(screen.getByLabelText('Move question 6 down')).toBeDisabled();
    await user.click(screen.getByLabelText('Move question 1 down'));
    expect(saved().fields[1].id).toBe(original[0].id); expect(saved().fields[1].binding).toEqual(original[0].binding);
    await user.click(screen.getByLabelText('Move question 2 up'));
    expect(saved().fields.map((f: DocumentQuestion) => f.id)).toEqual(original.map((f) => f.id));
  });
  it('explains and disables unsafe system type, required, conditional, duplicate and delete settings', async () => {
    const user = userEvent.setup(); render(<Harness initial={extended()} documentKey="review-eligibility" />);
    await user.click(screen.getByRole('button', { name: /Does the apprentice have an eligible residency/ }));
    expect(screen.getByText(/This field is system-linked/)).toBeInTheDocument();
    expect(screen.getByLabelText('Mandatory')).toBeDisabled();
    expect(screen.getByLabelText('Show only when a preceding field matches')).toBeDisabled();
    expect(screen.getAllByRole('button', { name: 'Duplicate' }).every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.getAllByRole('button', { name: 'Delete' }).every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
  });
  it.each(['dropdown', 'radio', 'checkbox'])('provides stable, editable and reorderable %s options and defaults', async (kind) => {
    const user = userEvent.setup(); render(<Harness initial={custom()} />);
    await user.selectOptions(screen.getByLabelText('Field type'), kind);
    const id = saved().fields[0].options[0].id;
    fireEvent.change(screen.getByLabelText('Option 1'), { target: { value: 'First choice' } });
    await user.click(screen.getByRole('button', { name: 'Add option' }));
    fireEvent.change(screen.getByLabelText('Option 2'), { target: { value: 'Second choice' } });
    await user.click(screen.getByLabelText('Default 1'));
    await user.click(screen.getByLabelText('Move option 1 down'));
    expect(saved().fields[0].options[1]).toEqual({ id, label: 'First choice' }); expect(saved().fields[0].defaultOptionIds).toEqual([id]);
    fireEvent.change(screen.getByLabelText('Option 2'), { target: { value: 'Renamed choice' } });
    expect(saved().fields[0].options[1].id).toBe(id);
    await user.click(screen.getByLabelText('Remove option 2')); expect(saved().fields[0].defaultOptionIds).toEqual([]);
  });
  it('supports ordinary types, configured values, mandatory and numeric bounds; upload is accurately disabled', async () => {
    const user = userEvent.setup(); render(<Harness initial={custom()} />);
    expect(within(screen.getByLabelText('Field type')).getByRole('option', { name: /File upload/ })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Field type'), 'number');
    fireEvent.change(screen.getByLabelText('Configured document value'), { target: { value: '42' } });
    fireEvent.change(screen.getByLabelText('Minimum'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('Maximum'), { target: { value: '100' } });
    await user.click(screen.getByLabelText(/Mandatory/));
    expect(saved().fields[0]).toMatchObject({ type: 'number', value: '42', min: 1, max: 100, required: true });
    await user.selectOptions(screen.getByLabelText('Field type'), 'description');
    expect(screen.queryByLabelText(/Mandatory/)).not.toBeInTheDocument(); expect(saved().fields[0].required).toBe(false);
  });
  it('adds, duplicates with a new identity, collapses and confirms deletion without touching other fields', async () => {
    const user = userEvent.setup(); render(<Harness initial={custom()} />);
    fireEvent.change(screen.getByLabelText('Question / Label'), { target: { value: 'Original question' } });
    await user.click(screen.getByRole('button', { name: 'Duplicate' }));
    expect(saved().fields).toHaveLength(2); expect(saved().fields[0].id).not.toBe(saved().fields[1].id);
    const second = screen.getAllByRole('article')[1];
    await user.click(within(second).getByRole('button', { name: /Original question.*Collapse/ }));
    expect(within(second).queryByLabelText('Question / Label')).not.toBeInTheDocument();
    vi.mocked(window.confirm).mockReturnValueOnce(false); await user.click(within(second).getByRole('button', { name: 'Delete' }));
    expect(saved().fields).toHaveLength(2);
    await user.click(within(second).getByRole('button', { name: 'Delete' })); expect(saved().fields).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Add Field' })); expect(saved().fields).toHaveLength(2);
  });
  it('persists conditions by stable option ID and warns when its controller is deleted', async () => {
    const user = userEvent.setup(); const source = { ...newQuestion(), id: 'source', label: 'Controlling question', type: 'radio' as const,
      options: [{ id: 'yes', label: 'Yes' }, { id: 'no', label: 'No' }] };
    const follow = { ...newQuestion(), label: 'Follow-up question' };
    render(<Harness initial={custom([source, follow])} />);
    await user.click(screen.getByRole('button', { name: /Follow-up question.*Expand/ }));
    await user.click(screen.getByLabelText('Show only when a preceding field matches'));
    await user.click(screen.getByLabelText('No'));
    expect(saved().fields[1].condition).toEqual({ field: 'source', values: ['no'] });
    await user.click(within(screen.getAllByRole('article')[0]).getByRole('button', { name: 'Delete' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Correct the conditional rule');
  });
});
