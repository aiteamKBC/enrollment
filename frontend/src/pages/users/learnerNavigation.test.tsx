import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppIcon } from '@/components/feature/AppIcon';
import { fetchEnrolmentUsers } from '@/api/enrolmentUsers';
import type { UserListRow } from './types';
import UsersListPage from './page';

vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ isAdmin: true }) }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/api/enrolmentUsers', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/enrolmentUsers')>(), fetchEnrolmentUsers: vi.fn(),
}));
vi.mock('@/api/staffUsers', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/staffUsers')>(), fetchStaffUsers: vi.fn(async () => []),
}));
vi.mock('@/api/employers', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/employers')>(), listEmployers: vi.fn(async () => ({ results: [] })),
}));
vi.mock('@/api/curriculum', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/curriculum')>(),
  fetchProgrammes: vi.fn(async () => []), fetchCohorts: vi.fn(async () => []), fetchGroups: vi.fn(async () => []),
}));

beforeEach(() => {
  vi.stubGlobal('AppIcon', AppIcon);
  // Any unexpected request stays offline, including invitation actions.
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('Unexpected request'); }));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

function Destination() {
  return <p data-testid="destination">{useLocation().pathname}</p>;
}

it.each(['commercial', 'apprenticeship'] as const)('opens the %s learner Dashboard from the admin directory', async source => {
  const learner: UserListRow = {
    id: '132', uuid: null, name: 'Test learner', type: 'User', source,
    email: 'learner@example.test', group: '', subscriptionStatus: 'FullUser',
    subscriptionVerified: true, learningPlan: false, hasSignedIn: true, programmeStatus: 'Delivery',
  };
  vi.mocked(fetchEnrolmentUsers).mockResolvedValue([learner]);
  render(<MemoryRouter initialEntries={['/users']}><Routes>
    <Route path="/users" element={<UsersListPage />} />
    <Route path="*" element={<Destination />} />
  </Routes></MemoryRouter>);
  fireEvent.click(await screen.findByTitle("Open Test learner's learner page"));
  expect(await screen.findByTestId('destination')).toHaveTextContent(`/workspace/learner/${source}/132`);
});
