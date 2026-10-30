/**
 * The "Edit apprenticeship wizard" builder: reachable from User Management,
 * edits a copy of the published layout, and publishes it in one go — adding
 * fields (with a dropdown-driven condition), reordering, making questions
 * optional or mandatory and removing them without losing their data.
 */
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
const success = vi.fn();
const error = vi.fn();
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success, error }) }));
const fetchWizardLayout = vi.fn();
const publishWizardLayout = vi.fn();
vi.mock('@/api/wizardLayout', () => ({
  fetchWizardLayout: (...args: unknown[]) => fetchWizardLayout(...args),
  publishWizardLayout: (...args: unknown[]) => publishWizardLayout(...args),
}));

import WizardBuilderPage from '../page';
import { addCustomField, moveItem, moveItemToStep, removeItem } from '../builderOps';
import { defaultLayout } from '../../wizard/layout/resolve';
import type { WizardLayout } from '../../wizard/layout/types';

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  fetchWizardLayout.mockResolvedValue({ layout: null, version: null, updatedAt: '', updatedBy: '' });
  publishWizardLayout.mockImplementation(async (layout: WizardLayout) => ({ layout, version: 1, updatedAt: '2026-10-03T10:00:00Z', updatedBy: 'Admin' }));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

const stepItems = (layout: WizardLayout, slug: string) => layout.steps.find((s) => s.slug === slug)!.items;

describe('layout edits', () => {
  it('moves an item past the next shown one, skipping removed items', () => {
    let layout = defaultLayout();
    layout = removeItem(layout, 'pd.lastName');
    layout = moveItem(layout, 'personal-details', 'pd.firstName', 1);
    const keys = stepItems(layout, 'personal-details').map((i) => i.key);
    expect(keys.slice(0, 3)).toEqual(['pd.email', 'pd.lastName', 'pd.firstName']);
  });

  it('hides a built-in or published field but drops one never published', () => {
    let layout = defaultLayout();
    const added = addCustomField(layout, 'cv-job', { label: 'Draft', type: 'text' });
    layout = removeItem(added.layout, added.key);
    expect(stepItems(layout, 'cv-job').some((i) => i.key === added.key)).toBe(false);

    layout = removeItem(layout, 'cv.experience');
    expect(stepItems(layout, 'cv-job').find((i) => i.key === 'cv.experience')!.hidden).toBe(true);
  });

  it('moves a block to another step', () => {
    const layout = moveItemToStep(defaultLayout(), 'block.policies', 'cv-job');
    expect(stepItems(layout, 'cv-job').at(-1)!.key).toBe('block.policies');
    expect(stepItems(layout, 'policies')).toEqual([]);
  });
});

function renderBuilder() {
  return render(
    <MemoryRouter initialEntries={['/users/wizard-builder']}>
      <Routes>
        <Route path="/users/wizard-builder" element={<WizardBuilderPage />} />
        <Route path="/users" element={<p>User directory</p>} />
      </Routes>
    </MemoryRouter>
  );
}

const openStep = async (name: string) => {
  const nav = await screen.findByRole('navigation', { name: 'Wizard steps' });
  await userEvent.click(within(nav).getByRole('button', { name }));
};

describe('the builder page', () => {
  it('starts from the standard wizard and cannot publish until something changes', async () => {
    renderBuilder();
    await openStep('Personal Details');
    expect(screen.getByText(/nothing has been published from the builder yet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Publish changes/ })).toBeDisabled();
    expect(screen.getByText('First Name')).toBeInTheDocument();
  });

  it('adds a dropdown and a follow-up shown for one answer, and publishes them', async () => {
    renderBuilder();
    await openStep('CV/Job Description');

    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    await userEvent.type(screen.getByLabelText('Question / label'), 'Shift pattern');
    await userEvent.selectOptions(screen.getByLabelText('Field type'), 'dropdown');
    await userEvent.type(screen.getByLabelText('Option 1'), 'Day');
    await userEvent.type(screen.getByLabelText('Option 2'), 'Night');
    await userEvent.click(screen.getByLabelText(/Mandatory — learners must answer/));
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));
    expect(screen.getByText('Shift pattern')).toBeInTheDocument();
    expect(screen.getByText(/New — adds a column when published/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    await userEvent.type(screen.getByLabelText('Question / label'), 'Night allowance');
    await userEvent.click(screen.getByLabelText(/Only show this field for certain answers/));
    await userEvent.selectOptions(screen.getByLabelText(/When this dropdown/), screen.getByRole('option', { name: /Shift pattern/ }));
    await userEvent.click(screen.getByLabelText('Night'));
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));
    expect(screen.getByText(/Shown when “Shift pattern” is Night/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    const dialog = screen.getByRole('dialog', { name: 'Publish the wizard?' });
    expect(within(dialog).getByText(/New database columns \(2\)/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /^Publish$/ }));

    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalledTimes(1));
    const [sent, baseVersion] = publishWizardLayout.mock.calls[0] as [WizardLayout, number | null];
    expect(baseVersion).toBeNull();
    const custom = stepItems(sent, 'cv-job').filter((i) => !i.builtin);
    expect(custom.map((i) => [i.label, i.type, i.required])).toEqual([
      ['Shift pattern', 'dropdown', true],
      ['Night allowance', 'text', false],
    ]);
    expect(custom[0].options).toEqual(['Day', 'Night']);
    expect(custom[1].condition).toEqual({ field: custom[0].key, values: ['Night'] });
    expect(success).toHaveBeenCalledWith('Wizard published', expect.any(String));
  });

  it('adds a follow-up question from an option’s checkbox, placed under the dropdown', async () => {
    renderBuilder();
    await openStep('CV/Job Description');

    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    await userEvent.type(screen.getByLabelText('Question / label'), 'Do you manage a team?');
    await userEvent.selectOptions(screen.getByLabelText('Field type'), 'dropdown');
    await userEvent.type(screen.getByLabelText('Option 1'), 'Yes');
    await userEvent.type(screen.getByLabelText('Option 2'), 'No');
    await userEvent.click(screen.getByRole('button', { name: 'Add option' }));
    await userEvent.type(screen.getByLabelText('Option 3'), 'Sometimes');

    const boxes = screen.getAllByLabelText('Ask a follow-up question when this is chosen');
    expect(boxes).toHaveLength(3);
    await userEvent.click(boxes[0]);
    // The follow-up's question is required before the field can be saved.
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));
    expect(screen.getByRole('alert')).toHaveTextContent('Write the follow-up question for each ticked option.');
    await userEvent.type(screen.getByLabelText(/Follow-up question for “Yes”/), 'How many people?');
    await userEvent.selectOptions(screen.getByLabelText('Answer type'), 'number');
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));
    expect(screen.getByText(/Shown when “Do you manage a team\?” is Yes/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalled());
    const sent = publishWizardLayout.mock.calls[0][0] as WizardLayout;
    const custom = stepItems(sent, 'cv-job').filter((i) => !i.builtin);
    expect(custom.map((i) => [i.label, i.type])).toEqual([['Do you manage a team?', 'dropdown'], ['How many people?', 'number']]);
    expect(custom[0].options).toEqual(['Yes', 'No', 'Sometimes']);
    expect(custom[1].condition).toEqual({ field: custom[0].key, values: ['Yes'] });
  });

  it('offers every field type for a follow-up, with its own options when it is a dropdown', async () => {
    renderBuilder();
    await openStep('CV/Job Description');
    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    await userEvent.type(screen.getByLabelText('Question / label'), 'Do you drive?');
    await userEvent.selectOptions(screen.getByLabelText('Field type'), 'dropdown');
    await userEvent.type(screen.getByLabelText('Option 1'), 'Yes');
    await userEvent.type(screen.getByLabelText('Option 2'), 'No');
    await userEvent.click(screen.getAllByLabelText('Ask a follow-up question when this is chosen')[0]);
    await userEvent.type(screen.getByLabelText(/Follow-up question for “Yes”/), 'Licence type');

    const type = screen.getByLabelText('Answer type');
    expect(within(type).getAllByRole('option').map((o) => o.textContent)).toEqual(['Text', 'Number', 'Dropdown', 'File upload']);
    await userEvent.selectOptions(type, 'dropdown');

    // A dropdown follow-up cannot be saved without choices of its own.
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));
    expect(screen.getByRole('alert')).toHaveTextContent('Add at least one option to each follow-up dropdown.');
    await userEvent.type(screen.getByLabelText('Follow-up option 1 for “Yes”'), 'Full');
    await userEvent.type(screen.getByLabelText('Follow-up option 2 for “Yes”'), 'Provisional');
    await userEvent.click(screen.getByRole('button', { name: 'Add follow-up option for “Yes”' }));
    await userEvent.type(screen.getByLabelText('Follow-up option 3 for “Yes”'), 'Motorcycle');
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));

    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalled());
    const custom = stepItems(publishWizardLayout.mock.calls[0][0] as WizardLayout, 'cv-job').filter((i) => !i.builtin);
    expect(custom[1]).toMatchObject({
      label: 'Licence type',
      type: 'dropdown',
      options: ['Full', 'Provisional', 'Motorcycle'],
      condition: { field: custom[0].key, values: ['Yes'] },
    });
  });

  it('shows a dropdown’s follow-ups ticked when it is edited, and unticking removes one', async () => {
    const layout = defaultLayout();
    const cv = layout.steps.find((s) => s.slug === 'cv-job')!;
    cv.items.push(
      { key: 'cf_team', builtin: false, hidden: false, label: 'Team?', type: 'dropdown', options: ['Yes', 'No'], table: 'Wizard_Cv_Job', column: 'Custom_team' },
      { key: 'cf_size', builtin: false, hidden: false, label: 'How many?', type: 'number', condition: { field: 'cf_team', values: ['Yes'] }, table: 'Wizard_Cv_Job', column: 'Custom_size' },
    );
    fetchWizardLayout.mockResolvedValue({ layout, version: 4, updatedAt: '', updatedBy: '' });
    renderBuilder();
    await openStep('CV/Job Description');
    await userEvent.click(screen.getByRole('button', { name: 'Edit Team?' }));

    const [yes, no] = screen.getAllByLabelText('Ask a follow-up question when this is chosen');
    expect(yes).toBeChecked();
    expect(no).not.toBeChecked();
    expect(screen.getByLabelText(/Follow-up question for “Yes”/)).toHaveValue('How many?');
    // A published follow-up keeps its type.
    expect(screen.getByLabelText('Answer type')).toBeDisabled();

    await userEvent.click(yes);
    await userEvent.click(screen.getByRole('button', { name: 'Update field' }));
    expect(screen.queryByRole('button', { name: 'Remove How many?' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Removed from this step \(1\)/ }));
    expect(screen.getByText('How many?')).toBeInTheDocument();
  });

  it('shows the Welcome text in order and publishes edited wording', async () => {
    renderBuilder();
    await openStep('Welcome');
    // Blocks open with their wording visible.
    expect(screen.getByLabelText('Heading')).toHaveValue('Welcome');
    const body = screen.getByLabelText('Page text') as HTMLTextAreaElement;
    expect(body.value.startsWith('## Shaping Tomorrow’s Business Leaders')).toBe(true);
    expect(screen.getByRole('heading', { name: 'What is an Apprenticeship?' })).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText('Heading'));
    await userEvent.type(screen.getByLabelText('Heading'), 'Welcome to KBC');
    expect(screen.getByText('Edited')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalled());
    expect((publishWizardLayout.mock.calls[0][0] as WizardLayout).texts).toEqual({ 'block.introduction.title': 'Welcome to KBC' });
  });

  it('edits a question’s wording from its row and can reset it', async () => {
    renderBuilder();
    await openStep('Personal Details');
    await userEvent.click(screen.getByRole('button', { name: 'Edit wording of Address' }));
    const input = screen.getByLabelText('Label');
    await userEvent.clear(input);
    await userEvent.type(input, 'Home address');
    expect(screen.getByText('“Home address”')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reset to standard wording' }));
    expect(screen.getByLabelText('Label')).toHaveValue('Address');
    expect(screen.getByRole('button', { name: /Publish changes/ })).toBeDisabled();
  });

  it('formats rich text from the toolbar', async () => {
    renderBuilder();
    await openStep('Welcome');
    const body = screen.getByLabelText('Page text') as HTMLTextAreaElement;
    await userEvent.clear(body);
    await userEvent.type(body, 'Hello');
    body.setSelectionRange(0, 5);
    await userEvent.click(screen.getByRole('button', { name: 'Bold' }));
    expect(body.value).toBe('**Hello**');
    expect(screen.getByText('Hello', { selector: 'strong' })).toBeInTheDocument();
  });

  it('warns before removing a question printed on the ILR document, and keeps it restorable', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderBuilder();
    await openStep('Personal Details');

    await userEvent.click(screen.getByRole('button', { name: 'Remove Address' }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('printed on the ILR document'));
    expect(screen.queryByRole('button', { name: 'Remove Address' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Removed from this step \(1\)/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Restore' }));
    expect(screen.getByRole('button', { name: 'Remove Address' })).toBeInTheDocument();
  });

  it('flags a document question made optional, and reorders steps', async () => {
    renderBuilder();
    await openStep('Personal Details');
    const row = screen.getByText('Email').closest('div.rounded-xl') as HTMLElement;
    await userEvent.click(within(row).getByLabelText('Mandatory'));
    expect(within(row).getByText(/prints blank on the ILR document/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Move Personal Details up' }));
    const steps = within(screen.getByRole('navigation', { name: 'Wizard steps' })).getAllByRole('listitem');
    expect(steps[1]).toHaveTextContent('Personal Details');

    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalled());
    const sent = publishWizardLayout.mock.calls[0][0] as WizardLayout;
    expect(sent.steps[1].slug).toBe('personal-details');
    expect(stepItems(sent, 'personal-details').find((i) => i.key === 'pd.email')!.required).toBe(false);
  });

  it('shows why a publish was refused and keeps the edits', async () => {
    publishWizardLayout.mockRejectedValue(new Error('Someone else has published the wizard since you opened it.'));
    renderBuilder();
    await openStep('Personal Details');
    await userEvent.click(screen.getByRole('button', { name: 'Move Last Name up' }));
    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(error).toHaveBeenCalledWith('Could not publish the wizard', expect.stringContaining('Someone else')));
    expect(screen.getByText('You have unpublished changes.')).toBeInTheDocument();
  });

  it('asks before leaving with unpublished changes', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderBuilder();
    await openStep('Personal Details');
    await userEvent.click(screen.getByRole('button', { name: 'Move Last Name up' }));
    await userEvent.click(screen.getByRole('button', { name: /Back to users/ }));
    expect(confirm).toHaveBeenCalled();
    expect(screen.queryByText('User directory')).not.toBeInTheDocument();
  });
});
