import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { mayAccessRoute } from '@/lib/routeAccess';
import { roleNavMap } from '@/mocks/navigation';
import {
  caseOwnerOptions,
  filterFirstSessionBookings,
  programmeOptions,
  type FirstSessionBooking,
  type FirstSessionBookingFilters,
} from '@/api/firstSessionBookings';

vi.mock('@/pages/admin/_shared/AdminPage', () => ({
  AdminPage: ({ children }: { children: ReactNode }) => <main>{children}</main>,
  DataPanel: ({ loading, error, empty, emptyMessage, onRetry, children }: { loading: boolean; error: string | null; empty?: boolean; emptyMessage?: string; onRetry?: () => void; children: ReactNode }) =>
    loading ? <p>Loading bookings</p> : error ? <div><p>{error}</p><button onClick={onRetry}>Try again</button></div> : empty ? <p>{emptyMessage}</p> : <>{children}</>,
  StatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}));

const FirstSessionBookingsPage = (await import('./page')).default;

function booking(overrides: Partial<FirstSessionBooking> = {}): FirstSessionBooking {
  return {
    id: 1,
    learnerId: 10,
    source: 'apprenticeship',
    learnerName: 'Alex Example',
    learnerEmail: 'alex@example.org',
    programme: 'Data Technician',
    caseOwner: { name: 'Olivia Owner', email: 'olivia@example.org' },
    bookedAt: '2026-09-20T09:30:00+00:00',
    bookedDate: '2026-09-20',
    sessionDate: '2026-10-06',
    sessionTime: '10:00',
    durationMinutes: 60,
    meetingLink: 'https://teams.example.org/join/1',
    state: 'scheduled',
    status: 'scheduled',
    syncState: 'synced',
    ...overrides,
  };
}

const rows = [
  booking(),
  booking({
    id: 2, learnerId: 11, source: 'commercial', learnerName: 'Sam Sample', learnerEmail: 'sam@example.org',
    programme: 'Project Management', caseOwner: { name: 'Pat Person', email: 'pat@example.org' },
    bookedAt: '2026-08-01T12:00:00+00:00', bookedDate: '2026-08-01', sessionDate: '2026-08-10',
    meetingLink: '', state: 'sync-failed', syncState: 'failed',
  }),
  booking({
    id: 3, learnerId: null, source: null, learnerName: 'Casey Cancelled', learnerEmail: 'casey@example.org',
    programme: '', bookedDate: '2026-09-01', sessionDate: '2026-09-05', state: 'cancelled', status: 'cancelled',
  }),
];

const noFilters: FirstSessionBookingFilters = { caseOwner: '', programme: '', sessionFrom: '', sessionTo: '', bookedFrom: '', bookedTo: '' };

function respond(body: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: () => Promise.resolve(body) });
}

function renderPage(path = '/users/first-sessions') {
  return render(<MemoryRouter initialEntries={[path]}><FirstSessionBookingsPage /></MemoryRouter>);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('first-session booking filters', () => {
  it('filters by case owner, programme and both date ranges, inclusively', () => {
    expect(filterFirstSessionBookings(rows, noFilters)).toHaveLength(3);
    expect(filterFirstSessionBookings(rows, { ...noFilters, caseOwner: 'pat@example.org' }).map(r => r.id)).toEqual([2]);
    expect(filterFirstSessionBookings(rows, { ...noFilters, programme: 'Data Technician' }).map(r => r.id)).toEqual([1]);
    expect(filterFirstSessionBookings(rows, { ...noFilters, sessionFrom: '2026-09-05', sessionTo: '2026-10-06' }).map(r => r.id)).toEqual([1, 3]);
    expect(filterFirstSessionBookings(rows, { ...noFilters, bookedFrom: '2026-09-01', bookedTo: '2026-09-01' }).map(r => r.id)).toEqual([3]);
    expect(filterFirstSessionBookings(rows, { ...noFilters, bookedTo: '2026-08-31', programme: 'Data Technician' })).toEqual([]);
  });

  it('never matches a missing date against a set bound', () => {
    const undated = booking({ id: 9, sessionDate: null, bookedDate: null });
    expect(filterFirstSessionBookings([undated], noFilters)).toHaveLength(1);
    expect(filterFirstSessionBookings([undated], { ...noFilters, sessionFrom: '2000-01-01' })).toEqual([]);
    expect(filterFirstSessionBookings([undated], { ...noFilters, bookedTo: '2100-01-01' })).toEqual([]);
  });

  it('builds sorted, de-duplicated options', () => {
    expect(caseOwnerOptions([...rows, booking({ id: 4 })])).toEqual([
      { value: 'olivia@example.org', label: 'Olivia Owner' },
      { value: 'pat@example.org', label: 'Pat Person' },
    ]);
    expect(programmeOptions(rows)).toEqual(['Data Technician', 'Project Management']);
  });
});

describe('First sessions page', () => {
  it('lists every booking, including failed and cancelled ones, with links to the learner and meeting', async () => {
    const fetchMock = respond({ count: 3, results: rows });
    vi.stubGlobal('fetch', fetchMock);
    renderPage();

    const table = await screen.findByRole('table', { name: 'First-session bookings' });
    expect(fetchMock).toHaveBeenCalledWith('/learner_api/first-session-bookings/', expect.objectContaining({ credentials: 'include' }));
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(within(table).getByRole('link', { name: 'Alex Example' })).toHaveAttribute('href', '/users/10');
    expect(within(table).getByRole('link', { name: 'Sam Sample' })).toHaveAttribute('href', '/users/11?source=commercial');
    expect(within(table).queryByRole('link', { name: 'Casey Cancelled' })).not.toBeInTheDocument();

    const join = within(table).getByRole('link', { name: 'Open Teams meeting for Alex Example' });
    expect(join).toHaveAttribute('href', 'https://teams.example.org/join/1');
    expect(join).toHaveAttribute('target', '_blank');
    expect(join).toHaveAttribute('rel', 'noopener noreferrer');

    const failed = within(table).getByRole('link', { name: 'Sam Sample' }).closest('tr')!;
    expect(within(failed).getByText('Teams sync failed')).toBeInTheDocument();
    expect(within(failed).getByText('No link')).toBeInTheDocument();
    expect(within(table).getByText('Cancelled')).toBeInTheDocument();
    const scheduled = within(table).getByRole('link', { name: 'Alex Example' }).closest('tr')!;
    expect(within(scheduled).getByText('Tue, 6 Oct 2026')).toBeInTheDocument();
    expect(within(scheduled).getByText('10:00 UK time · 60 min')).toBeInTheDocument();
    expect(within(scheduled).getByText('Scheduled')).toBeInTheDocument();
  });

  it('applies filters from the controls and from the URL, and clears them', async () => {
    vi.stubGlobal('fetch', respond({ count: 3, results: rows }));
    renderPage('/users/first-sessions?programme=Project%20Management');

    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    expect(screen.getByLabelText('Programme')).toHaveValue('Project Management');

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(4);
    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Case owner'), { target: { value: 'olivia@example.org' } });
    fireEvent.change(screen.getByLabelText('Session from'), { target: { value: '2026-10-01' } });
    const filtered = within(screen.getByRole('table')).getAllByRole('row');
    expect(filtered).toHaveLength(2);
    expect(within(filtered[1]).getByText('Alex Example')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Booked to'), { target: { value: '2026-09-19' } });
    expect(screen.getByText('No first-session bookings match these filters.')).toBeInTheDocument();
  });

  it('shows the server error with a retry, rather than an empty list', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 502, json: () => Promise.resolve({ error: 'First-session bookings could not be loaded. Please try again.' }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve({ count: 1, results: [rows[0]] }) });
    vi.stubGlobal('fetch', fetchMock);
    renderPage();

    expect(await screen.findByText('First-session bookings could not be loaded. Please try again.')).toBeInTheDocument();
    expect(screen.queryByText('No first-session bookings yet.')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('link', { name: 'Alex Example' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('First sessions access and navigation', () => {
  it('is open to enrolment and super-admin grants only', () => {
    const path = '/users/first-sessions';
    for (const access of ['super-admin', 'enrolment']) {
      expect(mayAccessRoute(path, { role: 'staff', access })).toBe(true);
    }
    for (const access of ['', 'coach', 'curriculum', 'record-monitor']) {
      expect(mayAccessRoute(path, { role: 'staff', access })).toBe(false);
    }
    expect(mayAccessRoute(path, { role: 'learner', access: 'enrolment' })).toBe(false);
    expect(mayAccessRoute(path, { role: 'employer', access: 'enrolment' })).toBe(false);
    expect(mayAccessRoute(path, { role: 'staff', access: 'coach', accessWorkspaces: [{ access: 'enrolment', home: '/users' }] })).toBe(true);
    // The directory itself keeps its own rule.
    expect(mayAccessRoute('/users', { role: 'staff', access: 'coach' })).toBe(true);
  });

  it('appears in both enrolment sidebars', () => {
    for (const role of ['apprentice', 'compliance']) {
      expect(roleNavMap[role].items.map(item => item.href)).toContain('/users/first-sessions');
    }
  });
});
