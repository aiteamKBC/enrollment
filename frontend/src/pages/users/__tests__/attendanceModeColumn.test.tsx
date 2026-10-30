import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppIcon } from '@/components/feature/AppIcon';
import { fetchEnrolmentUsers } from '@/api/enrolmentUsers';
import { fetchAttendanceModes } from '@/api/attendanceLectures';
import type { UserListRow } from '../types';
import UsersListPage from '../page';

vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ isAdmin: false }) }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/api/enrolmentUsers', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/enrolmentUsers')>(), fetchEnrolmentUsers: vi.fn(),
}));
vi.mock('@/api/attendanceLectures', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/attendanceLectures')>(), fetchAttendanceModes: vi.fn(),
}));
vi.mock('@/api/staffUsers', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/staffUsers')>(),
  fetchStaffUsers: vi.fn(async () => [{
    id: '7', uuid: null, name: 'Staff member', type: 'Tutor', email: 'staff@example.test', group: '',
    subscriptionStatus: 'FullUser', subscriptionVerified: true, learningPlan: false, hasSignedIn: true,
  }]),
}));
vi.mock('@/api/employers', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/employers')>(), listEmployers: vi.fn(async () => ({ results: [] })),
}));
vi.mock('@/api/curriculum', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/curriculum')>(),
  fetchProgrammes: vi.fn(async () => []), fetchCohorts: vi.fn(async () => []), fetchGroups: vi.fn(async () => []),
}));

const learner = (id: string, name: string): UserListRow => ({
  id, uuid: null, name, type: 'User', source: 'apprenticeship', email: `${id}@example.test`, group: '',
  subscriptionStatus: 'FullUser', subscriptionVerified: true, learningPlan: false, hasSignedIn: true,
  programmeStatus: 'Delivery',
});

beforeEach(() => {
  vi.stubGlobal('AppIcon', AppIcon);
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('Unexpected request'); }));
  vi.mocked(fetchEnrolmentUsers).mockResolvedValue([learner('11', 'Recorded learner'), learner('12', 'Live learner')]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

const view = () => render(<MemoryRouter initialEntries={['/users']}><UsersListPage /></MemoryRouter>);
const cellFor = async (name: string) => {
  const row = (await screen.findByText(name)).closest('tr')!;
  const column = screen.getAllByRole('columnheader').findIndex(h => h.textContent === 'Attendance mode');
  return within(row).getAllByRole('cell')[column];
};

it('shows each learner’s saved attendance mode, with no mode for staff', async () => {
  vi.mocked(fetchAttendanceModes).mockResolvedValue({ available: true, modes: { 11: { mode: 'lazy', requestedMode: null } } });
  view();
  expect(await within(await cellFor('Recorded learner')).findByText('Recorded')).toBeInTheDocument();
  expect(within(await cellFor('Live learner')).getByText('Live Sessions')).toBeInTheDocument();
  expect(await cellFor('Staff member')).toHaveTextContent('—');
});

const applyAttendanceFilter = (value: 'live' | 'lazy') => {
  const select = screen.getByText('Attendance mode', { selector: 'label' }).nextElementSibling as HTMLSelectElement;
  fireEvent.change(select, { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
};

it.each([
  ['lazy', 'Recorded learner', 'Live learner'],
  ['live', 'Live learner', 'Recorded learner'],
] as const)('filters the directory to %s learners only', async (mode, shown, hidden) => {
  vi.mocked(fetchAttendanceModes).mockResolvedValue({ available: true, modes: { 11: { mode: 'lazy', requestedMode: null } } });
  view();
  await within(await cellFor('Recorded learner')).findByText('Recorded');
  applyAttendanceFilter(mode);
  expect(await screen.findByText(shown)).toBeInTheDocument();
  expect(screen.queryByText(hidden)).not.toBeInTheDocument();
  expect(screen.queryByText('Staff member')).not.toBeInTheDocument();
});

it('says why nothing is listed when the filter is set but the modes cannot load', async () => {
  vi.mocked(fetchAttendanceModes).mockRejectedValue(new Error('Forbidden'));
  view();
  await screen.findByText('Live learner');
  applyAttendanceFilter('live');
  expect(await screen.findByText(/Attendance modes could not be loaded/)).toBeInTheDocument();
  expect(screen.queryByText('Live learner')).not.toBeInTheDocument();
});

it('shows a dash rather than guessing a mode when the modes cannot load', async () => {
  vi.mocked(fetchAttendanceModes).mockRejectedValue(new Error('Forbidden'));
  view();
  const cell = await cellFor('Live learner');
  await vi.waitFor(() => expect(cell).toHaveTextContent('—'));
  expect(within(cell).queryByText('Live Sessions')).not.toBeInTheDocument();
});
