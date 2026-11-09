import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { contentFor, keys, payloadFor } from './fixtures';
import type { DocumentKey } from '../types';
import type { DocumentRevision, RevisionDetail } from '../revisionApi';
const api = vi.hoisted(() => ({ revisionHistory: vi.fn(), createDocumentFromTemplate: vi.fn(), startRevision: vi.fn(), readRevision: vi.fn(), saveRevision: vi.fn(), previewRevision: vi.fn(), finalizeRevision: vi.fn(), discardRevision: vi.fn() }));
vi.mock('../revisionApi', () => api);
vi.mock('../api', () => ({ readFields: async () => ({ results: [{ key: 'learner.fullName', label: 'Learner name', group: 'Learner', format: 'text' }] }) }));
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { role: 'staff' }, user: { fullName: 'Synthetic Admin' } } }) }));
vi.mock('../DocumentPreview', () => ({ DocumentPreview: ({ value, onReady, onClose }: any) => <div role="dialog" aria-label="Saved new document preview"><button onClick={() => { onReady(value); onClose(); }}>Reviewed new document</button></div> }));
import { DocumentHistory } from '../DocumentHistory';
import { LearnerDocumentRevisions } from '../LearnerDocumentRevisions';

let rows: DocumentRevision[], current: RevisionDetail;
function revision(key: DocumentKey, number = 1): DocumentRevision {
  return { id: `${key}-r${number}`, issueId: key, sourceId: key, documentKey: key, title: contentFor(key).title,
    number, baseId: number > 1 ? `${key}-r1` : null, state: number === 1 ? 'original' : 'draft', editVersion: 1,
    signatureMethod: number === 1 ? 'original' : 'sign-again', status: number === 1 ? 'Signed' : 'Draft', fullySigned: number === 1,
    templateVersionId: 'historical-v1', templateVersionNumber: 1, createdBy: 'Admin', createdAt: '2030-01-01', finalizedAt: null,
    reason: '', changes: {}, checksum: 'hash', provenance: {}, signatures: {}, canDiscard: number > 1, creationMode: number > 1 ? 'learner' : 'edit' };
}
const mount = (key: DocumentKey) => render(<MemoryRouter><DocumentHistory title={contentFor(key).title}>{({ chevron, count, historyControl }) => <section aria-label="Issued document"><header>{chevron}{contentFor(key).title}{count}</header><LearnerDocumentRevisions learnerId="20" documentKey={key} sourceId={key} historyControl={historyControl} /></section>}</DocumentHistory></MemoryRouter>);
beforeEach(() => {
  vi.clearAllMocks(); rows = [];
  api.revisionHistory.mockImplementation(async () => ({ results: [...rows], setupRequired: false }));
  api.createDocumentFromTemplate.mockImplementation(async (_learner, key: DocumentKey) => {
    const preview = payloadFor(key); preview.sample = false;
    preview.template.content.blocks.unshift({ id: 'latest-question', type: 'field', label: 'Latest contract question?', value: '' });
    preview.template.fields = { ...preview.template.fields, 'learner.fullName': 'Synthetic Learner', 'document.programme.programme': 'Current programme' };
    preview.data.learnerName = 'Synthetic Learner';
    preview.template.content.blocks.unshift({ id: 'current-student', type: 'field', label: 'Learner name', value: '{{learner.fullName}}' });
    current = { revision: revision(key, 2), preview }; rows = [revision(key), current.revision]; return structuredClone(current);
  });
  api.saveRevision.mockImplementation(async (_id, _revision, _version, content, reason, signatureMethod) => {
    current.preview.template.content = content; current.revision = { ...current.revision, editVersion: current.revision.editVersion + 1, reason, signatureMethod };
    rows[1] = current.revision; return structuredClone(current);
  });
  api.readRevision.mockImplementation(async () => structuredClone(current));
  api.previewRevision.mockImplementation(async () => ({ ...structuredClone(current.preview), previewToken: 'reviewed-new-document' }));
  api.finalizeRevision.mockImplementation(async () => { current.revision = { ...current.revision, state: 'finalized', status: 'Available', canDiscard: false }; rows[1] = current.revision; return structuredClone(current); });
  api.discardRevision.mockImplementation(async () => { rows = rows.slice(0, 1); return { discarded: true }; });
});

describe('Create a current learner document from the latest template', () => {
  it.each(keys)('creates %s with current learner data and fresh signatures, retaining independent accordion state', async (key) => {
    mount(key);
    const create = screen.getByRole('button', { name: 'Create' });
    fireEvent.click(create); fireEvent.click(create);
    const editor = await screen.findByRole('dialog', { name: 'Learner document revision editor' });
    expect(api.createDocumentFromTemplate).toHaveBeenCalledTimes(1);
    expect(api.createDocumentFromTemplate).toHaveBeenCalledWith('20', key, key, expect.stringMatching(/^[a-f0-9-]{36}$/));
    expect(api.startRevision).not.toHaveBeenCalled(); expect(api.readRevision).not.toHaveBeenCalled();
    expect(editor).toHaveTextContent('New Document — Version 2 (Draft)');
    expect(editor).toHaveTextContent('Synthetic Learner'); expect(editor).toHaveTextContent(contentFor(key).title);
    await waitFor(() => expect(editor).toHaveTextContent('Latest contract question?'));
    expect(within(editor).getByRole('textbox', { name: 'Document page 1' })).toHaveTextContent('Synthetic Learner');
    expect(editor).toHaveTextContent('current data');
    expect(within(editor).queryByRole('button', { name: 'Keep Existing Signatures' })).not.toBeInTheDocument();
    expect(within(editor).queryByRole('button', { name: 'Sign Again' })).not.toBeInTheDocument();
    expect(within(editor).queryByRole('img')).not.toBeInTheDocument();
    for (const name of ['Add table', 'Insert dynamic field', 'Insert date', 'Insert signature block', 'Insert page break']) expect(within(editor).getByRole('button', { name })).toBeInTheDocument();
    fireEvent.click(within(editor).getByRole('button', { name: 'Close editor' }));
    expect(await screen.findByRole('button', { name: `Expand version history for ${contentFor(key).title}` })).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(await screen.findByText('Version 2 — Draft')).toBeVisible();
    expect(screen.getByText('Version 1 — Original')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Continue editing' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeEnabled();
    expect(api.finalizeRevision).not.toHaveBeenCalled();
  });

  it('saves authored content, reopens the same draft, previews and finalises through the current workflow', async () => {
    mount('ilr'); fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    let editor = await screen.findByRole('dialog', { name: 'Learner document revision editor' });
    const surface = within(editor).getByRole('textbox', { name: 'Document page 1' });
    surface.insertAdjacentHTML('beforeend', '<p>New learner-specific agreement terms.</p>'); fireEvent.input(surface);
    fireEvent.change(within(editor).getByLabelText('Reason for creating document (required)'), { target: { value: 'New learner document' } });
    fireEvent.click(within(editor).getByRole('button', { name: 'Save revision draft' }));
    await waitFor(() => expect(within(editor).getByRole('button', { name: 'Preview saved revision' })).toBeEnabled());
    expect(api.saveRevision.mock.calls[0][3].blocks).toEqual(expect.arrayContaining([expect.objectContaining({ text: 'New learner-specific agreement terms.' })]));
    expect(api.saveRevision.mock.calls[0][5]).toBe('sign-again');
    fireEvent.click(within(editor).getByRole('button', { name: 'Close editor' }));
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue editing' }));
    editor = await screen.findByRole('dialog', { name: 'Learner document revision editor' });
    expect(api.readRevision).toHaveBeenCalledWith('20', 'ilr-r2');
    expect(within(editor).getByRole('textbox', { name: 'Document page 1' })).toHaveTextContent('New learner-specific agreement terms.');
    fireEvent.click(within(editor).getByRole('button', { name: 'Preview saved revision' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reviewed new document' }));
    fireEvent.click(within(editor).getByRole('button', { name: 'Finalise revision' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Learner document revision editor' })).not.toBeInTheDocument());
    expect(api.finalizeRevision).toHaveBeenCalledWith('20', 'ilr-r2', 'reviewed-new-document');
    expect(await screen.findByText('Version 2 — Available')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
  });

  it('deletes an eligible new learner draft while preserving the original', async () => {
    mount('ilr'); fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    const editor = await screen.findByRole('dialog', { name: 'Learner document revision editor' });
    fireEvent.click(within(editor).getByRole('button', { name: 'Close editor' }));
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    const confirmation = await screen.findByRole('dialog', { name: 'Delete Draft Revision?' });
    fireEvent.click(within(confirmation).getByRole('button', { name: 'Delete Draft' }));
    expect(await screen.findByText('Draft revision deleted.')).toBeVisible();
    expect(api.discardRevision).toHaveBeenCalledWith('20', expect.objectContaining({ id: 'ilr-r2', creationMode: 'learner' }));
    expect(screen.getByText('Version 1 — Original')).toBeVisible();
    expect(screen.queryByText('Version 2 — Draft')).not.toBeInTheDocument();
  });
});
