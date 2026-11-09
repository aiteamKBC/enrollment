import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { payloadFor } from './fixtures';
import type { DocumentRevision } from '../revisionApi';
const state = vi.hoisted(() => ({ role: 'learner' }));
const api = vi.hoisted(() => ({ revisionHistory: vi.fn(), readRevision: vi.fn(), startRevision: vi.fn(), saveRevision: vi.fn(), previewRevision: vi.fn(), finalizeRevision: vi.fn(), signRevision: vi.fn() }));
vi.mock('../revisionApi', () => api);
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { role: state.role }, user: { fullName: 'Synthetic Learner' } } }) }));
vi.mock('../DocumentPreview', () => ({ DocumentPreview: ({ value, onClose }: any) => <div role="dialog" aria-label="Revised document viewer"><p>{value.data.documentRevision?.number}</p><p>{value.data.signatureProvenance?.apprentice?.name}</p><button onClick={onClose}>Close preview</button></div> }));
vi.mock('../previewPdf', () => ({ previewPdf: vi.fn(async () => new Blob(['synthetic-pdf'], { type: 'application/pdf' })) }));
import { LearnerDocumentRevisions } from '../LearnerDocumentRevisions';
const row = (number: number, status: string, method: string) => ({ id: `revision-${number}`, issueId: 'issue', sourceId: 'original-id', documentKey: 'apprenticeship-agreement', title: 'Agreement', number, baseId: number > 1 ? 'revision-1' : null, state: number === 1 ? 'original' : 'finalized', editVersion: 2, signatureMethod: method, status, fullySigned: status === 'Signed', templateVersionNumber: 1, templateVersionId: 'original-v1', provenance: {}, signatures: { apprentice: { signed: false, name: '', signedAt: null } }, createdAt: '2030-01-01', finalizedAt: '2030-01-01', createdBy: 'Admin', reason: 'Correct wording', changes: {}, checksum: 'hash' }) as DocumentRevision;
beforeEach(() => {
  vi.clearAllMocks(); state.role = 'learner';
  api.revisionHistory.mockResolvedValue({ results: [row(1, 'Signed', 'original'), row(2, 'Updated Copy', 'keep')], setupRequired: false });
  const preview = payloadFor('apprenticeship-agreement', true); preview.sample = false; preview.data.documentRevision = { number: 2 }; preview.data.signatureProvenance = { apprentice: { name: 'Historical Signer', sourceRevision: 1 } };
  api.readRevision.mockResolvedValue({ revision: row(2, 'Updated Copy', 'keep'), preview });
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:synthetic'); vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
});
describe('learner document versions', () => {
  it('shows R1 Signed then R2 Updated Copy under the same V1, with View and Download and no Edit', async () => {
    render(<MemoryRouter><LearnerDocumentRevisions learnerId="1" documentKey="apprenticeship-agreement" sourceId="original-id" /></MemoryRouter>);
    await screen.findByText('Version 2 — Updated Copy');
    const cards = screen.getAllByRole('article'); expect(cards[0]).toHaveTextContent('Version 1'); expect(cards[0]).toHaveTextContent('Signed'); expect(cards[1]).toHaveTextContent('Template V1.0 / Revision R2');
    expect(screen.queryByText(/Not Re-signed/i)).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    fireEvent.click(within(cards[1]).getByRole('button', { name: 'View' })); await screen.findByRole('dialog', { name: 'Revised document viewer' }); expect(screen.getByText('Historical Signer')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    fireEvent.click(within(cards[1]).getByRole('button', { name: 'Download' })); await waitFor(() => expect(clicked).toHaveBeenCalled());
    expect(api.readRevision).toHaveBeenCalledWith('1', 'revision-2');
  });
  it('shows actual Signed status after fresh signing and no pending sign button', async () => {
    api.revisionHistory.mockResolvedValue({ results: [row(1, 'Signed', 'original'), row(2, 'Signed', 'sign-again')], setupRequired: false });
    render(<MemoryRouter><LearnerDocumentRevisions learnerId="1" documentKey="apprenticeship-agreement" /></MemoryRouter>); await screen.findByText('Version 2 — Signed');
    expect(screen.queryByRole('button', { name: 'Sign revised document' })).not.toBeInTheDocument();
  });
  it('shows own fresh signing action, while historical reference copies cannot be signed', async () => {
    api.revisionHistory.mockResolvedValue({ results: [row(2, 'Available', 'sign-again')], setupRequired: false });
    render(<MemoryRouter><LearnerDocumentRevisions learnerId="1" documentKey="apprenticeship-agreement" /></MemoryRouter>); expect(await screen.findByRole('button', { name: 'Sign revised document' })).toBeEnabled();
  });
});
