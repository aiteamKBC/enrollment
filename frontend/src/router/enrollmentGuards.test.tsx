import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LearnerOnboardingGate, LearnerStart, WelcomeEntry } from './enrollmentGuards';

const state = vi.hoisted(() => ({ kind: 'apprenticeship', board: vi.fn(), session: vi.fn(), welcome: vi.fn() }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: state.kind, id: '41' }) }));
vi.mock('@/api/enrolmentUsers', () => ({ fetchEnrolmentBoard: state.board }));
vi.mock('@/api/learnerCalendar', () => ({ fetchLearnerFirstSession: state.session }));
vi.mock('@/api/firstLoginDetails', () => ({ fetchFirstLoginDetails: state.welcome }));
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { role: 'learner' } } }) }));

beforeEach(() => {
  vi.clearAllMocks(); state.kind = 'apprenticeship';
  state.board.mockResolvedValue({ programme: { status: 'Fresh user' } });
  state.session.mockResolvedValue({ access: 'open' });
});

function open(welcome = false) {
  render(<MemoryRouter><Routes>
    <Route path="/" element={welcome ? <WelcomeEntry><h1>Existing commercial entry</h1></WelcomeEntry> : <LearnerStart />} />
    <Route path="/learner/onboarding" element={<h1>Wizard entry</h1>} />
    <Route path="/learner/pending" element={<h1>Waiting for onboarding</h1>} />
    <Route path="/lms" element={<h1>Learning</h1>} />
  </Routes></MemoryRouter>);
}

it('sends Fresh apprentices to the waiting page without starting the wizard', async () => {
  state.board.mockResolvedValue({ programme: { status: 'Fresh user' } }); open();
  expect(await screen.findByRole('heading', { name: 'Waiting for onboarding' })).toBeInTheDocument();
  expect(state.welcome).not.toHaveBeenCalled(); expect(state.session).not.toHaveBeenCalled();
});
it('sends Onboarding apprentices to the wizard without a Welcome API gate', async () => {
  state.board.mockResolvedValue({ programme: { status: 'Onboarding' } }); open();
  expect(await screen.findByRole('heading', { name: 'Wizard entry' })).toBeInTheDocument();
  expect(state.welcome).not.toHaveBeenCalled(); expect(state.session).not.toHaveBeenCalled();
});
it('blocks a direct wizard URL while the apprentice is Fresh', async () => {
  render(<MemoryRouter initialEntries={['/learner/onboarding']}><Routes>
    <Route path="/learner/onboarding" element={<LearnerOnboardingGate><h1>Wizard entry</h1></LearnerOnboardingGate>} />
    <Route path="/learner/pending" element={<h1>Waiting for onboarding</h1>} />
  </Routes></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: 'Waiting for onboarding' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Wizard entry' })).not.toBeInTheDocument();
});
it('redirects an old apprenticeship Welcome bookmark without rendering either step', async () => {
  state.board.mockResolvedValue({ programme: { status: 'Onboarding' } });
  open(true);
  expect(await screen.findByRole('heading', { name: 'Wizard entry' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Existing commercial entry' })).not.toBeInTheDocument();
  expect(state.welcome).not.toHaveBeenCalled();
});
it('preserves later-stage learner routing', async () => {
  state.board.mockResolvedValue({ programme: { status: 'Active' } }); open();
  expect(await screen.findByRole('heading', { name: 'Learning' })).toBeInTheDocument();
});
it('preserves commercial routing and its existing entry component', async () => {
  state.kind = 'commercial'; open(true);
  expect(screen.getByRole('heading', { name: 'Existing commercial entry' })).toBeInTheDocument();
  expect(state.board).not.toHaveBeenCalled();
});
it('does not fetch apprenticeship gates for commercial first login', async () => {
  state.kind = 'commercial'; open();
  expect(await screen.findByRole('heading', { name: 'Learning' })).toBeInTheDocument();
  expect(state.board).not.toHaveBeenCalled(); expect(state.welcome).not.toHaveBeenCalled();
});
