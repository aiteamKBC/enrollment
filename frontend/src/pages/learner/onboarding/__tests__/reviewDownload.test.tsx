/**
 * Enrolment reviews — a completed review can be downloaded straight from its
 * card, as the same document the review's own Export PDF produces.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { OnboardingReviewSlot } from '@/api/reviewForm';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: 'apprenticeship', id: '670' }) }));
vi.mock('@/hooks/useLearnerNavGate', () => ({ useEnrolmentSubmitted: () => true }));
const form = vi.hoisted(() => ({ fetchReviewForm: vi.fn(), fetchOnboardingReviewSlots: vi.fn() }));
vi.mock('@/api/reviewForm', async (original) => ({ ...(await original<typeof import('@/api/reviewForm')>()), ...form }));
const doc = vi.hoisted(() => ({ downloadReviewPdf: vi.fn() }));
vi.mock('../reviews/reviewDocument', () => doc);

import OnboardingReviewsPage from '../reviews/page';
import { REVIEW_QUESTION_LABELS } from '../reviews/questions';

const unsigned = { signature: '', name: '', signedAt: null, signed: false };
const review = (type: OnboardingReviewSlot['type'], label: string, extra: Partial<OnboardingReviewSlot>): OnboardingReviewSlot =>
  ({ type, label, issued: true, eventKey: `ev-${type}`, signatures: { learner: unsigned, admin: unsigned, signable: false }, ...extra });

const card = (label: string) => screen.getByRole('heading', { name: label }).closest('div.rounded-2xl') as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  form.fetchOnboardingReviewSlots.mockResolvedValue({
    programme: '',
    allIssued: true,
    allCompleted: false,
    reviews: [
      review('eligibility-review', 'Eligibility Review & FS Discussion', { started: true, completed: true, issuedAt: '2026-10-15T12:52:00Z', completedAt: '2026-10-15T13:05:00Z' }),
      review('workspace', 'RPL And Experience', { started: true, completed: false, issuedAt: '2026-10-15T13:32:00Z' }),
      review('training-plan', 'Workplace Health & Safety Declaration', { started: false }),
    ],
  });
});

function renderPage() {
  render(<MemoryRouter><OnboardingReviewsPage /></MemoryRouter>);
}

describe('issued reviews', () => {
  it('show when they were issued, in UK time', async () => {
    renderPage();
    await screen.findByText('RPL And Experience');
    expect(within(card('RPL And Experience')).getByText('Issued 15 Oct 2026, 14:32')).toBeInTheDocument();
    // Not finished, so no completion date.
    expect(within(card('RPL And Experience')).queryByText(/^Completed /)).not.toBeInTheDocument();
  });

  it('show when a finished review was completed', async () => {
    renderPage();
    await screen.findByText('Eligibility Review & FS Discussion');
    const done = card('Eligibility Review & FS Discussion');
    expect(within(done).getByText('Issued 15 Oct 2026, 13:52')).toBeInTheDocument();
    expect(within(done).getByText('Completed 15 Oct 2026, 14:05')).toBeInTheDocument();
  });
});

describe('enrolment review download', () => {
  it('is offered only on a completed review', async () => {
    renderPage();
    await screen.findByText('Eligibility Review & FS Discussion');

    expect(within(card('Eligibility Review & FS Discussion')).getByRole('button', { name: /Download review/ })).toBeInTheDocument();
    expect(within(card('RPL And Experience')).queryByRole('button', { name: /Download review/ })).not.toBeInTheDocument();
    expect(within(card('Workplace Health & Safety Declaration')).queryByRole('button', { name: /Download review/ })).not.toBeInTheDocument();
  });

  it('downloads that review’s document from the saved answers', async () => {
    const saved = { completed: true, answers: {} };
    form.fetchReviewForm.mockResolvedValue(saved);
    renderPage();
    await screen.findByText('Eligibility Review & FS Discussion');

    await userEvent.click(within(card('Eligibility Review & FS Discussion')).getByRole('button', { name: /Download review/ }));

    await waitFor(() => expect(doc.downloadReviewPdf).toHaveBeenCalledWith(saved, REVIEW_QUESTION_LABELS));
    expect(form.fetchReviewForm).toHaveBeenCalledWith('apprenticeship', '670', 'ev-eligibility-review');
  });

  it('says so when the download fails', async () => {
    form.fetchReviewForm.mockRejectedValue(new Error('Review not found.'));
    renderPage();
    await screen.findByText('Eligibility Review & FS Discussion');

    await userEvent.click(within(card('Eligibility Review & FS Discussion')).getByRole('button', { name: /Download review/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Review not found.');
    expect(doc.downloadReviewPdf).not.toHaveBeenCalled();
  });
});
