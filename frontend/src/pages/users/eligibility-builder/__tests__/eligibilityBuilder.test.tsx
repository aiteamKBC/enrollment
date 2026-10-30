/**
 * The "Edit eligibility form" builder: reachable from User Management, edits a
 * copy of the published layout and publishes it in one go — answer lists,
 * wording, custom questions (with a condition), order, mandatory, and removing
 * questions without losing their data. The questions that identify the
 * applicant cannot be removed.
 */
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
const success = vi.fn();
const error = vi.fn();
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success, error }) }));
const fetchEligibilityLayout = vi.fn();
const publishEligibilityLayout = vi.fn();
vi.mock('@/api/eligibilityFormLayout', () => ({
  fetchEligibilityLayout: (...args: unknown[]) => fetchEligibilityLayout(...args),
  publishEligibilityLayout: (...args: unknown[]) => publishEligibilityLayout(...args),
}));

import EligibilityBuilderPage from '../page';
import {
  conditionSources,
  dependents,
  moveItem,
  problems,
  removeItem,
  saveCustomQuestion,
  setText,
  updateItem,
} from '../builderOps';
import { defaultLayout } from '@/pages/apprenticeship-application/layout/resolve';
import type { EligibilityItem, EligibilityLayout } from '@/pages/apprenticeship-application/layout/types';

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  fetchEligibilityLayout.mockResolvedValue({ layout: null, version: null, updatedAt: '', updatedBy: '' });
  publishEligibilityLayout.mockImplementation(async (layout: EligibilityLayout) => ({ layout, version: 1, updatedAt: '2026-10-15T10:00:00Z', updatedBy: 'Admin' }));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

const sectionItems = (layout: EligibilityLayout, slug: string) => layout.sections.find((s) => s.slug === slug)!.items;
const item = (layout: EligibilityLayout, key: string) => layout.sections.flatMap((s) => s.items).find((i) => i.key === key)!;

describe('layout edits', () => {
  it('moves a question past the next shown one, skipping removed questions', () => {
    let layout = removeItem(defaultLayout(), 'surname');
    layout = moveItem(layout, 'personal-details', 'firstName', 1);
    expect(sectionItems(layout, 'personal-details').map((i) => i.key).slice(0, 3)).toEqual(['workEmail', 'surname', 'firstName']);
  });

  it('hides a built-in or published question but drops one never published', () => {
    const draft = { label: 'Draft', type: 'text' as const, options: [], helpText: '', required: false, condition: null };
    const added = saveCustomQuestion(defaultLayout(), 'residency', null, draft);
    expect(item(added.layout, added.key)).toMatchObject({ builtin: false, label: 'Draft' });
    let layout = removeItem(added.layout, added.key);
    expect(sectionItems(layout, 'residency').some((i) => i.key === added.key)).toBe(false);

    layout = removeItem(layout, 'phoneNumber');
    expect(item(layout, 'phoneNumber').hidden).toBe(true);
    const published = { ...defaultLayout() };
    published.sections[2].items.push({ key: 'cf_x', builtin: false, hidden: false, label: 'X', type: 'text', column: 'custom_x' });
    expect(item(removeItem(published, 'cf_x'), 'cf_x').hidden).toBe(true);
  });

  it('keeps edited wording through every other edit', () => {
    let layout = setText(defaultLayout(), 'firstName.label', 'Given name');
    layout = updateItem(layout, 'phoneNumber', { required: true });
    layout = moveItem(layout, 'personal-details', 'phoneNumber', -1);
    layout = removeItem(layout, 'dateOfBirth');
    expect(layout.texts).toEqual({ 'firstName.label': 'Given name' });
    expect(setText(layout, 'firstName.label', undefined).texts).toBeUndefined();
  });

  it('knows what is asked only for an answer to a question', () => {
    const layout = defaultLayout();
    expect(dependents(layout, 'attendedFundedTraining').map((i) => i.key)).toEqual(['fundedTrainingCompletedOn']);
    const withFollowUp = saveCustomQuestion(layout, 'residency', null, {
      label: 'Which visa?', type: 'text', options: [], helpText: '', required: true, condition: { field: 'residencyStatus', values: ['Other'] },
    });
    expect(dependents(withFollowUp.layout, 'residencyStatus').map((i) => i.label)).toEqual(['Which visa?']);
    // A question cannot depend on itself or on what depends on it.
    const sources = conditionSources(withFollowUp.layout, 'residencyStatus').map((s) => s.item.key);
    expect(sources).not.toContain('residencyStatus');
    expect(sources).toEqual(expect.arrayContaining(['employmentStatus', 'livesInUk', 'programme']));
    expect(sources).not.toContain('firstName');
  });

  it('finds what would stop a publish', () => {
    let layout = defaultLayout();
    expect(problems(layout)).toEqual([]);
    layout = updateItem(layout, 'programme', { options: [' ', ''] });
    layout = updateItem(layout, 'residencyStatus', { options: ['UK Citizen', 'UK Citizen '] });
    layout.sections[0].hidden = true;
    const custom: EligibilityItem = { key: 'new_1', builtin: false, hidden: false, label: 'Why?', type: 'text', condition: { field: 'employmentStatus', values: ['Retired'] } };
    layout.sections[1].items.push(custom);
    expect(problems(layout)).toEqual(expect.arrayContaining([
      '“First Name” identifies the applicant, so it must stay on a section applicants see.',
      '“Choose Your Applying Programme” needs at least one answer.',
      '“Please provide your residency status?” lists the same answer twice.',
      '“Why?” is shown for answers the form no longer offers: Retired.',
    ]));
  });
});

function renderBuilder() {
  return render(
    <MemoryRouter initialEntries={['/users/eligibility-builder']}>
      <Routes>
        <Route path="/users/eligibility-builder" element={<EligibilityBuilderPage />} />
        <Route path="/users" element={<p>Users directory</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const publishedLayout = () => publishEligibilityLayout.mock.calls[0][0] as EligibilityLayout;

async function publish() {
  fireEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: /^Publish$/ }));
  await waitFor(() => expect(success).toHaveBeenCalled());
}

describe('builder page', () => {
  it('opens on the standard form with nothing to publish', async () => {
    renderBuilder();
    expect(await screen.findByText(/Showing the standard form/)).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Form sections' });
    for (const name of ['Form heading', 'Personal Details', 'Employment', 'Residency', 'Programme Suitability']) {
      expect(within(nav).getByRole('button', { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: /Publish changes/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /Back to users/ }));
    expect(await screen.findByText('Users directory')).toBeInTheDocument();
  });

  it('never lets the questions that identify the applicant be removed or hidden', async () => {
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    expect(screen.queryByRole('button', { name: 'Remove First Name' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove Work Email Address' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Show this section to applicants' })).toBeDisabled();
    const firstName = screen.getByText('First Name').closest('div.rounded-xl') as HTMLElement;
    expect(within(firstName).getByRole('checkbox', { name: 'Mandatory' })).toBeDisabled();
    expect(within(firstName).getByRole('checkbox', { name: 'Mandatory' })).toBeChecked();
  });

  it('edits a built-in answer list and publishes it', async () => {
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    fireEvent.click(screen.getByRole('button', { name: 'Programme Suitability' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Edit answers of Choose Your Applying Programme' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Choose Your Applying Programme: answer 2' }), { target: { value: 'Data Analyst Level 4' } });
    fireEvent.click(screen.getByRole('button', { name: /Remove “Project Controls Professional Level 6”/ }));
    expect(screen.getByText('Answers edited')).toBeInTheDocument();

    await publish();
    expect(publishEligibilityLayout.mock.calls[0][1]).toBeNull();
    expect(item(publishedLayout(), 'programme').options).toEqual([
      'Marketing Executive Level 4', 'Data Analyst Level 4', 'Associate Project Manager Level 4',
    ]);
    expect(await screen.findByText(/Published version 1/)).toBeInTheDocument();
  });

  it('edits the wording of a built-in question', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    await user.click(screen.getByRole('button', { name: 'Edit wording of Surname' }));
    const box = screen.getByRole('textbox', { name: 'Question' });
    await user.clear(box);
    await user.type(box, 'Family name');
    await publish();
    expect(publishedLayout().texts).toEqual({ 'surname.label': 'Family name' });
  });

  it('adds a question asked only for one answer to another', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    await user.click(screen.getByRole('button', { name: 'Residency' }));
    await user.click(screen.getByRole('button', { name: /Add question/ }));
    const editor = screen.getByRole('group', { name: 'New question' });
    await user.type(within(editor).getByRole('textbox', { name: 'Question' }), 'Which visa do you hold?');
    await user.click(within(editor).getByRole('checkbox', { name: /Mandatory/ }));
    await user.click(within(editor).getByRole('checkbox', { name: /Only ask this question/ }));
    await user.selectOptions(within(editor).getByRole('combobox', { name: /When this question/ }), 'residencyStatus');
    await user.click(within(editor).getByRole('checkbox', { name: 'Other' }));
    await user.click(within(editor).getByRole('button', { name: /Add question/ }));

    expect(screen.getByText('New — adds a column when published')).toBeInTheDocument();
    expect(screen.getByText('Asked when “Please provide your residency status?” is Other')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    expect(within(await screen.findByRole('dialog')).getByText(/Which visa do you hold\?/)).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(success).toHaveBeenCalled());
    const added = sectionItems(publishedLayout(), 'residency').at(-1)!;
    expect(added).toMatchObject({
      builtin: false, label: 'Which visa do you hold?', type: 'text', required: true,
      condition: { field: 'residencyStatus', values: ['Other'] },
    });
  });

  it('removes a question and restores it', async () => {
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    fireEvent.click(screen.getByRole('button', { name: 'Remove Phone Number' }));
    expect(screen.queryByRole('button', { name: 'Remove Phone Number' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Removed from this section \(1\)/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    expect(screen.getByRole('button', { name: 'Remove Phone Number' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Publish changes/ })).toBeDisabled();
  });

  it('warns before removing a question others depend on', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    fireEvent.click(screen.getByRole('button', { name: 'Programme Suitability' }));
    fireEvent.click(await screen.findByRole('button', { name: /Remove Have you attended any other government funded/ }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('1 question only asked for an answer to this question will stop being asked'));
    expect(screen.getByRole('button', { name: /Remove Have you attended any other government funded/ })).toBeInTheDocument();
  });

  it('will not publish a form with a problem', async () => {
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Section name' }), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Every section needs a name.')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /^Publish$/ })).toBeDisabled();
    expect(publishEligibilityLayout).not.toHaveBeenCalled();
  });

  it('reports a refused publish and keeps the changes', async () => {
    publishEligibilityLayout.mockRejectedValueOnce(new Error('Someone else has published the eligibility form since you opened it.'));
    renderBuilder();
    await screen.findByRole('textbox', { name: 'Section name' });
    fireEvent.click(screen.getByRole('button', { name: 'Remove Phone Number' }));
    fireEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(error).toHaveBeenCalledWith('Could not publish the eligibility form', expect.stringContaining('Someone else')));
    expect(screen.getByText('You have unpublished changes.')).toBeInTheDocument();
  });
});
