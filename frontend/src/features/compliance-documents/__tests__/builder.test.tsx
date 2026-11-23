import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { contentFor, indexFor, payloadFor, versionFor } from './fixtures';
import seedData from '../seed.json';
import type { DocumentTemplate } from '../types';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
const api = vi.hoisted(() => ({ listTemplates: vi.fn(), readTemplate: vi.fn(), readFields: vi.fn(), saveTemplate: vi.fn(),
  previewTemplate: vi.fn(), publishTemplate: vi.fn(), deleteDraft: vi.fn(), templateHistory: vi.fn() }));
vi.mock('../api', () => api);
vi.mock('../DocumentPreview', () => ({ DocumentPreview: ({ value, onReady, onClose }: any) => <div role="dialog" aria-label="Synthetic PDF viewer"><p>{value.notice}</p>
  <button onClick={() => onReady(value)}>Finish PDF rendering</button><button onClick={onClose}>Close preview</button></div> }));
import Page from '../page';

beforeEach(() => {
  vi.resetAllMocks(); vi.spyOn(window, 'confirm').mockReturnValue(true);
  api.listTemplates.mockResolvedValue(indexFor());
  api.readTemplate.mockImplementation(async (id: string) => ({ published: versionFor(id as any), draft: null, revision: 0, baseVersion: `${id}-version-1` }));
  api.readFields.mockImplementation(async (id: keyof typeof seedData.fields) => ({ results: Object.values(seedData.fields[id]) }));
  api.saveTemplate.mockImplementation(async (content: DocumentTemplate, baseVersion: string, revision: number) => ({ content: structuredClone(content), baseVersion, revision: revision + 1 }));
  api.previewTemplate.mockResolvedValue(payloadFor('ilr', true));
  api.publishTemplate.mockResolvedValue({ published: { ...versionFor('ilr', true), number: 2 } });
  api.templateHistory.mockResolvedValue({ results: [versionFor('ilr')] });
});
async function editIlr() {
  const user = userEvent.setup(); render(<Page />);
  await screen.findAllByText('4 documents');
  await user.click(screen.getByRole('button', { name: /Compliance Documents/ }));
  await user.click(await screen.findByRole('button', { name: 'Edit Individual Learner Record' }));
  await screen.findByRole('textbox', { name: 'Document page 1' });
  return user;
}
function editHeading(text: string) { const surface = screen.getByRole('textbox', { name: 'Document page 1' }); surface.querySelector('h1,h2,h3')!.textContent = text; fireEvent.input(surface); }

describe('admin document builder', () => {
  it('starts unselected and switches the shared content panel without changing records', async () => {
    const source = indexFor();
    const before = structuredClone(source);
    api.listTemplates.mockResolvedValue(source);
    render(<Page />); await screen.findAllByText('4 documents');
    const compliance = screen.getByRole('button', { name: /Compliance Documents/ });
    const reviews = screen.getByRole('button', { name: /Reviews Documents/ });
    expect(compliance).toHaveAttribute('aria-pressed', 'false');
    expect(compliance).not.toHaveAttribute('aria-expanded');
    expect(screen.getByText('Select a document category')).toBeVisible();
    expect(within(screen.getByRole('navigation', { name: 'Document categories' })).getAllByRole('button')).toHaveLength(2);
    expect(reviews).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('table')).toBeNull();
    await userEvent.click(compliance);
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(4);
    const group = within(screen.getByRole('region', { name: 'Compliance Documents' }));
    for (const record of source.results.filter((t) => t.category === 'Compliance')) expect(group.getByRole('button', { name: `Edit ${record.title}` })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Edit ULN Privacy Notice & Learner Acknowledgement' })).toBeNull();
    await userEvent.click(reviews);
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(4);
    await userEvent.click(compliance);
    expect(reviews).toHaveAttribute('aria-pressed', 'false');
    expect(compliance).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByRole('table')).toHaveLength(1);
    await userEvent.click(compliance);
    expect(compliance).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(4);
    expect(source).toEqual(before);
    expect(api.saveTemplate).not.toHaveBeenCalled(); expect(api.publishTemplate).not.toHaveBeenCalled();
  });

  it('uses actual categories for changed and added records and searches title, category and programme', async () => {
    const source = indexFor();
    source.results[0].category = 'Review';
    source.results[0].programmes = ['PCP_L6'];
    source.results.push({ ...source.results[0], templateId: 'new-api-record', title: 'New API document', category: 'Compliance' });
    api.listTemplates.mockResolvedValue(source);
    render(<Page />); await screen.findByText('5 documents');
    const input = screen.getByLabelText('Search document templates');
    fireEvent.change(input, { target: { value: 'Project Controls Professional' } });
    expect(screen.queryByRole('table')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /Reviews Documents/ }));
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(1);
    expect(within(screen.getByRole('region', { name: 'Reviews Documents' })).getByRole('button', { name: `Edit ${source.results[0].title}` })).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /Compliance Documents/ }));
    expect(input).toHaveValue('Project Controls Professional');
    expect(within(screen.getByRole('region', { name: 'Compliance Documents' })).getByRole('button', { name: 'Edit New API document' })).toBeVisible();
    fireEvent.change(input, { target: { value: 'Review' } });
    expect(screen.queryByRole('button', { name: /^Edit / })).toBeNull();
    expect(screen.getByText('No matching templates in this category.')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /Reviews Documents/ }));
    expect(input).toHaveValue('Review');
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(5);
    fireEvent.change(input, { target: { value: 'New API document' } });
    await userEvent.click(screen.getByRole('button', { name: /Compliance Documents/ }));
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(1);
    fireEvent.change(input, { target: { value: 'missing-search-result' } });
    expect(screen.getByText('No matching templates in this category.')).toBeVisible();
    expect(screen.queryByRole('button', { name: /^Edit / })).toBeNull();
  });

  it.each(['Compliance', 'Review'] as const)('preserves row preview, history and edit identities for %s', async category => {
    const record = indexFor().results.find((t) => t.category === category)!;
    render(<Page />); await screen.findAllByText('4 documents');
    await userEvent.click(screen.getByRole('button', { name: category === 'Compliance' ? /Compliance Documents/ : /Reviews Documents/ }));
    await userEvent.click(screen.getByRole('button', { name: `Preview ${record.title}` }));
    await screen.findByRole('dialog', { name: 'Synthetic PDF viewer' });
    expect(api.previewTemplate).toHaveBeenCalledWith(record.templateId, 0, undefined, record.publishedVersion!.id, true);
    await userEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    await userEvent.click(screen.getByRole('button', { name: `Version history for ${record.title}` }));
    await waitFor(() => expect(api.templateHistory).toHaveBeenCalledWith(record.templateId));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Close history' }));
    await userEvent.click(screen.getByRole('button', { name: `Edit ${record.title}` }));
    await waitFor(() => expect(api.readTemplate).toHaveBeenCalledWith(record.templateId));
    expect(api.saveTemplate).not.toHaveBeenCalled(); expect(api.publishTemplate).not.toHaveBeenCalled();
  });

  it('prepares the shared design only in the editable draft and keeps the original layout selectable', async () => {
    const user = await editIlr();
    expect(screen.getByRole('combobox', { name: 'Document design' })).toHaveValue('purple-reference-v2');
    expect(screen.getByRole('textbox', { name: 'Document page 1' }).closest('.document-lavender-v1')).not.toBeNull();
    expect(api.saveTemplate).not.toHaveBeenCalled(); expect(api.publishTemplate).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Document design' }), 'classic');
    expect(screen.getByRole('textbox', { name: 'Document page 1' }).closest('.document-lavender-v1')).toBeNull();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Document design' }), 'lavender-v1');
    await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    expect(api.saveTemplate).toHaveBeenCalledWith(expect.objectContaining({ designTheme: 'lavender-v1', title: contentFor('ilr').title, blocks: contentFor('ilr').blocks }), 'ilr-version-1', 0);
    expect(api.publishTemplate).not.toHaveBeenCalled();
  });
  it('lists all eight documents in their original categories and supports search', async () => {
    render(<Page />); await screen.findAllByText('4 documents');
    expect(screen.queryByRole('button', { name: /^Edit / })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /Compliance Documents/ }));
    await userEvent.click(screen.getByRole('button', { name: /Reviews Documents/ }));
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(4);
    expect(screen.queryAllByText('Compliance', { exact: true })).toHaveLength(0);
    expect(screen.getAllByText('Review', { exact: true })).toHaveLength(4);
    fireEvent.change(screen.getByLabelText('Search document templates'), { target: { value: 'ULN' } });
    expect(screen.getAllByRole('button', { name: /^Edit / })).toHaveLength(1);
  });
  it('lists the dynamic Suitability & RPL report without exposing static template actions', async () => {
    api.listTemplates.mockResolvedValueOnce({ ...indexFor(), results: [...indexFor().results, {
      templateId: 'suitability-rpl-report', documentKey: 'suitability-rpl-report', title: 'Suitability & RPL Report',
      fullTitle: 'Suitability & Recognition of Prior Learning Report', category: 'Review', programmes: [],
      publishedVersion: null, draft: null, updatedAt: null, dynamic: true,
    }] });
    render(<Page />);
    await screen.findByText('5 documents');
    expect(screen.queryByText('Suitability & RPL Report')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Compliance Documents/ }));
    expect(screen.queryByText('Suitability & RPL Report')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Reviews Documents/ }));
    expect(await screen.findByText('Suitability & RPL Report')).toBeInTheDocument();
    expect(screen.getByText('PDF: Suitability & Recognition of Prior Learning Report')).toBeInTheDocument();
    expect(screen.getByText('Open from the learner’s Suitability & RPL Report tab')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit Suitability & RPL Report' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Preview Suitability & RPL Report' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Version history for Suitability & RPL Report' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search document templates'), { target: { value: 'Recognition of Prior Learning' } });
    expect(screen.getByText('Suitability & RPL Report')).toBeVisible();
    expect(screen.getByRole('button', { name: /Reviews Documents/ })).toHaveTextContent('5 documents / 1 matching');
    await userEvent.click(screen.getByRole('button', { name: /Compliance Documents/ }));
    expect(screen.getByText('No matching templates in this category.')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /Reviews Documents/ }));
    expect(screen.getByText('Suitability & RPL Report')).toBeVisible();
    expect(api.readTemplate).not.toHaveBeenCalled();
    expect(api.saveTemplate).not.toHaveBeenCalled();
    expect(api.publishTemplate).not.toHaveBeenCalled();
  });
  it('saves a changed heading with optimistic revision and leaves publication explicit', async () => {
    const user = await editIlr();
    editHeading('Approved edited heading');
    await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    await screen.findByText(/Draft saved\./);
    expect(api.saveTemplate).toHaveBeenCalledWith(expect.objectContaining({ blocks: expect.arrayContaining([expect.objectContaining({ text: 'Approved edited heading' })]) }), 'ilr-version-1', 0);
    expect(api.publishTemplate).not.toHaveBeenCalled(); expect(api.previewTemplate).not.toHaveBeenCalled();
  });
  it('requires save, successful PDF rendering and organisational review before publishing', async () => {
    const user = await editIlr(); const publish = screen.getByRole('button', { name: 'Publish new version' });
    expect(publish).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    await user.click(await screen.findByRole('button', { name: 'Preview saved draft' }));
    await screen.findByRole('dialog', { name: 'Synthetic PDF viewer' });
    expect(publish).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Finish PDF rendering' }));
    await user.click(screen.getByRole('button', { name: 'Close preview' }));
    await user.click(screen.getByLabelText(/I confirm this wording/));
    expect(publish).toBeEnabled(); await user.click(publish);
    await screen.findByText(/Published version 2\./);
    expect(api.publishTemplate).toHaveBeenCalledWith('ilr', 1, 'synthetic-preview-token', true, '');
  });
  it('invalidates preview after further edits and reports stale-draft errors without discarding work', async () => {
    const user = await editIlr(); api.saveTemplate.mockRejectedValueOnce(new Error('This draft changed elsewhere. Reload it before saving.'));
    editHeading('Keep my local wording');
    await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere');
    expect(screen.getByRole('textbox', { name: 'Document page 1' })).toHaveTextContent('Keep my local wording');
  });
  it('uses page navigation and persists page reordering without listing paragraphs', async () => {
    const user = await editIlr();
    expect(screen.queryByRole('navigation', { name: 'Document content blocks' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add page' }));
    const surface = screen.getByRole('textbox', { name: 'Document page 2' });
    surface.innerHTML = '<p>New page for {{learner.fullName}}</p>'; fireEvent.input(surface);
    await user.click(screen.getByRole('button', { name: 'Move page 2 up' }));
    await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    await waitFor(() => expect(api.saveTemplate).toHaveBeenCalled());
    expect(api.saveTemplate.mock.calls[0][0].blocks[0].text).toBe('New page for {{learner.fullName}}');
  });
  it('edits custom table cells within the full page', async () => {
    const user = await editIlr();
    const surface = screen.getByRole('textbox', { name: 'Document page 1' });
    surface.insertAdjacentHTML('beforeend', '<table><tr><th>New column</th></tr><tr><td>Saved cell</td></tr></table>'); fireEvent.input(surface);
    await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    await waitFor(() => expect(api.saveTemplate).toHaveBeenCalled());
    const table = api.saveTemplate.mock.calls[0][0].blocks.find((b: any) => b.type === 'table' && b.columns[0] === 'New column');
    expect(table.rows[0][0]).toBe('Saved cell');
  });
  it('creates a scoped programme variant without mutating the shared template', async () => {
    const user = await editIlr(); await user.click(screen.getByRole('button', { name: 'Create programme variant' }));
    const modal = screen.getByRole('dialog');
    await user.click(within(modal).getByLabelText('Marketing Executive Level 4'));
    await user.click(within(modal).getByRole('button', { name: 'Create draft variant' }));
    await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    await waitFor(() => expect(api.saveTemplate).toHaveBeenCalled());
    const [content, base, revision] = api.saveTemplate.mock.calls[0];
    expect(content.templateId).toMatch(/^ilr@/); expect(content.programmes).toEqual(['ME_L4']);
    expect(base).toBe('new'); expect(revision).toBe(0); expect(contentFor('ilr').programmes).toEqual([]);
  });
  it('defaults to sample preview and requires an explicit valid learner for real preview', async () => {
    const user = await editIlr(); await user.click(screen.getByRole('button', { name: 'Save Draft' }));
    await user.click(screen.getByLabelText('Use authorised real learner data for preview'));
    await user.click(screen.getByRole('button', { name: 'Preview saved draft' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('existing learner identifier'); expect(api.previewTemplate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Existing learner ID'), { target: { value: '123' } });
    await user.click(screen.getByRole('button', { name: 'Preview saved draft' }));
    await waitFor(() => expect(api.previewTemplate).toHaveBeenCalledWith('ilr', 1, 123, undefined, false));
  });
  it('opens historical versions without saving, publishing or issuing', async () => {
    const user = await editIlr(); await user.click(screen.getByRole('button', { name: 'Version history for Individual Learner Record' }));
    await user.click(await screen.findByRole('button', { name: 'Preview historical PDF' }));
    await waitFor(() => expect(api.previewTemplate).toHaveBeenCalledWith('ilr', 0, undefined, 'ilr-version-1', false));
    expect(api.saveTemplate).not.toHaveBeenCalled(); expect(api.publishTemplate).not.toHaveBeenCalled();
  });
});
