import { beforeEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppIcon } from '@/components/feature/AppIcon';
import type { PlrPdfPreviewResult } from '@/api/plrPdfPreview';

const api = vi.hoisted(() => ({ preview: vi.fn() }));
vi.mock('@/api/plrPdfPreview', () => ({ previewPlrPdf: api.preview }));
vi.mock('@/api/plrEvidence', () => ({ fetchPlrEvidence: async () => [], uploadPlrEvidence: vi.fn(), deletePlrEvidence: vi.fn(), getPlrEvidenceUrl: vi.fn() }));
vi.mock('@/hooks/useToast.helpers', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
const wizard = vi.hoisted(() => ({ admin: true, id: '41', uln: '0123456789', setSection: vi.fn(), saveIlr: vi.fn(), importPlrPdf: vi.fn() }));
vi.mock('../WizardContext.helpers', () => ({ useWizard: () => ({
  userId: wizard.id, isCommercial: false, canEditUln: wizard.admin, setSection: wizard.setSection, saveIlr: wizard.saveIlr,
  importPlrPdf: wizard.importPlrPdf,
  draft: { plr: { uln: wizard.uln, records: [{ id: 'saved', subject: 'Existing qualification', grade: '5', credits: 0 }] } },
}) }));
import Plr from '../steps/Plr';

const result: PlrPdfPreviewResult = {
  previewOnly: true, uln: '0123456789', learnerName: 'Synthetic Learner', dateOfBirth: '1994-03-12', identityVerified: true,
  counts: { learningEvents: 1, achievements: 0 }, warnings: ['Synthetic validation warning'],
  records: [{ placeOfStudy: 'Synthetic College', qualificationType: 'Award', subject: 'Temporary PDF qualification', level: '',
    awardDate: '', credits: '', grade: 'L1', recordType: 'Imported', sourceSection: 'Learning Events', awardingBody: 'Synthetic Awards',
    referenceNumber: 'SYN-001', startDate: '', endDate: '2020-06-30', completionStatus: 'Withdrawn', outcomeStatus: 'No Achievement', language: '' }],
};
const pdf = () => new File(['%PDF-synthetic'], 'synthetic.pdf', { type: 'application/pdf' });

beforeEach(() => {
  vi.clearAllMocks(); vi.stubGlobal('AppIcon', AppIcon);
  wizard.admin = true; wizard.id = '41'; wizard.uln = '0123456789';
  api.preview.mockResolvedValue(structuredClone(result));
  wizard.importPlrPdf.mockResolvedValue({ imported: true, plr: { uln: result.uln, records: [] }, added: 1, skipped: 0,
    message: 'PLR import complete. 1 records imported and 0 records skipped.' });
});

it('shows the upload button only to Admins and preserves Add and table headers', () => {
  const { rerender } = render(<Plr />);
  const button = screen.getByRole('button', { name: 'Import PLR PDF' });
  expect(screen.getByRole('button', { name: 'Add' }).parentElement).toContainElement(button);
  expect(screen.getByRole('textbox', { name: 'ULN' })).toHaveValue('0123456789');
  expect(screen.getByRole('columnheader', { name: 'Edit' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Delete' })).toBeInTheDocument();
  wizard.admin = false; rerender(<Plr />);
  expect(screen.queryByRole('button', { name: 'Import PLR PDF' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument();
});

it('previews values and withdrawal statuses without saving or changing the existing ULN/table', async () => {
  render(<Plr />);
  const file = pdf();
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), file);
  const modal = await screen.findByRole('dialog', { name: 'PLR PDF preview' });
  expect(api.preview).toHaveBeenCalledWith('apprenticeship', '41', file, '0123456789', expect.any(AbortSignal));
  expect(within(modal).getByText('Temporary PDF qualification')).toBeInTheDocument();
  expect(within(modal).getByText('Withdrawn')).toBeInTheDocument();
  expect(within(modal).getByText('No Achievement')).toBeInTheDocument();
  expect(within(modal).getByText('Synthetic validation warning')).toBeInTheDocument();
  const cells = within(within(modal).getAllByRole('row')[1]).getAllByRole('cell');
  for (const column of [6, 9, 10]) expect(cells[column]).toBeEmptyDOMElement();
  expect(within(modal).queryByRole('button', { name: /save|confirm import/i })).not.toBeInTheDocument();
  expect(wizard.setSection).not.toHaveBeenCalled(); expect(wizard.saveIlr).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox', { name: 'ULN' })).toHaveValue('0123456789');
  expect(screen.getByText('Existing qualification')).toBeInTheDocument();
  await userEvent.click(within(modal).getByRole('button', { name: 'Close preview' }));
  expect(screen.queryByText('Temporary PDF qualification')).not.toBeInTheDocument();
});

it('shows a loading indicator without blocking existing Add functionality', async () => {
  let finish!: (value: PlrPdfPreviewResult) => void;
  api.preview.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  render(<Plr />);
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), pdf());
  expect(screen.getByRole('button', { name: 'Processing PDF…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled();
  await act(async () => { finish(result); });
  expect(await screen.findByRole('dialog', { name: 'PLR PDF preview' })).toBeInTheDocument();
});

it('keeps parser/identity errors local to PLR and preserves existing records', async () => {
  api.preview.mockRejectedValueOnce(new Error('The learner name in the PDF does not match the selected student.'));
  render(<Plr />);
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), pdf());
  expect(await screen.findByRole('alert')).toHaveTextContent('does not match');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByText('Existing qualification')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled();
  expect(wizard.setSection).not.toHaveBeenCalled(); expect(wizard.saveIlr).not.toHaveBeenCalled();
});

it('rejects invalid and oversized files before sending an upload', async () => {
  render(<Plr />);
  for (const file of [new File(['text'], 'synthetic.txt', { type: 'text/plain' }),
    new File([], 'empty.pdf', { type: 'application/pdf' }),
    new File([new Uint8Array(8 * 1024 * 1024 + 1)], 'large.pdf', { type: 'application/pdf' })]) {
    fireEvent.change(screen.getByLabelText('Select PLR PDF'), { target: { files: [file] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('no larger than 8 MB');
  }
  expect(api.preview).not.toHaveBeenCalled();
});

it('accepts a temporary zero-record preview without adding a ULN requirement', async () => {
  wizard.uln = '';
  api.preview.mockResolvedValueOnce({ ...result, records: [], counts: { learningEvents: 0, achievements: 0 } });
  render(<Plr />);
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), pdf());
  const modal = await screen.findByRole('dialog', { name: 'PLR PDF preview' });
  expect(within(modal).getByText('The report contains no qualification records.')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'ULN' })).toHaveValue('');
  expect(wizard.setSection).not.toHaveBeenCalled();
});

it('cancels and discards a pending preview when the selected student changes', async () => {
  let finish!: (value: PlrPdfPreviewResult) => void;
  api.preview.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const { rerender } = render(<Plr />);
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), pdf());
  const signal = api.preview.mock.calls[0][4] as AbortSignal;
  wizard.id = '42'; rerender(<Plr />);
  expect(signal.aborted).toBe(true);
  await act(async () => { finish(result); });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(wizard.setSection).not.toHaveBeenCalled();
});

it('confirms using the original file and reports imported/skipped counts without Save progress', async () => {
  render(<Plr />);
  const file = pdf();
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), file);
  const modal = await screen.findByRole('dialog', { name: 'PLR PDF preview' });
  expect(wizard.importPlrPdf).not.toHaveBeenCalled();
  await userEvent.click(within(modal).getByRole('button', { name: 'Import Records' }));
  expect(wizard.importPlrPdf).toHaveBeenCalledWith(file, '0123456789', expect.any(AbortSignal));
  expect(await screen.findByRole('status')).toHaveTextContent('1 records imported and 0 records skipped');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(wizard.saveIlr).not.toHaveBeenCalled();
});

it('keeps the preview open when backend import validation fails', async () => {
  wizard.importPlrPdf.mockRejectedValueOnce(new Error('The PDF ULN does not match the selected student.'));
  render(<Plr />);
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), pdf());
  const modal = await screen.findByRole('dialog', { name: 'PLR PDF preview' });
  await userEvent.click(within(modal).getByRole('button', { name: 'Import Records' }));
  expect(await within(modal).findByRole('alert')).toHaveTextContent('does not match');
  expect(within(modal).getByRole('button', { name: 'Import Records' })).toBeEnabled();
  expect(screen.getByText('Existing qualification')).toBeInTheDocument();
});

it('blocks confirmation if learner identity could not be verified', async () => {
  api.preview.mockResolvedValueOnce({ ...result, identityVerified: false });
  render(<Plr />);
  await userEvent.upload(screen.getByLabelText('Select PLR PDF'), pdf());
  const modal = await screen.findByRole('dialog', { name: 'PLR PDF preview' });
  expect(within(modal).getByRole('button', { name: 'Import Records' })).toBeDisabled();
  expect(wizard.importPlrPdf).not.toHaveBeenCalled();
});
