import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { OnboardingReviewSlot } from '@/api/reviewForm';

const state = vi.hoisted(() => ({ load: vi.fn(), submitted: true, kind: 'apprenticeship' }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: state.kind, id: '670' }) }));
vi.mock('@/hooks/useLearnerNavGate', () => ({ useEnrolmentSubmitted: () => state.submitted }));
vi.mock('@/features/compliance-documents/LearnerDocumentRevisions', () => ({ LearnerDocumentRevisions: () => null }));
vi.mock('@/api/reviewForm', async original => ({ ...(await original<typeof import('@/api/reviewForm')>()), fetchOnboardingReviewSlots: state.load }));
import Page from '../reviews/page';

const reviews: OnboardingReviewSlot[] = [
  { type: 'eligibility-review', label: 'Eligibility Review & FS Discussion', issued: true, started: true, completed: true, eventKey: 'eligibility:670/1', issuedAt: '2026-10-15T12:00:00Z' },
  { type: 'workspace', label: 'RPL And Experience', issued: true, started: true, eventKey: 'workspace:670/1' },
  { type: 'training-plan', label: 'Workplace Health & Safety Declaration', issued: true, eventKey: 'training:670/1' },
  { type: 'uln-privacy', label: 'ULN Privacy Notice & Learner Acknowledgement', issued: false },
];
beforeEach(() => {
  vi.clearAllMocks(); state.submitted = true; state.kind = 'apprenticeship';
  state.load.mockResolvedValue({ reviews, allCompleted: false, allIssued: false, programme: '' });
});
function open() {
  render(<MemoryRouter initialEntries={['/learner/onboarding/reviews']}><Routes>
    <Route path="/learner/onboarding/reviews" element={<Page />} />
    <Route path="/learner/onboarding/reviews/:eventKey" element={<p>Existing review destination</p>} />
  </Routes></MemoryRouter>);
}
const card = (title: string) => screen.getByRole('heading', { name: title }).closest('div.rounded-2xl') as HTMLElement;

it('counts each real review once, using completion rather than issuance or signature assumptions', async () => {
  open();
  const summary = await screen.findByLabelText('Review status summary');
  for (const status of ['completed', 'progress', 'ready', 'unissued']) expect(summary.querySelector(`[data-status="${status}"] dd`)).toHaveTextContent('1');
  expect(screen.getByText('1 of 4 completed')).toBeInTheDocument();
  const progress = screen.getByRole('progressbar', { name: 'Enrolment reviews completed' });
  expect(progress).toHaveAttribute('aria-valuenow', '1');
  expect(progress.firstElementChild).toHaveStyle({ width: '25%' });
  expect(within(card(reviews[3].label)).getByText('Not issued yet')).toBeInTheDocument();
  expect(within(card(reviews[3].label)).queryByRole('button')).not.toBeInTheDocument();
  expect(within(card(reviews[3].label)).getByText('Your enrolment officer will issue this review to you. It will appear here, ready to start.')).toBeInTheDocument();
});

it('retains exact action labels, completed controls and real dates only', async () => {
  open(); await screen.findByText(reviews[0].label);
  expect(within(card(reviews[0].label)).getByText('Issued 15 Oct 2026, 13:00')).toBeInTheDocument();
  for (const action of ['View review', 'Sign review', 'Download review']) expect(within(card(reviews[0].label)).getByRole('button', { name: action })).toBeInTheDocument();
  expect(within(card(reviews[1].label)).getByRole('button', { name: 'Continue review' })).toBeInTheDocument();
  expect(within(card(reviews[2].label)).getByRole('button', { name: 'Start' })).toBeInTheDocument();
  expect(within(card(reviews[1].label)).queryByText(/^Issued /)).not.toBeInTheDocument();
  await userEvent.click(within(card(reviews[1].label)).getByRole('button', { name: 'Continue review' }));
  expect(await screen.findByText('Existing review destination')).toBeInTheDocument();
});

it('shows actual all-unissued counts without creating available actions', async () => {
  state.load.mockResolvedValue({ reviews: reviews.map(review => ({ ...review, issued: false, started: false, completed: false, eventKey: undefined, issuedAt: null })), allCompleted: false });
  open();
  const summary = await screen.findByLabelText('Review status summary');
  expect(summary.querySelector('[data-status="unissued"] dd')).toHaveTextContent('4');
  expect(summary.querySelector('[data-status="completed"] dd')).toHaveTextContent('0');
  expect(screen.getByText('0 of 4 completed')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Start|Continue review|Sign review/ })).not.toBeInTheDocument();
});

it('does not show fabricated summary counts while loading or after a failed read', async () => {
  state.load.mockRejectedValue(new Error('Reviews could not load'));
  open();
  expect(screen.queryByLabelText('Review status summary')).not.toBeInTheDocument();
  await screen.findByText('Reviews could not load');
  expect(screen.queryByLabelText('Review status summary')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
});

it.each(['unsubmitted', 'commercial'])('preserves the %s access boundary', mode => {
  if (mode === 'unsubmitted') state.submitted = false;
  else state.kind = 'commercial';
  open();
  expect(screen.queryByLabelText('Review status summary')).not.toBeInTheDocument();
  expect(state.load).not.toHaveBeenCalled();
  expect(screen.getByRole('heading', { name: mode === 'commercial' ? 'No onboarding reviews are required' : 'Reviews open once your enrolment is submitted' })).toBeInTheDocument();
});
