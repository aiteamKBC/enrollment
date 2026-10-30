/**
 * Bulk attendance mode: filters narrow the learner list, only selected learners
 * are sent, Live Sessions is the default, and each learner's result is read back
 * from the server rather than assumed. Admin changes skip manager approval.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AttendanceMode } from '@/api/attendanceLectures';
import type { UserListRow } from '../../types';

const updateAttendanceMode = vi.fn();
const fetchAttendanceModes = vi.fn();

vi.mock('@/api/attendanceLectures', async () => {
  const actual = await vi.importActual<typeof import('@/api/attendanceLectures')>('@/api/attendanceLectures');
  return {
    ...actual,
    updateAttendanceMode: (...args: unknown[]) => updateAttendanceMode(...args),
    fetchAttendanceModes: (...args: unknown[]) => fetchAttendanceModes(...args),
  };
});

const { BulkAttendanceModeModal } = await import('../BulkAttendanceModeModal');

const base = { uuid: null, type: 'User', email: '', subscriptionStatus: '', subscriptionVerified: false, learningPlan: false };
const ROWS: UserListRow[] = [
  { ...base, id: '1', name: 'Alex Able', source: 'apprenticeship', caseOwner: 'Owner A', programme: 'Prog 1', cohort: 'C1', group: 'G1' },
  { ...base, id: '2', name: 'Blake Bond', source: 'commercial', caseOwner: 'Owner B', programme: 'Prog 2', cohort: 'C2', group: 'G2' },
  { ...base, id: '3', name: 'Casey Cole', source: 'apprenticeship', caseOwner: 'Owner A', programme: 'Prog 1', cohort: 'C1', group: 'G3' },
  { ...base, id: '9', name: 'Staff Person', type: 'Admin', group: '', source: 'staff' },
];

const mode = (patch: Partial<AttendanceMode>): AttendanceMode => ({
  available: true, mode: 'live', requestedMode: null, status: 'active', emailSent: false,
  managerAvailable: true, remindersEnabled: true, updatedAt: null, ...patch,
});

const rowNames = () => screen.getAllByRole('checkbox', { name: /^Select (?!all)/ }).map((c) => c.getAttribute('aria-label'));

beforeEach(() => {
  updateAttendanceMode.mockReset();
  fetchAttendanceModes.mockReset();
  fetchAttendanceModes.mockResolvedValue({ available: true, modes: {} });
});

describe('BulkAttendanceModeModal', () => {
  it('lists learners only and filters by case owner, group and name', async () => {
    const user = userEvent.setup();
    render(<BulkAttendanceModeModal rows={ROWS} onClose={() => undefined} />);
    expect(rowNames()).toEqual(['Select Alex Able', 'Select Blake Bond', 'Select Casey Cole']);

    await user.selectOptions(screen.getByLabelText('Case owner'), 'Owner A');
    expect(rowNames()).toEqual(['Select Alex Able', 'Select Casey Cole']);

    await user.selectOptions(screen.getByLabelText('Programme'), 'Prog 1');
    await user.selectOptions(screen.getByLabelText('Group'), 'G3');
    expect(rowNames()).toEqual(['Select Casey Cole']);

    await user.selectOptions(screen.getByLabelText('Group'), '');
    await user.type(screen.getByLabelText('Learner name'), 'alex');
    expect(rowNames()).toEqual(['Select Alex Able']);
  });

  it('filters by current attendance mode once the saved modes load', async () => {
    const user = userEvent.setup();
    fetchAttendanceModes.mockResolvedValue({ available: true, modes: {
      '2': { mode: 'lazy', requestedMode: null },
      '3': { mode: 'live', requestedMode: 'lazy' },
    } });
    render(<BulkAttendanceModeModal rows={ROWS} onClose={() => undefined} />);

    const filter = screen.getByLabelText('Attendance mode');
    await waitFor(() => expect(filter).toBeEnabled());

    await user.selectOptions(filter, 'Recorded');
    expect(rowNames()).toEqual(['Select Blake Bond']);

    // No saved entry and a pending Recorded request both count as Live Sessions.
    await user.selectOptions(filter, 'Live Sessions');
    expect(rowNames()).toEqual(['Select Alex Able', 'Select Casey Cole']);

    await user.selectOptions(filter, '');
    expect(rowNames()).toHaveLength(3);
  });

  it('keeps the attendance mode filter disabled when modes cannot load', async () => {
    fetchAttendanceModes.mockResolvedValue({ available: false, modes: {} });
    render(<BulkAttendanceModeModal rows={ROWS} onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText('Attendance mode is not available yet.')).toBeInTheDocument());
    expect(screen.getByLabelText('Attendance mode')).toBeDisabled();
  });

  it('shows each learner’s current attendance mode and updates it after applying', async () => {
    const user = userEvent.setup();
    fetchAttendanceModes.mockResolvedValue({ available: true, modes: {
      '2': { mode: 'lazy', requestedMode: null },
      '3': { mode: 'live', requestedMode: 'lazy' },
    } });
    updateAttendanceMode.mockResolvedValue(mode({ mode: 'lazy', status: 'active' }));
    render(<BulkAttendanceModeModal rows={ROWS} onClose={() => undefined} />);

    const rowOf = (name: string) => screen.getByLabelText(`Select ${name}`).closest('tr')!;
    await waitFor(() => expect(within(rowOf('Blake Bond')).getByText('Recorded')).toBeInTheDocument());
    expect(within(rowOf('Alex Able')).getByText('Live Sessions')).toBeInTheDocument();
    expect(within(rowOf('Casey Cole')).getByText('Recorded requested')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Recorded' }));
    await user.click(screen.getByLabelText('Select Alex Able'));
    await user.click(screen.getByRole('button', { name: 'Apply to 1 selected' }));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(within(rowOf('Alex Able')).queryByText('Live Sessions')).not.toBeInTheDocument());
    expect(within(rowOf('Alex Able')).getAllByText('Recorded')).toHaveLength(2);
  });

  it('defaults to Live Sessions and sends only the selected learners', async () => {
    const user = userEvent.setup();
    updateAttendanceMode.mockResolvedValue(mode({ mode: 'live' }));
    render(<BulkAttendanceModeModal rows={ROWS} onClose={() => undefined} />);

    expect(screen.getByRole('radio', { name: 'Live Sessions' })).toBeChecked();
    await user.click(screen.getByLabelText('Select Alex Able'));
    await user.click(screen.getByLabelText('Select Blake Bond'));
    await user.click(screen.getByRole('button', { name: 'Apply to 2 selected' }));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(screen.getAllByText('Live Sessions', { selector: 'td' })).toHaveLength(2));
    expect(updateAttendanceMode).toHaveBeenCalledTimes(2);
    expect(updateAttendanceMode).toHaveBeenCalledWith('apprenticeship', '1', 'live', { direct: true });
    expect(updateAttendanceMode).toHaveBeenCalledWith('commercial', '2', 'live', { direct: true });
  });

  it('sets Recorded directly, without manager approval, and keeps per-learner failures visible', async () => {
    const user = userEvent.setup();
    updateAttendanceMode.mockImplementation(async (_kind: string, id: string) => {
      if (id === '3') throw new Error('Could not save attendance mode. Please retry.');
      return mode({ mode: 'lazy', status: 'active' });
    });
    render(<BulkAttendanceModeModal rows={ROWS} onClose={() => undefined} />);

    await user.click(screen.getByRole('radio', { name: 'Recorded' }));
    await user.click(screen.getByLabelText('Select all shown learners'));
    await user.click(screen.getByRole('button', { name: 'Apply to 3 selected' }));
    expect(screen.queryByText(/manager|approv/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(screen.getByText('2 updated, 1 need attention')).toBeInTheDocument());
    const casey = screen.getByLabelText('Select Casey Cole').closest('tr')!;
    expect(within(casey).getByText('Could not save attendance mode. Please retry.')).toBeInTheDocument();
    expect(screen.getAllByText('Recorded', { selector: 'td' })).toHaveLength(2);
    expect(updateAttendanceMode).toHaveBeenCalledWith('apprenticeship', '1', 'lazy', { direct: true });
    expect(updateAttendanceMode).not.toHaveBeenCalledWith(expect.anything(), '9', expect.anything(), expect.anything());
  });
});
