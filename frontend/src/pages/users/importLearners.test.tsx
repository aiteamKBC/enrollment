import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppIcon } from '@/components/feature/AppIcon';
import { fetchEnrolmentUsers } from '@/api/enrolmentUsers';
import { importEnrolmentUsers, type LearnerImportResult } from '@/api/enrolmentUserImport';
import type { UserListRow } from './types';
import UsersListPage from './page';

vi.mock("@/hooks/useAuth.helpers", () => ({ useAuth: () => ({ isAdmin: true }) }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/api/enrolmentUsers', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/enrolmentUsers')>(), fetchEnrolmentUsers: vi.fn(),
}));
vi.mock('@/api/enrolmentUserImport', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/enrolmentUserImport')>(), importEnrolmentUsers: vi.fn(),
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
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('Unexpected request'); }));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it('refreshes the directory after importing the validated workbook', async () => {
  const learner: UserListRow = {
    id: '500', uuid: null, name: 'Imported Learner', type: 'User', source: 'commercial',
    email: 'imported@example.test', group: '', subscriptionStatus: 'FullUser',
    subscriptionVerified: true, learningPlan: false, hasAccount: true,
  };
  const result: LearnerImportResult = {
    count: 1, imported: 0, results: [], errors: [],
    preview: [{ row: 2, name: learner.name, email: learner.email, programme: '', cohort: '', group: '' }],
  };
  vi.mocked(fetchEnrolmentUsers).mockResolvedValueOnce([]).mockResolvedValue([learner]);
  vi.mocked(importEnrolmentUsers).mockResolvedValueOnce(result).mockResolvedValueOnce({ ...result, imported: 1, results: [learner] });
  render(<MemoryRouter><UsersListPage /></MemoryRouter>);
  await screen.findByText(/No users yet/);
  expect(screen.getByRole('button', { name: 'Download template' })).toBeInTheDocument();
  // The wizard and eligibility form builders are opened from the same header.
  expect(screen.getByRole('button', { name: 'Edit apprenticeship wizard' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit eligibility form' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Upload learners' }));
  const workbook = new File(['workbook'], 'learners.xlsx');
  fireEvent.change(screen.getByLabelText('Completed template'), { target: { files: [workbook] } });
  fireEvent.click(screen.getByRole('button', { name: 'Validate file' }));
  const save = await screen.findByRole('button', { name: 'Import 1 learners' });
  expect(fetchEnrolmentUsers).toHaveBeenCalledTimes(1);
  fireEvent.click(save);
  await screen.findByText('1 learner imported successfully.');
  fireEvent.click(screen.getByRole('button', { name: 'Done' }));
  await waitFor(() => expect(fetchEnrolmentUsers).toHaveBeenCalledTimes(2));
  expect(await screen.findByText('Imported Learner')).toBeInTheDocument();
  expect(screen.getByText('Showing 1 to 1 of 1 users')).toBeInTheDocument();
});
