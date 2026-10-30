/**
 * Extended ILR — Eligibility evidence is uploaded to storage when picked, not
 * kept as a list of file names in the answers.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const api = vi.hoisted(() => ({
  fetchIlrEvidence: vi.fn(),
  uploadIlrEvidence: vi.fn(),
  deleteIlrEvidence: vi.fn(),
  getIlrEvidenceUrl: vi.fn(),
}));
vi.mock('@/api/extendedIlr', () => api);
vi.mock("../WizardContext.helpers", () => ({ useWizard: () => ({ userId: '41', isCommercial: false }) }));
const toast = vi.hoisted(() => ({ error: vi.fn() }));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: toast.error }) }));

import IlrEligibilityEvidence from '../steps/IlrEligibilityEvidence';

const PASSPORT = { id: 'f1', filename: 'passport.pdf', contentType: 'application/pdf', sizeBytes: 4, uploadedAt: '2026-09-27T10:00:00Z' };

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ILR eligibility evidence', () => {
  it('uploads a picked file for this learner and lists what the server stored', async () => {
    api.fetchIlrEvidence.mockResolvedValue({ results: [], locked: false });
    api.uploadIlrEvidence.mockResolvedValue(PASSPORT);
    render(<IlrEligibilityEvidence />);
    await screen.findByText('No evidence uploaded');

    const file = new File(['%PDF'], 'passport.pdf', { type: 'application/pdf' });
    await userEvent.upload(screen.getByLabelText(/upload file/i), file);

    expect(api.uploadIlrEvidence).toHaveBeenCalledWith('apprenticeship', '41', file);
    expect(await screen.findByRole('button', { name: 'passport.pdf' })).toBeInTheDocument();
  });

  it('reports a refused upload by file name', async () => {
    api.fetchIlrEvidence.mockResolvedValue({ results: [], locked: false });
    api.uploadIlrEvidence.mockRejectedValue(new Error('File exceeds the 25 MB size limit.'));
    render(<IlrEligibilityEvidence />);
    await screen.findByText('No evidence uploaded');

    await userEvent.upload(screen.getByLabelText(/upload file/i), new File(['x'], 'big.pdf', { type: 'application/pdf' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not upload big.pdf', 'File exceeds the 25 MB size limit.'));
    expect(screen.getByText('No evidence uploaded')).toBeInTheDocument();
  });

  it('removes a file through the server while the ILR is unsigned', async () => {
    api.fetchIlrEvidence.mockResolvedValue({ results: [PASSPORT], locked: false });
    api.deleteIlrEvidence.mockResolvedValue(undefined);
    render(<IlrEligibilityEvidence />);

    await userEvent.click(await screen.findByRole('button', { name: 'Delete passport.pdf' }));

    expect(api.deleteIlrEvidence).toHaveBeenCalledWith('apprenticeship', '41', 'f1');
    await waitFor(() => expect(screen.queryByText('passport.pdf')).not.toBeInTheDocument());
  });

  it('is read-only once the ILR is signed', async () => {
    api.fetchIlrEvidence.mockResolvedValue({ results: [PASSPORT], locked: true });
    render(<IlrEligibilityEvidence />);

    expect(await screen.findByRole('button', { name: 'passport.pdf' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete passport.pdf' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/upload file/i)).not.toBeInTheDocument();
    expect(screen.getByText(/can no longer be changed/)).toBeInTheDocument();
  });
});
