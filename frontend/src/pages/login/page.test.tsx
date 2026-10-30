import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import LoginPage from './page';
import { AuthError, PasswordSetupRequired, apiAuthHealth, apiMicrosoftStart, type AuthUser } from '@/api/auth';

const { login, authState } = vi.hoisted(() => ({ login: vi.fn(), authState: {
  account: null as (Pick<AuthUser, 'role' | 'access' | 'accessHome' | 'subjectId' | 'hasLegacyRecord'>
    & Partial<Pick<AuthUser, 'subjectType'>>) | null,
} }));
vi.mock("@/hooks/useAuth.helpers", () => ({
  useAuth: () => ({ login, auth: authState, isInitialized: true }),
}));
vi.mock('@/api/auth', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/auth')>(),
  apiAuthHealth: vi.fn().mockResolvedValue({ microsoftSso: { configured: false } }),
  apiMicrosoftStart: vi.fn(),
}));

beforeEach(() => {
  authState.account = null;
  login.mockReset(); login.mockImplementation(() => new Promise(() => {}));
  vi.mocked(apiAuthHealth).mockResolvedValue({ microsoftSso: { configured: false } } as Awaited<ReturnType<typeof apiAuthHealth>>);
  vi.mocked(apiMicrosoftStart).mockReset();
});
afterEach(cleanup);

function openForm() {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  return {
    email: screen.getByLabelText('Email address', { exact: true }) as HTMLInputElement,
    password: screen.getByLabelText('Password', { exact: true }) as HTMLInputElement,
    submit: screen.getByRole('button', { name: 'Sign in to Workspace' }),
  };
}

// Password managers may replace DOM values without firing React's onChange.
function autofill(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
}

it('sends a learner using the first-sign-in password straight to set their own', async () => {
  login.mockRejectedValueOnce(new PasswordSetupRequired('/set-password?token=T'));
  function Destination() {
    const location = useLocation();
    return <output data-testid="destination">{location.pathname}{location.search}|{JSON.stringify(location.state)}</output>;
  }
  render(<MemoryRouter initialEntries={['/login']}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/set-password" element={<Destination />} />
  </Routes></MemoryRouter>);
  autofill(screen.getByLabelText('Email address', { exact: true }) as HTMLInputElement, 'learner@kbc.test');
  autofill(screen.getByLabelText('Password', { exact: true }) as HTMLInputElement, 'changeme_password');
  fireEvent.click(screen.getByRole('button', { name: 'Sign in to Workspace' }));
  expect(await screen.findByTestId('destination')).toHaveTextContent('/set-password?token=T|{"firstSignIn":true}');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('submits the visible autofilled values instead of stale React state', async () => {
  const { email, password, submit } = openForm();
  fireEvent.change(email, { target: { value: 'old@example.test' } });
  fireEvent.change(password, { target: { value: 'previous-value' } });
  autofill(email, 'current@example.test');
  autofill(password, 'Visible value @ 9');
  fireEvent.click(submit);
  await waitFor(() => expect(login).toHaveBeenCalledWith('current@example.test', 'Visible value @ 9', false));
});

it('opens the fixed lecture feedback page after learner sign-in from its QR', async () => {
  login.mockResolvedValue({ id: 61, role: 'learner', subjectType: 'learner', subjectId: 61, accessHome: '/learner/home' });
  render(<MemoryRouter initialEntries={['/login?lectureFeedback=1']}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/learner/feedback" element={<h1>Lecture feedback destination</h1>} />
  </Routes></MemoryRouter>);
  autofill(screen.getByLabelText('Email address', { exact: true }) as HTMLInputElement, 'learner@example.test');
  autofill(screen.getByLabelText('Password', { exact: true }) as HTMLInputElement, 'Example password 7!');
  fireEvent.click(screen.getByRole('button', { name: 'Sign in to Workspace' }));
  expect(await screen.findByRole('heading', { name: 'Lecture feedback destination' })).toBeInTheDocument();
});

it('uses the same fixed destination when an already signed-in learner scans the QR', async () => {
  authState.account = { role: 'learner', subjectType: 'learner', subjectId: 61, accessHome: '/learner/home' };
  render(<MemoryRouter initialEntries={['/login?lectureFeedback=1']}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/learner/feedback" element={<h1>Lecture feedback destination</h1>} />
  </Routes></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: 'Lecture feedback destination' })).toBeInTheDocument();
});

it('forwards only the fixed lecture feedback intent to Microsoft sign-in', async () => {
  vi.mocked(apiAuthHealth).mockResolvedValue({ microsoftSso: { configured: true } } as Awaited<ReturnType<typeof apiAuthHealth>>);
  vi.mocked(apiMicrosoftStart).mockRejectedValue(new AuthError('Provider unavailable.', 503));
  render(<MemoryRouter initialEntries={['/login?lectureFeedback=1']}><LoginPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: /Microsoft/i }));
  await screen.findByText('Provider unavailable.');
  expect(apiMicrosoftStart).toHaveBeenCalledExactlyOnceWith(undefined, 'lecture-feedback');
});

it('allows a first visit filled entirely by the password manager', async () => {
  const { email, password, submit } = openForm();
  autofill(email, 'current@example.test');
  autofill(password, 'Visible value @ 9');
  expect(submit).toBeEnabled();
  fireEvent.click(submit);
  await waitFor(() => expect(login).toHaveBeenCalledWith('current@example.test', 'Visible value @ 9', false));
});

it('preserves typed password spaces and the remember choice', async () => {
  const { email, password, submit } = openForm();
  fireEvent.change(email, { target: { value: 'current@example.test' } });
  fireEvent.change(password, { target: { value: '  Typed value @ 9  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remember me' }));
  fireEvent.click(submit);
  await waitFor(() => expect(login).toHaveBeenCalledWith('current@example.test', '  Typed value @ 9  ', true));
});

it('keeps autofilled values when revealing the password and choosing remember me', async () => {
  const { email, password, submit } = openForm();
  autofill(email, 'current@example.test');
  autofill(password, 'Visible value @ 9');
  fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remember me' }));
  expect(password).toHaveAttribute('type', 'text');
  expect(password).toHaveValue('Visible value @ 9');
  expect(email).toHaveValue('current@example.test');
  fireEvent.click(submit);
  await waitFor(() => expect(login).toHaveBeenCalledWith('current@example.test', 'Visible value @ 9', true));
});

it('reads updated autofill on retry after a rejected sign-in', async () => {
  login.mockRejectedValueOnce(new AuthError('Incorrect email or password.', 401));
  const { email, password, submit } = openForm();
  autofill(email, 'current@example.test');
  autofill(password, 'previous-value');
  fireEvent.click(submit);
  await screen.findByRole('alert');
  expect(email).toHaveValue('current@example.test');
  autofill(password, 'Visible value @ 9');
  fireEvent.click(submit);
  await waitFor(() => expect(login).toHaveBeenLastCalledWith('current@example.test', 'Visible value @ 9', false));
  expect(login).toHaveBeenCalledTimes(2);
});

it.each(['/workspace/learner', '/coach/caseload'])('opens monitoring after email/password login despite return target %s', async from => {
  login.mockResolvedValue({ id: 700, role: 'staff', access: 'record-monitor', accessHome: '/old-otjh/monitor' });
  render(<MemoryRouter initialEntries={[{ pathname: '/login', state: { from } }]}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/old-otjh/monitor" element={<h1>Monitoring destination</h1>} />
    <Route path="/workspace/learner" element={<h1>Learner destination</h1>} />
  </Routes></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Email address', { exact: true }), { target: { value: 'monitor@example.org' } });
  fireEvent.change(screen.getByLabelText('Password', { exact: true }), { target: { value: 'Example temporary password 7!' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in to Workspace' }));
  expect(await screen.findByRole('heading', { name: 'Monitoring destination' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Learner destination' })).not.toBeInTheDocument();
});

it.each([
  [false, false], [false, true], [true, false], [true, true],
])('opens Student Home despite a remembered Dashboard (existing session: %s, imported: %s)', async (existingSession, hasLegacyRecord) => {
  const account = { role: 'learner' as const, subjectId: 42, hasLegacyRecord, accessHome: '/workspace/learner' };
  if (existingSession) authState.account = account;
  else login.mockResolvedValue(account);
  render(<MemoryRouter initialEntries={[{ pathname: '/login', state: { from: '/workspace/learner' } }]}><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/old-otjh" element={<h1>Transition portal</h1>} />
    <Route path="/learner/home" element={<h1>Student Home destination</h1>} />
    <Route path="/workspace/learner" element={<h1>LMS destination</h1>} />
  </Routes></MemoryRouter>);
  if (!existingSession) {
    autofill(screen.getByLabelText('Email address', { exact: true }) as HTMLInputElement, 'learner@example.test');
    autofill(screen.getByLabelText('Password', { exact: true }) as HTMLInputElement, ' Example password 7! ');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to Workspace' }));
  }
  expect(await screen.findByRole('heading', { name: 'Student Home destination' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'LMS destination' })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Transition portal' })).not.toBeInTheDocument();
  if (existingSession) expect(login).not.toHaveBeenCalled();
  else expect(login).toHaveBeenCalledWith('learner@example.test', ' Example password 7! ', false);
});

const homeAccounts = [
  { role: 'admin' as const, subjectId: 1, accessHome: '/workspace/admin' },
  { role: 'staff' as const, subjectId: 1, accessHome: '/workspace/coach' },
  { role: 'staff' as const, subjectId: 1, accessHome: '/workspace/tutor' },
  { role: 'staff' as const, subjectId: 1, accessHome: '/workspace/curriculum' },
  { role: 'staff' as const, subjectId: 1, accessHome: '/access-required' },
  { role: 'employer' as const, subjectId: 42, accessHome: '/employers/42' },
];
it.each(homeAccounts.flatMap(account => [false, true].map(existingSession => ({ account, existingSession }))))(
  'opens $account.accessHome with existing session=$existingSession despite the last open page', async ({ account, existingSession }) => {
    if (existingSession) authState.account = account;
    else login.mockResolvedValue(account);
    render(<MemoryRouter initialEntries={[{ pathname: '/login', state: { from: '/notifications?filter=unread' } }]}><Routes>
      <Route path="/login" element={<LoginPage/>}/>
      <Route path={account.accessHome} element={<h1>Account home</h1>}/>
      <Route path="/notifications" element={<h1>Old page</h1>}/>
    </Routes></MemoryRouter>);
    if (!existingSession) {
      autofill(screen.getByLabelText('Email address', { exact: true }) as HTMLInputElement, 'user@example.test');
      autofill(screen.getByLabelText('Password', { exact: true }) as HTMLInputElement, 'Test password 7!');
      fireEvent.click(screen.getByRole('button', { name: 'Sign in to Workspace' }));
    }
    expect(await screen.findByRole('heading', { name: 'Account home' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Old page' })).not.toBeInTheDocument();
  },
);

it('always offers Microsoft sign-in, and reports an unconfigured deployment on click', async () => {
  vi.mocked(apiMicrosoftStart).mockRejectedValue(new AuthError('Microsoft sign-in is not configured.', 503));
  render(<MemoryRouter initialEntries={['/login']}><LoginPage/></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: /Sign in with Microsoft/i }));
  expect(await screen.findByText('Microsoft sign-in is not configured.')).toBeInTheDocument();
});

it('starts Microsoft sign-in without forwarding a remembered page', async () => {
  vi.mocked(apiAuthHealth).mockResolvedValue({ microsoftSso: { configured: true } } as Awaited<ReturnType<typeof apiAuthHealth>>);
  // Stop before the external handoff; verify the destination argument and error recovery.
  vi.mocked(apiMicrosoftStart).mockRejectedValue(new AuthError('Provider temporarily unavailable.', 503));
  render(<MemoryRouter initialEntries={[{ pathname: '/login', state: { from: '/curriculum/programmes?tab=published' } }]}><LoginPage/></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: /Microsoft/i }));
  await screen.findByText('Provider temporarily unavailable.');
  expect(apiMicrosoftStart).toHaveBeenCalledExactlyOnceWith();
});
