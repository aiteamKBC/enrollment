import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ApplicationReview, ReviewAction, ReviewStatus } from '@/api/eligibilityApplications';

const fetchReview = vi.fn(), record = vi.fn();
vi.mock('@/api/eligibilityApplications', () => ({
  fetchApplicationReview: (...args: unknown[]) => fetchReview(...args),
  recordApplicationReview: (...args: unknown[]) => record(...args),
}));
import { ReviewPanel } from './ReviewPanel';
import { REVIEW_LABELS } from './reviewLabels';

const initial: ApplicationReview = {
  status: null, version: 0, history: [], enabled: true, available: true,
  allowedActions: ['approved', 'rejected', 'more_evidence_required'],
};
function decided(action: ReviewAction): ApplicationReview {
  return { ...initial, status: action.action, version: 1,
    allowedActions: action.action === 'more_evidence_required' ? ['approved', 'rejected'] : [],
    history: [{ id: '1', applicationId: '42', version: 1, reviewerId: '7', reviewerName: 'Example Reviewer',
      previousStatus: 'pending_assessment', status: action.action, notes: action.notes, decidedAt: '2026-11-03T12:00:00Z' }],
  };
}
beforeEach(() => {
  fetchReview.mockReset().mockResolvedValue(initial);
  record.mockReset().mockImplementation(async (_id, action) => decided(action));
});
afterEach(cleanup);

async function open() {
  render(<ReviewPanel id="42" />);
  await screen.findByText('No staff review decisions recorded.');
}
async function confirm(action: ReviewStatus) {
  fireEvent.change(screen.getByLabelText('Decision notes (required; staff only)'), { target: { value: 'Employer evidence checked.' } });
  fireEvent.click(screen.getByRole('button', { name: REVIEW_LABELS[action] }));
  return screen.findByRole('dialog');
}

it.each(['approved', 'rejected', 'more_evidence_required'] as const)('requires notes and confirmation before recording %s', async action => {
  await open();
  fireEvent.click(screen.getByRole('button', { name: REVIEW_LABELS[action] }));
  expect(screen.getByRole('alert')).toHaveTextContent('Decision notes are required.');
  expect(record).not.toHaveBeenCalled();
  const dialog = await confirm(action);
  expect(within(dialog).getByText(/No learner account, invitation or email/)).toBeInTheDocument();
  expect(record).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Record decision' }));
  await screen.findByText('Review recorded. No account, invitation or email was created.');
  expect(record).toHaveBeenCalledWith('42', { action, notes: 'Employer evidence checked.', expectedVersion: 0, requestId: expect.any(String) });
  expect(screen.getByText(/Example Reviewer/)).toHaveTextContent('Reviewer #7');
  expect(screen.getByText('Employer evidence checked.')).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  if (action !== 'more_evidence_required') expect(screen.queryByLabelText('Decision notes (required; staff only)')).not.toBeInTheDocument();
});

it('cancel and Escape do not record a decision', async () => {
  await open();
  let dialog = await confirm('approved');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(record).not.toHaveBeenCalled();
  dialog = await confirm('rejected');
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(record).not.toHaveBeenCalled();
});

it('prevents repeated clicks and retains the same request ID for a failed-request retry', async () => {
  let reject!: (reason: Error) => void;
  record.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  await open();
  const dialog = await confirm('approved');
  const button = within(dialog).getByRole('button', { name: 'Record decision' });
  fireEvent.click(button); fireEvent.click(button);
  expect(record).toHaveBeenCalledTimes(1);
  expect(within(dialog).getByRole('button', { name: 'Recording…' })).toBeDisabled();
  reject(new Error('Connection interrupted. Retry this decision.'));
  await within(dialog).findByRole('alert');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Record decision' }));
  await screen.findByText(/Review recorded/);
  expect(record).toHaveBeenCalledTimes(2);
  expect(record.mock.calls[1]).toEqual(record.mock.calls[0]);
});

it('requires reloading after a conflicting reviewer decision', async () => {
  record.mockRejectedValue(Object.assign(new Error('Another reviewer has changed this application.'), { status: 409 }));
  await open();
  const dialog = await confirm('rejected');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Record decision' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Reload review history' }));
  await waitFor(() => expect(fetchReview).toHaveBeenCalledTimes(2));
  expect(record).toHaveBeenCalledTimes(1);
});

it('shows history while decision writes are disabled', async () => {
  fetchReview.mockResolvedValue({ ...decided({ action: 'approved', notes: 'Checked', expectedVersion: 0, requestId: 'unused' }), enabled: false });
  render(<ReviewPanel id="42" />);
  expect(await screen.findByText('Review decisions are disabled in this environment.')).toBeInTheDocument();
  expect(screen.getByText('Checked')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Approved|Rejected|More evidence/ })).not.toBeInTheDocument();
});

it('does not offer actions for historical records or unavailable review storage', async () => {
  fetchReview.mockResolvedValue({ ...initial, enabled: false, available: false, allowedActions: [] });
  render(<ReviewPanel id="42" />);
  expect(await screen.findByText('Review history is not available in this environment.')).toBeInTheDocument();
  expect(screen.queryByLabelText('Decision notes (required; staff only)')).not.toBeInTheDocument();
  expect(record).not.toHaveBeenCalled();
});

it('offers a history retry after a permission or service failure without enabling decisions', async () => {
  fetchReview.mockRejectedValueOnce(new Error('Review access unavailable.'));
  render(<ReviewPanel id="42" />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Review access unavailable.');
  expect(screen.queryByLabelText('Decision notes (required; staff only)')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry review history' }));
  await screen.findByText('No staff review decisions recorded.');
});
