import { modulesConfiguration } from '@/features/programme-suitability/__tests__/modulesFixtures';
vi.mock('@/features/programme-suitability/modules/api', () => ({ getModuleDefaults: async () => ({ configuration: modulesConfiguration }), getModulesDraft: async () => ({ configuration: null, baseVersion: null }), saveModulesDraft: vi.fn() }));
/**
 * The "Edit apprenticeship wizard" builder: reachable from User Management,
 * edits a copy of the published layout, and publishes it in one go — adding
 * fields (with a dropdown-driven condition), reordering, making questions
 * optional or mandatory and removing them without losing their data.
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
const fetchWizardLayout = vi.fn();
const publishWizardLayout = vi.fn();
const fetchWizardVersions = vi.fn();
const fetchWizardVersion = vi.fn();
const updateWizardVersion = vi.fn();
vi.mock('@/api/wizardLayout', () => ({
  fetchWizardLayout: (...args: unknown[]) => fetchWizardLayout(...args),
  publishWizardLayout: (...args: unknown[]) => publishWizardLayout(...args),
  fetchWizardVersions: (...args: unknown[]) => fetchWizardVersions(...args),
  fetchWizardVersion: (...args: unknown[]) => fetchWizardVersion(...args),
  updateWizardVersion: (...args: unknown[]) => updateWizardVersion(...args),
}));

import WizardBuilderPage from '../page';
import { addCustomField, duplicateItem, moveItem, moveItemAfter, moveItemBefore, moveItemToStep, removeItem } from '../builderOps';
import { defaultLayout, resolveLayout } from '../../wizard/layout/resolve';
import type { WizardLayout } from '../../wizard/layout/types';
import { configuration } from '@/features/programme-suitability/__tests__/fixtures';

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

beforeEach(() => {
  fetchWizardLayout.mockResolvedValue({ layout: null, version: null, updatedAt: '', updatedBy: '' });
  fetchWizardVersions.mockResolvedValue([]);
  fetchWizardVersion.mockResolvedValue({ layout: null, version: 6, updatedAt: '', updatedBy: '' });
  updateWizardVersion.mockResolvedValue({});
  publishWizardLayout.mockImplementation(async (layout: WizardLayout) => ({ layout, version: 1, updatedAt: '2026-10-03T10:00:00Z', updatedBy: 'Admin' }));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });

const stepItems = (layout: WizardLayout, slug: string) => layout.steps.find((s) => s.slug === slug)!.items;

describe('layout edits', () => {
  it('resolves the three legacy CV questions as multi-line fields', () => {
    const layout = defaultLayout();
    layout.steps.find((s) => s.slug === 'cv-job')!.items.push(
      { key: 'cf_understanding', builtin: false, hidden: false, label: 'What is your understanding of the programme you are applying for?', type: 'text' },
      { key: 'cf_career', builtin: false, hidden: false, label: 'How will this programme help you in your career development/aspirations, and/or with your progression?', type: 'text' },
      { key: 'cf_role', builtin: false, hidden: false, label: 'Please explain how your current job role relates to the apprenticeship programme you are applying for. In your answer, include your job title, main duties, and how this programme will help you develop new knowledge, skills and behaviours in your role.', type: 'text' },
    );
    const cv = resolveLayout(layout).steps.find((s) => s.slug === 'cv-job')!;
    expect(cv.items.slice(-3).map((item) => item.type)).toEqual(['multi_line', 'multi_line', 'multi_line']);
  });

  it('moves an item past the next shown one, skipping removed items', () => {
    let layout = defaultLayout();
    layout = removeItem(layout, 'pd.lastName');
    layout = moveItem(layout, 'personal-details', 'pd.firstName', 1);
    const keys = stepItems(layout, 'personal-details').map((i) => i.key);
    expect(keys.slice(0, 3)).toEqual(['pd.email', 'pd.lastName', 'pd.firstName']);
  });

  it('moves an item before another shown item and duplicates custom fields without storage', () => {
    let layout = defaultLayout();
    const added = addCustomField(layout, 'cv-job', { label: 'Original question', type: 'single_line', required: true, table: 'Wizard_Cv_Job', column: 'Custom_original_question' });
    layout = moveItemBefore(added.layout, 'cv-job', added.key, 'cv.experience');
    const cv = stepItems(layout, 'cv-job');
    expect(cv.findIndex((item) => item.key === added.key)).toBeLessThan(cv.findIndex((item) => item.key === 'cv.experience'));
    const movedAfter = moveItemAfter(layout, 'cv-job', added.key, 'cv.experience');
    const afterItems = stepItems(movedAfter, 'cv-job');
    expect(afterItems.findIndex((item) => item.key === added.key)).toBeGreaterThan(afterItems.findIndex((item) => item.key === 'cv.experience'));

    const result = duplicateItem(layout, 'cv-job', added.key)!;
    const copy = stepItems(result.layout, 'cv-job').find((item) => item.key === result.key)!;
    expect(copy).toMatchObject({ label: 'Original question', type: 'single_line', required: true, builtin: false, hidden: false });
    expect(copy.column).toBeUndefined();
    expect(copy.key).not.toBe(added.key);
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
  if (name === 'Personal Details' && !within(nav).queryByRole('button', { name })) {
    await userEvent.click(within(nav).getByRole('button', { name: /Removed steps/ }));
    await userEvent.click(within(nav).getByRole('button', { name: 'Restore Personal Details' }));
  }
  await userEvent.click(within(nav).getByRole('button', { name }));
};

const dragFieldBefore = (sourceLabel: string, targetLabel: string) => {
  const source = screen.getByText(sourceLabel).closest('li')!;
  const target = screen.getByText(targetLabel).closest('li')!;
  const dataTransfer = { effectAllowed: '', dropEffect: '', setData: vi.fn(), getData: vi.fn(() => sourceLabel) };
  fireEvent.dragStart(source, { dataTransfer });
  fireEvent.drop(target, { dataTransfer });
};

describe('the builder page', () => {
  it('opens an older version and sends its id when renaming it', async () => {
    const layout = defaultLayout();
    fetchWizardLayout.mockResolvedValue({ layout, version: 7, updatedAt: '', updatedBy: 'Admin' });
    fetchWizardVersion.mockResolvedValue({ layout, version: 6, updatedAt: '', updatedBy: 'Previous admin' });
    fetchWizardVersions.mockResolvedValue([
      { version: 7, scope: 'default', name: 'Current', learnerKind: null, archived: false },
      { version: 6, scope: 'default', name: 'Older', learnerKind: null, archived: false },
    ]);
    vi.spyOn(window, 'prompt').mockReturnValue('Renamed older version');
    renderBuilder();
    const versionSelect = await screen.findByRole('combobox', { name: 'Version to edit' });
    await waitFor(() => expect(within(versionSelect).getByRole('option', { name: /version 6/ })).toBeInTheDocument());
    await userEvent.selectOptions(versionSelect, '6');
    await waitFor(() => expect(fetchWizardVersion).toHaveBeenCalledWith(6));
    await waitFor(() => expect(screen.getByText(/Published version 6/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Rename version' }));
    await waitFor(() => expect(updateWizardVersion).toHaveBeenCalledWith(6, { name: 'Renamed older version' }));
    expect(within(versionSelect).getByRole('option', { name: /version 6 · Renamed older version/ })).toBeInTheDocument();
  });

  it('shows the renamed current default and hides archived versions until requested', async () => {
    const layout = defaultLayout();
    const available = [
      { version: 7, scope: 'default', name: 'Global version', learnerKind: null, archived: false },
      { version: 6, scope: 'default', name: 'Earlier', learnerKind: null, archived: false },
    ];
    fetchWizardLayout.mockResolvedValue({ layout, version: 7, updatedAt: '', updatedBy: 'Admin' });
    fetchWizardVersions.mockImplementation(async () => available.map((entry) => ({ ...entry })));
    updateWizardVersion.mockImplementation(async (id: number, change: { name?: string; archived?: boolean }) => {
      Object.assign(available.find((entry) => entry.version === id)!, change);
      return {};
    });
    vi.spyOn(window, 'prompt').mockReturnValue('November default');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderBuilder();
    const versionSelect = await screen.findByRole('combobox', { name: 'Version to edit' });
    await waitFor(() => expect(within(versionSelect).getByRole('option', { name: /version 6/ })).toBeInTheDocument());
    const archivedCheckbox = screen.getByRole('checkbox', { name: 'Show archived versions' });
    expect(archivedCheckbox).not.toBeChecked();
    await userEvent.click(screen.getByRole('button', { name: 'Rename version' }));
    expect(within(versionSelect).getByRole('option', { name: /Current default · version 7 · November default/ })).toBeInTheDocument();
    await userEvent.selectOptions(versionSelect, '6');
    await waitFor(() => expect(screen.getByText(/Published version 6/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Archive version' }));
    await waitFor(() => expect(within(versionSelect).queryByRole('option', { name: /version 6/ })).toBeNull());
    await userEvent.click(archivedCheckbox);
    expect(within(versionSelect).getByRole('option', { name: /version 6 · Earlier \(archived\)/ })).toBeInTheDocument();
  });

  it('ignores an older version request after switching form type', async () => {
    const layout = defaultLayout();
    let rejectOld: ((reason: Error) => void) | undefined;
    fetchWizardVersion.mockImplementation(() => new Promise((_resolve, reject) => { rejectOld = reject; }));
    fetchWizardLayout.mockImplementation(async (options: { kind?: string }) => ({
      layout, version: options.kind === 'commercial' ? 9 : 7, updatedAt: '', updatedBy: 'Admin',
    }));
    fetchWizardVersions.mockResolvedValue([
      { version: 7, scope: 'default', name: 'Current', learnerKind: null, archived: false },
      { version: 6, scope: 'default', name: 'Older', learnerKind: null, archived: false },
    ]);
    renderBuilder();
    const versionSelect = await screen.findByRole('combobox', { name: 'Version to edit' });
    await waitFor(() => expect(within(versionSelect).getByRole('option', { name: /version 6/ })).toBeInTheDocument());
    await userEvent.selectOptions(versionSelect, '6');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Form type' }), 'commercial');
    rejectOld?.(new Error('Unknown learner kind.'));
    await waitFor(() => expect(screen.getByText(/Published version 9/)).toBeInTheDocument());
    expect(screen.queryByText('Unknown learner kind.')).not.toBeInTheDocument();
  });

  it('creates a commercial targeted version without assigning learners', async () => {
    const layout = defaultLayout();
    fetchWizardLayout.mockImplementation(async (options: { kind?: string }) => ({
      layout, version: options.kind === 'commercial' ? 9 : 7, updatedAt: '', updatedBy: 'Admin',
    }));
    fetchWizardVersions.mockResolvedValue([
      { version: 7, scope: 'default', name: 'Global version', learnerKind: 'apprenticeship', archived: false },
      { version: 9, scope: 'default', name: 'Global version', learnerKind: 'commercial', archived: false },
    ]);
    renderBuilder();
    await userEvent.click(await screen.findByRole('button', { name: /Create targeted version/ }));
    const dialog = screen.getByRole('dialog', { name: 'Create a targeted wizard version' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Commercial learners' }));
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Version name' }), 'Commercial intake');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create version' }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalledWith(expect.anything(), 9, {
      scope: 'targeted', name: 'Commercial intake', parentVersion: 9, learnerKind: 'commercial',
    }));
  });

  it('publishes edited modules with the existing wizard version and preserves assessment configuration', async () => {
    const layout = { ...defaultLayout(), programmeSuitability: configuration, programmeModules: structuredClone(modulesConfiguration) };
    fetchWizardLayout.mockResolvedValue({ layout, version: 7, updatedAt: '', updatedBy: 'Admin' });
    renderBuilder(); await openStep('Programme Suitability & Modules');
    const row = within(screen.getByRole('group', { name: 'Edit module Social Media' }));
    const sessions = row.getByLabelText('Sessions');
    await userEvent.clear(sessions); await userEvent.type(sessions, '18');
    expect(layout.programmeModules.programmes[0].modules[1].sessions).toBe(14);
    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Publish the wizard?' })).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalledTimes(1));
    const [published, version] = publishWizardLayout.mock.calls[0] as [WizardLayout, number];
    expect(version).toBe(7);
    expect(published.programmeModules!.programmes[0].modules[1].sessions).toBe(18);
    expect(published.programmeSuitability).toEqual(configuration);
  });

  it('adapts old layouts and hosts the existing assessment editor only on the new sixth step without publishing', async () => {
    const layout = { ...defaultLayout(), programmeSuitability: configuration };
    layout.steps = layout.steps.filter((s) => s.slug !== 'programme-suitability');
    const original = structuredClone(layout);
    fetchWizardLayout.mockResolvedValue({ layout, version: 7, updatedAt: '', updatedBy: 'Admin' });
    renderBuilder();
    await openStep('Programme Suitability & Modules');
    expect(screen.getByRole('region', { name: 'Programme Suitability Assessment configuration' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Programme Suitability & Modules step' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Publish changes/ })).toBeDisabled();
    await userEvent.click(screen.getByRole('tab', { name: 'Marketing Assessment' }));
    expect(screen.getByDisplayValue('marketing responsibilities')).toBeInTheDocument();
    await openStep('CV/Job Description');
    expect(screen.queryByRole('region', { name: 'Programme Suitability Assessment configuration' })).toBeNull();
    expect(screen.getByText('Highest-level qualification')).toBeInTheDocument();
    expect(publishWizardLayout).not.toHaveBeenCalled();
    expect(layout).toEqual(original);
  });

  it('starts from the standard wizard and cannot publish until something changes', async () => {
    renderBuilder();
    expect(await screen.findByText(/nothing has been published from the builder yet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Publish changes/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Removed steps (0)' })).toBeInTheDocument();
    await openStep('Personal Details');
    expect(screen.getByText('First Name')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Publish changes/ })).toBeDisabled();
    await openStep('ILR');
    expect(screen.getByText(/Apprenticeships have one required Your signature field/)).toBeInTheDocument();
  });

  it('removes and restores a step while keeping its questions in the published layout', async () => {
    renderBuilder();
    await openStep('CV/Job Description');
    await userEvent.click(screen.getByRole('button', { name: 'Remove CV/Job Description step' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove step' }));
    const nav = screen.getByRole('navigation', { name: 'Wizard steps' });
    expect(within(nav).queryByRole('button', { name: 'CV/Job Description' })).toBeNull();
    await userEvent.click(within(nav).getByRole('button', { name: /Removed steps/ }));
    expect(within(nav).getByRole('button', { name: 'Restore CV/Job Description' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalled());
    const sent = publishWizardLayout.mock.calls[0][0] as WizardLayout;
    expect(sent.steps.find((step) => step.slug === 'cv-job')).toMatchObject({ hidden: true, items: expect.any(Array) });
    await userEvent.click(within(nav).getByRole('button', { name: 'Restore CV/Job Description' }));
    expect(within(nav).getByRole('button', { name: 'CV/Job Description' })).toBeInTheDocument();
    expect(screen.getByText('Highest-level qualification')).toBeInTheDocument();
  });

  it('keeps required journey steps available', async () => {
    renderBuilder();
    await openStep('ILR');
    expect(screen.getByRole('button', { name: 'Remove ILR step' })).toBeDisabled();
    await openStep('Skills Radar');
    expect(screen.getByRole('button', { name: 'Remove Skills Radar step' })).toBeDisabled();
  });

  it('offers separate single-line and multi-line text fields', async () => {
    renderBuilder();
    await openStep('CV/Job Description');

    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    const type = screen.getByLabelText('Field type');
    expect(within(type).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Single-line text', 'Multi-line text', 'Number', 'Dropdown', 'File upload',
      'Heading', 'Description', 'Divider', 'Radio (single choice)', 'Checkbox (multiple choice)',
    ]);
    await userEvent.type(screen.getByLabelText('Question / label'), 'Additional details');
    await userEvent.selectOptions(type, 'multi_line');
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));

    expect(screen.getByText(/Multi-line text/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalled());
    const custom = stepItems(publishWizardLayout.mock.calls[0][0] as WizardLayout, 'cv-job').filter((i) => !i.builtin);
    expect(custom).toContainEqual(expect.objectContaining({ label: 'Additional details', type: 'multi_line' }));
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
      ['Night allowance', 'single_line', false],
    ]);
    expect(custom[0].options).toEqual(['Day', 'Night']);
    expect(custom[1].condition).toEqual({ field: custom[0].key, values: ['Night'] });
    expect(success).toHaveBeenCalledWith('Wizard published', expect.any(String));
  });

  it('creates content blocks and choice fields with reorderable options', async () => {
    renderBuilder();
    await openStep('CV/Job Description');

    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    await userEvent.selectOptions(screen.getByLabelText('Field type'), 'heading');
    await userEvent.type(screen.getByLabelText('Heading text'), 'GCSEs');
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));

    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    await userEvent.selectOptions(screen.getByLabelText('Field type'), 'radio');
    await userEvent.type(screen.getByLabelText('Question / label'), 'Which route do you prefer?');
    await userEvent.type(screen.getByLabelText('Option 1'), 'Online');
    await userEvent.type(screen.getByLabelText('Option 2'), 'In person');
    await userEvent.click(screen.getByRole('button', { name: 'Move option 2 up' }));
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));

    expect(screen.getAllByText('GCSEs').length).toBeGreaterThan(0);
    expect(screen.getByText(/Radio \(single choice\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(publishWizardLayout).toHaveBeenCalled());
    const custom = stepItems(publishWizardLayout.mock.calls[0][0] as WizardLayout, 'cv-job').filter((i) => !i.builtin);
    expect(custom).toContainEqual(expect.objectContaining({ label: 'GCSEs', type: 'heading' }));
    expect(custom).toContainEqual(expect.objectContaining({ label: 'Which route do you prefer?', type: 'radio', options: ['In person', 'Online'] }));
  });

  it('duplicates a custom field and supports dragging a field before another', async () => {
    renderBuilder();
    await openStep('CV/Job Description');

    await userEvent.click(screen.getByRole('button', { name: 'Add field' }));
    await userEvent.type(screen.getByLabelText('Question / label'), 'Copy me');
    await userEvent.click(screen.getByRole('button', { name: /^Add field$/ }));

    await userEvent.click(screen.getByRole('button', { name: 'Duplicate Copy me' }));
    expect(screen.getByDisplayValue('Copy me')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    const list = screen.getByRole('list', { name: 'Items on CV/Job Description' });
    const source = screen.getAllByText('Copy me')[1].closest('li')!;
    const target = screen.getByText('CV upload').closest('li')!;
    const dataTransfer = { effectAllowed: '', dropEffect: '', setData: vi.fn(), getData: vi.fn(() => source.querySelector('p')?.textContent ?? '') };
    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });
    expect(Array.from(list.querySelectorAll(':scope > li')).findIndex((li) => li.textContent?.includes('Copy me')))
      .toBeLessThan(Array.from(list.querySelectorAll(':scope > li')).findIndex((li) => li.textContent?.includes('CV upload')));
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
    expect(within(type).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Single-line text', 'Multi-line text', 'Number', 'Dropdown', 'File upload',
      'Radio (single choice)', 'Checkbox (multiple choice)',
    ]);
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
    dragFieldBefore('Last Name', 'First Name');
    await userEvent.click(screen.getByRole('button', { name: /Publish changes/ }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Publish$/ }));
    await waitFor(() => expect(error).toHaveBeenCalledWith('Could not publish the wizard', expect.stringContaining('Someone else')));
    expect(screen.getByText('You have unpublished changes.')).toBeInTheDocument();
  });

});
