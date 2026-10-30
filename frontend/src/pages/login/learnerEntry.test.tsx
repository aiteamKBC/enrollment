import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Link, MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import type { AuthUser } from '@/api/auth';
import { OldOtjhGate, OldOtjhProvider } from '@/features/old-otjh/hooks';
import { fetchLearnerEntry } from '@/features/old-otjh/entry';
import LoginPage from './page';

const session = vi.hoisted(() => ({
  auth: { account: null as AuthUser | null },
  login: vi.fn(), logout: vi.fn(), isInitialized: true,
}));
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => session }));
vi.mock('@/api/auth', async original => ({
  ...await original<typeof import('@/api/auth')>(),
  apiAuthHealth: vi.fn().mockResolvedValue({ microsoftSso: { configured: false } }),
}));
vi.mock('@/features/old-otjh/entry', () => ({ fetchLearnerEntry: vi.fn() }));

const pending = { classification: 'existing' as const, enabled: true, required: true,
  canAccess: false, totalMonths: 2, completedMonths: 0 };
const allowed = { ...pending, required: false, canAccess: true, completedMonths: 2 };

beforeEach(() => {
  session.auth.account = null;
  session.login.mockImplementation(async () => {
    // A stale session hint must not decide whether a signature is required.
    session.auth.account = { id: 42, role: 'learner', subjectId: 42,
      hasLegacyRecord: false, accessHome: '/workspace/learner' } as AuthUser;
    return session.auth.account;
  });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function signInFromDashboard() {
  render(<OldOtjhProvider><MemoryRouter initialEntries={[
    { pathname: '/login', state: { from: '/workspace/learner?tab=attendance' } },
  ]}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<OldOtjhGate><Outlet /></OldOtjhGate>}>
      <Route path="/learner/home" element={<><h1>Student landing page</h1><Link to="/workspace/learner/dashboard">Dashboard</Link></>} />
      <Route path="/workspace/learner/dashboard" element={<h1>Learner dashboard</h1>} />
      <Route path="/old-otjh/months" element={<h1>Previous records to sign</h1>} />
    </Route>
  </Routes></MemoryRouter></OldOtjhProvider>);
  fireEvent.change(screen.getByLabelText('Email address', { exact: true }), { target: { value: 'learner@example.test' } });
  fireEvent.change(screen.getByLabelText('Password', { exact: true }), { target: { value: 'Test password 7!' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in to Workspace' }));
}

it.each(['new', 'existing'] as const)('opens the landing page first for an allowed %s learner and keeps Dashboard usable', async classification => {
  vi.mocked(fetchLearnerEntry).mockResolvedValue({ ...allowed, classification });
  signInFromDashboard();
  expect(await screen.findByRole('heading', { name: 'Student landing page' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Learner dashboard' })).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'Dashboard' }));
  expect(await screen.findByRole('heading', { name: 'Learner dashboard' })).toBeInTheDocument();
});

it('requires the old learner signature before showing the landing page after login', async () => {
  vi.mocked(fetchLearnerEntry).mockResolvedValue(pending);
  signInFromDashboard();
  expect(await screen.findByRole('dialog', { name: 'Review and sign your previous learning record' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Student landing page' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Learner dashboard' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'Review and sign' }));
  expect(await screen.findByRole('heading', { name: 'Previous records to sign' })).toBeInTheDocument();
  vi.mocked(fetchLearnerEntry).mockResolvedValue(allowed);
  fireEvent(window, new Event('previous-record-updated'));
  expect(await screen.findByRole('heading', { name: 'Student landing page' })).toBeInTheDocument();
});
