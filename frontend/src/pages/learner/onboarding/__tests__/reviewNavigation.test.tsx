import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { enrolmentReviewPath, reviewReturnPath } from '../reviews/navigation';

const api = vi.hoisted(() => ({ fetchReviewForm: vi.fn() }));
vi.mock('@/api/reviewForm', async original => ({ ...(await original<typeof import('@/api/reviewForm')>()), ...api }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: 'commercial', id: '999' }) }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
import ReviewForm from '../reviews/form';

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchReviewForm.mockResolvedValue({ reviewLabel: 'Selected review', answers: {}, sections: [], sectionStatus: {}, completed: false });
});

function Destination() {
  const location = useLocation();
  return <p data-testid="destination">{location.pathname}{location.search}</p>;
}
function open(path: string) {
  // A single initial entry proves Back does not depend on browser history.
  render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/users/:userId/reviews/:eventKey" element={<ReviewForm />} />
    <Route path="/learner/onboarding/reviews/:eventKey" element={<ReviewForm />} />
    <Route path="/users/:userId" element={<Destination />} />
    <Route path="/learner/onboarding/reviews" element={<Destination />} />
  </Routes></MemoryRouter>);
}

it.each(['eligibility-review', 'workspace', 'training-plan', 'uln-privacy', 'future-review'])(
  'returns %s directly to the originating learner Documents tab after refresh/direct entry', async type => {
    open(enrolmentReviewPath('670', `review:apprenticeship:670:${type}`));
    await screen.findByRole('heading', { name: 'Selected review' });
    expect(api.fetchReviewForm).toHaveBeenCalledWith('apprenticeship', '670', `review:apprenticeship:670:${type}`);
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByTestId('destination')).toHaveTextContent('/users/670?tab=documents');
  },
);

it('uses the route learner for Back even without the admin query flag', async () => {
  open('/users/42/reviews/existing-review');
  await userEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(screen.getByTestId('destination')).toHaveTextContent('/users/42?tab=documents');
});

it('keeps the learner-origin Reviews return destination', async () => {
  open('/learner/onboarding/reviews/own-review');
  await userEvent.click(screen.getByRole('button', { name: 'Back' }));
  expect(screen.getByTestId('destination')).toHaveTextContent('/learner/onboarding/reviews');
});

it('uses the same return destination for Save and Close', async () => {
  open(enrolmentReviewPath('42', 'draft-review'));
  await userEvent.click(await screen.findByRole('button', { name: 'Save and Close' }));
  expect(screen.getByTestId('destination')).toHaveTextContent('/users/42?tab=documents');
});

it('encodes event keys and does not mix learner identities', () => {
  expect(enrolmentReviewPath('42', 'review:42/1')).toBe('/users/42/reviews/review%3A42%2F1?admin=1');
  expect(reviewReturnPath('42')).not.toBe(reviewReturnPath('670'));
  expect(reviewReturnPath()).toBe('/learner/onboarding/reviews');
});
