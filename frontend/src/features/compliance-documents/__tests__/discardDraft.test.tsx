import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { payloadFor } from './fixtures';
import type { DocumentRevision } from '../revisionApi';

const state = vi.hoisted(() => ({ role: 'staff' }));
const api = vi.hoisted(() => ({ revisionHistory: vi.fn(), readRevision: vi.fn(), startRevision: vi.fn(), discardRevision: vi.fn() }));
vi.mock('../revisionApi', () => api);
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { role: state.role } } }) }));
vi.mock('../LearnerRevisionEditor', () => ({ LearnerRevisionEditor: ({ initial }: { initial: { revision: DocumentRevision } }) => <div role="dialog" aria-label="Existing revision editor">{initial.revision.id}</div> }));
vi.mock('../DocumentPreview', () => ({ DocumentPreview: () => <div role="dialog" aria-label="Existing document viewer" /> }));
vi.mock('../previewPdf', () => ({ previewPdf: vi.fn(async () => new Blob(['pdf'], { type: 'application/pdf' })) }));
import { LearnerDocumentRevisions } from '../LearnerDocumentRevisions';

const row = (number: number): DocumentRevision => ({ id: `r${number}`, issueId:'issue', sourceId:'issued-aa', documentKey:'apprenticeship-agreement', title:'Agreement', number, baseId:number > 1 ? 'r1' : null, state:number === 1 ? 'original' : 'draft', editVersion:2, signatureMethod:number === 1 ? 'original' : '', status:number === 1 ? 'Signed' : 'Draft', fullySigned:number === 1, canDiscard:number > 1, templateVersionId:'v1', templateVersionNumber:1, createdBy:'Admin', createdAt:'2030-01-01', finalizedAt:number === 1 ? '2030-01-01' : null, reason:'Correction', changes:{}, checksum:'checksum', signatures:{}, provenance:{} });
let rows: DocumentRevision[];
const mount = () => render(<MemoryRouter><LearnerDocumentRevisions learnerId="20" documentKey="apprenticeship-agreement" sourceId="issued-aa" /></MemoryRouter>);
const openHistory = async () => { fireEvent.click(await screen.findByRole('button', { name:'History' })); return screen.findByRole('dialog', { name:'Document revision history' }); };
const openDelete = async () => { const history = await openHistory(); fireEvent.click(within(history).getByRole('button', { name:'Delete' })); return screen.findByRole('dialog', { name:'Delete Draft Revision?' }); };

beforeEach(() => {
  vi.clearAllMocks(); state.role = 'staff'; rows = [row(1), row(2)];
  api.revisionHistory.mockImplementation(async () => ({ results:[...rows], setupRequired:false }));
  api.readRevision.mockImplementation(async (_learner, id) => ({ revision:rows.find(r => r.id === id), preview:payloadFor('apprenticeship-agreement') }));
  api.discardRevision.mockImplementation(async () => { rows = rows.filter(r => r.id !== 'r2'); return { discarded:true, id:'r2' }; });
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:synthetic'); vi.spyOn(URL,'revokeObjectURL').mockImplementation(() => {});
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('admin draft discard from existing revision History', () => {
  it('offers Delete next to Continue editing only for the eligible draft', async () => {
    mount(); const history = await openHistory(); const cards = within(history).getAllByRole('article');
    expect(within(cards[0]).queryByRole('button', { name:'Delete' })).not.toBeInTheDocument();
    expect(within(cards[1]).getByRole('button', { name:'Continue editing' })).toBeEnabled();
    expect(within(cards[1]).getByRole('button', { name:'Delete' })).toBeEnabled();
    for (const label of ['View','Download','Compare']) expect(within(cards[1]).getByRole('button', { name:label })).toBeEnabled();
  });
  it('requires confirmation and Cancel preserves the draft without an API write', async () => {
    mount(); const confirmation = await openDelete();
    expect(confirmation).toHaveTextContent('Are you sure you want to delete Version 2 — Draft?');
    expect(confirmation).toHaveTextContent('The original document, previous versions and signatures will remain unchanged.');
    expect(api.discardRevision).not.toHaveBeenCalled();
    fireEvent.click(within(confirmation).getByRole('button', { name:'Cancel' }));
    expect(screen.queryByRole('dialog', { name:'Delete Draft Revision?' })).not.toBeInTheDocument();
    expect(within(screen.getByRole('dialog', { name:'Document revision history' })).getByText(/Revision R2/)).toBeVisible();
    expect(api.discardRevision).not.toHaveBeenCalled();
  });
  it('Escape cancels only the confirmation and keeps History open', async () => {
    mount(); await openDelete();
    fireEvent.keyDown(document, { key:'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name:'Delete Draft Revision?' })).not.toBeInTheDocument());
    expect(screen.getByRole('dialog', { name:'Document revision history' })).toBeVisible();
    expect(api.discardRevision).not.toHaveBeenCalled();
  });
  it('discards only the selected draft, refreshes History and stays discarded after remount', async () => {
    const view = mount(); const confirmation = await openDelete();
    fireEvent.click(within(confirmation).getByRole('button', { name:'Delete Draft' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name:'Delete Draft Revision?' })).not.toBeInTheDocument());
    expect(api.discardRevision).toHaveBeenCalledWith('20', expect.objectContaining({ id:'r2', sourceId:'issued-aa', documentKey:'apprenticeship-agreement', editVersion:2 }));
    const history = screen.getByRole('dialog', { name:'Document revision history' });
    expect(within(history).queryByText(/Revision R2/)).not.toBeInTheDocument();
    expect(within(history).getByText(/Revision R1/)).toHaveTextContent('Signed');
    expect(screen.getByRole('status')).toHaveTextContent('Draft revision deleted.');
    view.unmount(); mount(); await screen.findByText('Version 1 — Original');
    expect(screen.queryByText('Version 2 — Draft')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name:'Continue editing' })).not.toBeInTheDocument();
  });
  it('disables confirmation controls during deletion and handles a rejected request visibly', async () => {
    let reject!: (error:Error) => void;
    api.discardRevision.mockImplementation(() => new Promise((_resolve, rejectPromise) => { reject = rejectPromise; }));
    mount(); const confirmation = await openDelete();
    fireEvent.click(within(confirmation).getByRole('button', { name:'Delete Draft' }));
    expect(within(confirmation).getByRole('button', { name:'Deleting…' })).toBeDisabled();
    expect(within(confirmation).getByRole('button', { name:'Cancel' })).toBeDisabled();
    reject(new Error('This revision was finalised elsewhere.'));
    expect(await within(confirmation).findByRole('alert')).toHaveTextContent('This revision was finalised elsewhere.');
    expect(within(screen.getByRole('dialog', { name:'Document revision history' })).getByText(/Revision R2/)).toBeVisible();
    expect(screen.queryByText('Draft revision deleted.')).not.toBeInTheDocument();
  });
  it.each(['original','finalized','partly-signed','ineligible-draft'])('never offers Delete for %s', async (kind) => {
    const candidate = row(2); candidate.canDiscard = false;
    if (kind === 'original') candidate.state = 'original';
    if (kind === 'finalized' || kind === 'partly-signed') { candidate.state = 'finalized'; candidate.signatureMethod = 'sign-again'; candidate.finalizedAt = '2030-01-01'; }
    rows = [row(1),candidate]; mount(); const history = await openHistory();
    expect(within(history).queryByRole('button', { name:'Delete' })).not.toBeInTheDocument();
  });
  it('does not offer learner access to draft Delete even if a response includes canDiscard', async () => {
    state.role = 'learner'; mount(); await screen.findByText('Version 2 — Draft');
    expect(screen.queryByRole('button', { name:'Delete' })).not.toBeInTheDocument();
  });
  it('preserves remaining View, Download, Compare and Continue editing actions', async () => {
    mount(); const region = await screen.findByRole('region', { name:'apprenticeship-agreement document revisions' });
    await within(region).findByText('Version 2 — Draft');
    const draft = within(region).getAllByRole('article')[1];
    fireEvent.click(within(draft).getByRole('button', { name:'Compare' }));
    expect(await screen.findByRole('dialog', { name:'Revision R2 changes' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name:'Close comparison' }));
    fireEvent.click(within(draft).getByRole('button', { name:'Continue editing' }));
    expect(await screen.findByRole('dialog', { name:'Existing revision editor' })).toHaveTextContent('r2');
    expect(api.readRevision).toHaveBeenCalledWith('20','r2');
    fireEvent.click(within(draft).getByRole('button', { name:'Download' }));
    await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled());
    fireEvent.click(within(draft).getByRole('button', { name:'View' }));
    expect(await screen.findByRole('dialog', { name:'Existing document viewer' })).toBeVisible();
  });
});
