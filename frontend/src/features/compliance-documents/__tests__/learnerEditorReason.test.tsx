import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { payloadFor } from './fixtures';
import { LearnerRevisionEditor } from '../LearnerRevisionEditor';
const api = vi.hoisted(() => ({ saveRevision: vi.fn(), previewRevision: vi.fn(), finalizeRevision: vi.fn(), readRevision: vi.fn() }));
vi.mock('../revisionApi', () => api);
vi.mock('../api', () => ({ readFields: async () => ({ results: [] }) }));
vi.mock('../FullPageEditor', () => ({ FullPageEditor: () => <p>Document content</p> }));
vi.mock('../DocumentDesignControl', () => ({ DocumentDesignControl: () => null }));
vi.mock('../DocumentPreview', () => ({ DocumentPreview: ({ value, onReady, onClose }: any) => <div><button onClick={() => { onReady(value); onClose(); }}>Review PDF</button></div> }));
const onChanged = vi.fn(), onClose = vi.fn();
function detail(creationMode = 'learner', reason = ''): any {
  return { revision: { id: 'draft', number: 3, editVersion: 1, signatureMethod: 'sign-again', creationMode, reason, provenance: {} }, preview: payloadFor('training-plan') };
}
beforeEach(() => {
  vi.clearAllMocks();
  api.previewRevision.mockResolvedValue({ ...payloadFor('training-plan'), previewToken: 'reviewed-token' });
  api.finalizeRevision.mockResolvedValue({});
});

it('finalizes an unchanged Create after PDF review with no reason or signature choice required', async () => {
  render(<LearnerRevisionEditor learnerId="1" initial={detail()} onChanged={onChanged} onClose={onClose} />);
  expect(screen.getByLabelText('Reason for creating document (optional)')).toHaveValue('');
  expect(screen.queryByRole('button', { name: 'Keep Existing Signatures' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save revision draft' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Finalise revision' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview saved revision' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Review PDF' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Finalise revision' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Finalise revision' }));
  await waitFor(() => expect(api.finalizeRevision).toHaveBeenCalledWith('1', 'draft', 'reviewed-token'));
  expect(api.saveRevision).not.toHaveBeenCalled();
});

it.each(['', 'Optional creation note'])('saves a Create draft with optional reason %j', async reason => {
  api.saveRevision.mockResolvedValue(detail('learner', reason));
  render(<LearnerRevisionEditor learnerId="1" initial={detail()} onChanged={onChanged} onClose={onClose} />);
  if (reason) fireEvent.change(screen.getByLabelText('Reason for creating document (optional)'), { target: { value: reason } });
  fireEvent.click(screen.getByRole('button', { name: 'Save revision draft' }));
  await waitFor(() => expect(api.saveRevision).toHaveBeenCalledWith('1', 'draft', 1, expect.any(Object), reason, 'sign-again'));
});

it('keeps a reason mandatory for Edit and offers both signature options', () => {
  render(<LearnerRevisionEditor learnerId="1" initial={detail('edit')} onChanged={onChanged} onClose={onClose} />);
  expect(screen.getByLabelText('Reason for change (required)')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Keep Existing Signatures' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Sign Again' })).toBeEnabled();
  for (const name of ['Save revision draft', 'Preview saved revision', 'Finalise revision']) expect(screen.getByRole('button', { name })).toBeDisabled();
});
