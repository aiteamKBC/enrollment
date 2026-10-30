import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import LoginPage from './page';
import { apiMicrosoftStart, PasswordSetupRequired, type AuthUser } from '@/api/auth';

const session = vi.hoisted(() => ({
  auth: { account: null as AuthUser | null }, login: vi.fn(), isInitialized: true,
}));
vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => session }));
vi.mock('@/api/auth', async original => ({
  ...await original<typeof import('@/api/auth')>(), apiMicrosoftStart: vi.fn(),
}));
beforeEach(() => { session.auth.account = null; vi.resetAllMocks(); });
afterEach(cleanup);

function Destination() {
  const location = useLocation();
  return <output>{location.pathname}{location.search}</output>;
}
function open(query = 'download=android') {
  return render(<MemoryRouter initialEntries={['/login?' + query]}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="*" element={<Destination />} />
  </Routes></MemoryRouter>);
}
function signIn() {
  fireEvent.change(screen.getByLabelText('Email address', { exact: true }), { target: { value: 'learner@example.test' } });
  fireEvent.change(screen.getByLabelText('Password', { exact: true }), { target: { value: 'Example password 1!' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in to Workspace' }));
}

it('explains sign-in and then offers a real browser download after password login', async () => {
  session.login.mockImplementation(async () => {
    session.auth.account = { role: 'learner' } as AuthUser;
    return session.auth.account;
  });
  open();
  expect(screen.getByText('Sign in to download KBC Learn for Android')).toBeInTheDocument();
  signIn();
  expect(await screen.findByRole('link', { name: 'Download Android app' }))
    .toHaveAttribute('href', '/learner_api/mobile/download/android/');
  expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
});
it('offers the download immediately for an existing learner session', () => {
  session.auth.account = { role: 'learner' } as AuthUser;
  open('download=android&next=https://evil.test');
  expect(screen.getByRole('link', { name: 'Download Android app' }))
    .toHaveAttribute('href', '/learner_api/mobile/download/android/');
});
it('retains normal routing for unknown download targets', async () => {
  session.auth.account = { role: 'learner' } as AuthUser;
  open('download=https://evil.test');
  expect(await screen.findByText('/learner/home')).toBeInTheDocument();
});
it('does not offer a learner download to employer accounts', async () => {
  session.auth.account = { role: 'employer' } as AuthUser;
  open();
  expect(screen.queryByRole('link', { name: 'Download Android app' })).not.toBeInTheDocument();
});
it('carries only the fixed download intent into Microsoft sign-in', async () => {
  vi.mocked(apiMicrosoftStart).mockRejectedValue(new Error('Synthetic unavailable provider'));
  open('download=android&next=https://evil.test');
  fireEvent.click(screen.getByRole('button', { name: 'Sign in with Microsoft' }));
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  expect(apiMicrosoftStart).toHaveBeenCalledWith('android');
});
it('keeps the Android journey when a first sign-in requires password setup', async () => {
  session.login.mockRejectedValue(new PasswordSetupRequired('/set-password?token=synthetic'));
  open(); signIn();
  expect(await screen.findByText('/set-password?token=synthetic&download=android')).toBeInTheDocument();
});

it.each([
  { role: 'admin', subjectType: 'staff' },
  { role: 'staff', subjectType: 'staff', access: 'coach' },
  { role: 'staff', subjectType: 'staff', access: 'tutor', accesses: ['tutor', 'coach'] },
])('offers the installer to an existing authorised staff session: %j', account => {
  session.auth.account = account as AuthUser;
  open();
  expect(screen.getByRole('link', { name: 'Download Android app' }))
    .toHaveAttribute('href', '/learner_api/mobile/download/android/');
  expect(screen.getByRole('button', { name: 'Go to my workspace' })).toBeInTheDocument();
});

it.each([
  { role: 'admin', subjectType: 'staff' },
  { role: 'staff', subjectType: 'staff', accesses: ['coach'] },
])('keeps a newly signed-in authorised staff account on the download page: %j', async account => {
  session.login.mockImplementation(async () => {
    session.auth.account = account as AuthUser;
    return session.auth.account;
  });
  open(); signIn();
  expect(await screen.findByRole('link', { name: 'Download Android app' })).toBeInTheDocument();
});

it.each([
  { role: 'staff', subjectType: 'staff', access: 'tutor' },
  { role: 'staff', subjectType: 'learner', access: 'coach' },
  { role: 'employer', subjectType: 'employer', accesses: ['coach'] },
])('does not offer installer access based on an unrelated or mismatched grant: %j', account => {
  session.auth.account = account as AuthUser;
  open();
  expect(screen.queryByRole('link', { name: 'Download Android app' })).not.toBeInTheDocument();
});
