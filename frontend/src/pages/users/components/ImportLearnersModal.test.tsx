import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fetchEnrolmentUserTemplate,
  importEnrolmentUsers,
  LearnerImportError,
  type LearnerImportResult,
} from '@/api/enrolmentUserImport';
import type { UserListRow } from '../types';
import { DownloadLearnerTemplateButton } from './DownloadLearnerTemplateButton';
import { ImportLearnersModal } from './ImportLearnersModal';

vi.mock('@/api/enrolmentUserImport', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/enrolmentUserImport')>(),
  importEnrolmentUsers: vi.fn(), fetchEnrolmentUserTemplate: vi.fn(),
}));

const learner: UserListRow = {
  id: '101', uuid: null, source: 'commercial', name: 'New Learner', email: 'new@example.test', type: 'User',
  group: 'A', programme: 'Test programme', cohort: 'October', subscriptionStatus: 'FullUser',
  subscriptionVerified: false, learningPlan: false,
};
const preview: LearnerImportResult = {
  count: 1, imported: 0, results: [], errors: [],
  preview: [{ row: 2, name: learner.name, email: learner.email, programme: 'Test programme', cohort: 'October', group: 'A' }],
};
const imported: LearnerImportResult = { ...preview, imported: 1, results: [learner] };
const file = new File(['workbook'], 'learners.xlsx');
const selectFile = (selected = file) => fireEvent.change(screen.getByLabelText('Completed template'), { target: { files: [selected] } });

beforeEach(() => { vi.mocked(importEnrolmentUsers).mockResolvedValue(preview); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); });

describe('ImportLearnersModal', () => {
  it('validates without saving, then imports only after the explicit import action', async () => {
    const onImported = vi.fn();
    render(<ImportLearnersModal onClose={vi.fn()} onImported={onImported} />);
    expect(screen.getByRole('button', { name: 'Validate file' })).toBeDisabled();
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
    expect(await screen.findByText('1 learner ready to import. No learners have been saved yet.')).toBeInTheDocument();
    expect(importEnrolmentUsers).toHaveBeenCalledWith(file, true);
    expect(importEnrolmentUsers).toHaveBeenCalledTimes(1);
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.getByText('new@example.test')).toBeInTheDocument();

    vi.mocked(importEnrolmentUsers).mockResolvedValueOnce(imported);
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 learners' }));
    expect(await screen.findByText('1 learner imported successfully.')).toBeInTheDocument();
    expect(importEnrolmentUsers).toHaveBeenLastCalledWith(file, false);
    expect(onImported).toHaveBeenCalledWith([learner]);
    expect(screen.queryByRole('button', { name: /Import \d/ })).not.toBeInTheDocument();
  });

  it('shows workbook row errors and prevents import when validation fails', async () => {
    const onImported = vi.fn();
    vi.mocked(importEnrolmentUsers).mockRejectedValueOnce(new LearnerImportError('Please correct the workbook.', {
      ...preview, errors: [{ row: 2, field: 'Email', message: 'This email already exists.' }],
    }));
    render(<ImportLearnersModal onClose={vi.fn()} onImported={onImported} />);
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
    expect(await screen.findByText('Row 2 (Email):')).toBeInTheDocument();
    expect(screen.getByText(/This email already exists/)).toBeInTheDocument();
    expect(screen.getByText(/No learners were imported/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Import \d/ })).not.toBeInTheDocument();
    expect(onImported).not.toHaveBeenCalled();
  });

  it('invalidates the preview when a different workbook is selected', async () => {
    render(<ImportLearnersModal onClose={vi.fn()} onImported={vi.fn()} />);
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
    await screen.findByRole('button', { name: 'Import 1 learners' });
    selectFile(new File(['replacement'], 'updated.xlsx'));
    expect(screen.queryByRole('button', { name: 'Import 1 learners' })).not.toBeInTheDocument();
    expect(screen.queryByText('new@example.test')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Validate file' })).toBeEnabled();
  });

  it('blocks duplicate submissions and dismissal while learners are being saved', async () => {
    const onClose = vi.fn();
    render(<ImportLearnersModal onClose={onClose} onImported={vi.fn()} />);
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
    await screen.findByRole('button', { name: 'Import 1 learners' });
    let finish!: (value: LearnerImportResult) => void;
    vi.mocked(importEnrolmentUsers).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 learners' }));
    fireEvent.click(screen.getByRole('button', { name: 'Importing...' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(importEnrolmentUsers).toHaveBeenCalledTimes(2);
    await act(async () => { finish(imported); });
    expect(screen.getByRole('button', { name: 'Done' })).toBeEnabled();
  });

  it('keeps account provisioning warnings after successful learner creation', async () => {
    render(<ImportLearnersModal onClose={vi.fn()} onImported={vi.fn()} />);
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
    await screen.findByRole('button', { name: 'Import 1 learners' });
    vi.mocked(importEnrolmentUsers).mockResolvedValueOnce({ ...imported, results: [{
      ...learner, invitation: { accountCreated: false, invited: false, emailSent: false, error: 'Account service unavailable.', expiresAt: null },
    }] });
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 learners' }));
    expect(await screen.findByText('Account setup needs attention')).toBeInTheDocument();
    expect(screen.getByText(/Account service unavailable/)).toBeInTheDocument();
    expect(screen.getByText('1 learner imported successfully.')).toBeInTheDocument();
  });

  it('treats an account awaiting invitation as a successful import', async () => {
    render(<ImportLearnersModal onClose={vi.fn()} onImported={vi.fn()} />);
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
    await screen.findByRole('button', { name: 'Import 1 learners' });
    vi.mocked(importEnrolmentUsers).mockResolvedValueOnce({ ...imported, results: [{
      ...learner, invitation: { accountCreated: true, invited: false, emailSent: false, awaitingInvitation: true, error: null, expiresAt: null },
    }] });
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 learners' }));
    await screen.findByText('1 learner imported successfully.');
    expect(screen.queryByText('Account setup needs attention')).not.toBeInTheDocument();
  });

  it('requires fresh validation if an import request fails', async () => {
    render(<ImportLearnersModal onClose={vi.fn()} onImported={vi.fn()} />);
    selectFile();
    fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
    await screen.findByRole('button', { name: 'Import 1 learners' });
    vi.mocked(importEnrolmentUsers).mockRejectedValueOnce(new Error('Could not confirm import.'));
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 learners' }));
    await screen.findByText('Could not confirm import.');
    expect(screen.queryByRole('button', { name: 'Import 1 learners' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Validate file' })).toBeEnabled();
  });

  it.each([
    [new File(['data'], 'learners.csv'), /Choose an Excel workbook/],
    [new File([], 'empty.xlsx'), /The file is empty/],
    [new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'large.xlsx'), /The file is too large/],
  ])('rejects an unsuitable file before making an upload request', (selected, message) => {
    render(<ImportLearnersModal onClose={vi.fn()} onImported={vi.fn()} />);
    selectFile(selected);
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(screen.getByRole('button', { name: 'Validate file' })).toBeDisabled();
    expect(importEnrolmentUsers).not.toHaveBeenCalled();
  });
});

describe('DownloadLearnerTemplateButton', () => {
  it('starts an Excel download and releases the blob URL', async () => {
    const blob = new Blob(['workbook']);
    vi.mocked(fetchEnrolmentUserTemplate).mockResolvedValueOnce(blob);
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:template');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<DownloadLearnerTemplateButton />);
    fireEvent.click(screen.getByRole('button', { name: 'Download template' }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith(blob);
    const anchor = click.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe('learners-import-template.xlsx');
    expect(anchor.href).toBe('blob:template');
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:template'), { timeout: 1500 });
  });
});
