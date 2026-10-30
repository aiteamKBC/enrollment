/**
 * SignaturePad offers the signed-in person's saved signature, and saves a new
 * one for next time — but never for a box someone signs on another's behalf.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const SAVED = 'data:image/png;base64,SAVED';
const api = vi.hoisted(() => ({ fetchSavedSignature: vi.fn(), saveSignature: vi.fn() }));
vi.mock('@/api/savedSignature', () => api);
const auth = vi.hoisted(() => ({
  value: { user: { fullName: 'Sam Staff' }, account: { role: 'staff', subjectType: 'staff', subjectId: 7 } } as Record<string, unknown>,
}));
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ auth: auth.value }) }));

import { SignaturePad } from '../steps/SignaturePad';

const staff = { user: { fullName: 'Sam Staff' }, account: { role: 'staff', subjectType: 'staff', subjectId: 7 } };
const learner = { user: { fullName: 'Lee Learner' }, account: { role: 'learner', subjectType: 'learner', subjectId: 41 } };

beforeEach(() => {
  vi.clearAllMocks();
  auth.value = staff;
  api.fetchSavedSignature.mockResolvedValue({ signature: SAVED, savedAt: '2026-09-01T00:00:00Z' });
  api.saveSignature.mockResolvedValue({ signature: SAVED, savedAt: '' });
});

describe('SignaturePad saved signature', () => {
  it('offers the saved signature and signs with one click', async () => {
    const onCommit = vi.fn();
    render(<SignaturePad onCommit={onCommit} onCancel={vi.fn()} signatoryName="Sam Staff" />);

    expect(await screen.findByRole('img', { name: 'Your saved signature' })).toHaveAttribute('src', SAVED);
    expect(api.fetchSavedSignature).toHaveBeenCalledWith('staff:7');
    await userEvent.click(screen.getByRole('button', { name: /Sign with this signature/ }));

    expect(onCommit).toHaveBeenCalledWith(SAVED);
    // Re-using it is not a new signature to save.
    expect(api.saveSignature).not.toHaveBeenCalled();
  });

  it('lets them sign differently, by drawing or uploading', async () => {
    render(<SignaturePad onCommit={vi.fn()} onCancel={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Use a different signature' }));

    expect(screen.getByLabelText('Draw your signature')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: 'Upload' }));
    expect(screen.getByLabelText('Upload your signature')).toHaveAttribute('accept', 'image/png,image/jpeg');
    expect(screen.getByText(/saved as your signature for next time/)).toBeInTheDocument();
  });

  it('asks for one to be drawn or uploaded when nothing is saved yet', async () => {
    api.fetchSavedSignature.mockResolvedValue({ signature: '', savedAt: '' });
    render(<SignaturePad onCommit={vi.fn()} onCancel={vi.fn()} />);

    await waitFor(() => expect(api.fetchSavedSignature).toHaveBeenCalled());
    expect(screen.queryByRole('img', { name: 'Your saved signature' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Draw' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Upload' })).toBeInTheDocument();
  });

  it('never offers or saves staff’s own signature on a box signed for someone else', async () => {
    render(<SignaturePad onCommit={vi.fn()} onCancel={vi.fn()} signatoryName="Lee Learner" />);

    expect(screen.getByLabelText('Draw your signature')).toBeInTheDocument();
    expect(api.fetchSavedSignature).not.toHaveBeenCalled();
    expect(screen.queryByText(/saved as your signature for next time/)).not.toBeInTheDocument();
  });

  it('stays on "Signing..." until the document is signed, and signs only once however often it is pressed', async () => {
    let finish!: () => void;
    const onCommit = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    render(<SignaturePad onCommit={onCommit} onCancel={vi.fn()} signatoryName="Sam Staff" />);
    const button = await screen.findByRole('button', { name: /Sign with this signature/ });

    await userEvent.click(button);
    const signingButton = await screen.findByRole('button', { name: /Signing\.\.\./ });
    expect(signingButton).toBeDisabled();
    expect(screen.getByTestId('signature-action-spinner')).toHaveClass('animate-spin');
    expect(screen.getByRole('button', { name: 'Use a different signature' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /Signing\.\.\./ }));
    expect(onCommit).toHaveBeenCalledTimes(1);

    finish();
    expect(await screen.findByRole('button', { name: /Sign with this signature/ })).toBeEnabled();
  });

  it('shows the error when signing fails, and lets them try again', async () => {
    const onCommit = vi.fn().mockRejectedValueOnce(new Error('Database error')).mockResolvedValueOnce(undefined);
    render(<SignaturePad onCommit={onCommit} onCancel={vi.fn()} signatoryName="Sam Staff" />);

    await userEvent.click(await screen.findByRole('button', { name: /Sign with this signature/ }));
    expect(await screen.findByText('Database error')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Sign with this signature/ }));
    expect(onCommit).toHaveBeenCalledTimes(2);
  });

  it('treats a learner as always signing for themselves, whatever name the box shows', async () => {
    auth.value = learner;
    render(<SignaturePad onCommit={vi.fn()} onCancel={vi.fn()} signatoryName="LEE  learner (enrolment)" />);

    await screen.findByRole('img', { name: 'Your saved signature' });
    expect(api.fetchSavedSignature).toHaveBeenCalledWith('learner:41');
  });
});
