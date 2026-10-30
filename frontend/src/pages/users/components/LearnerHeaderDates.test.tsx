import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { updateEnrolmentUser } from '@/api/enrolmentUsers';
import type { EnrolmentBoard } from '../types';
import { LearnerHeaderDates } from './LearnerHeaderDates';

vi.mock('@/api/enrolmentUsers', () => ({ updateEnrolmentUser: vi.fn() }));

describe('LearnerHeaderDates', () => {
  beforeEach(() => vi.resetAllMocks());

  it('prefills dates, saves explicit edits, and supports clearing', async () => {
    vi.mocked(updateEnrolmentUser).mockResolvedValue({ programme: { learnerStartDate: '2028-02-29', learnerEndDate: '' } } as EnrolmentBoard);
    render(<LearnerHeaderDates learnerId="132" startDate="2028-01-01" endDate="2028-12-31" />);
    expect(screen.getByLabelText('Start date')).toHaveValue('2028-01-01');
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2028-02-29' } });
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '' } });
    expect(updateEnrolmentUser).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save dates' }));
    await screen.findByRole('status');
    expect(updateEnrolmentUser).toHaveBeenCalledWith('132', { learnerStartDate: '2028-02-29', learnerEndDate: null });
    expect(screen.queryByRole('button', { name: 'Save dates' })).not.toBeInTheDocument();
  });

  it('blocks a reversed date range', () => {
    render(<LearnerHeaderDates learnerId="132" startDate="2028-01-01" />);
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2027-12-31' } });
    expect(screen.getByRole('alert')).toHaveTextContent('End date must be on or after start date');
    expect(screen.getByRole('button', { name: 'Save dates' })).toBeDisabled();
    expect(updateEnrolmentUser).not.toHaveBeenCalled();
  });

  it('retains edits after a failed save so they can be retried', async () => {
    vi.mocked(updateEnrolmentUser).mockRejectedValue(new Error('Could not reach the server'));
    render(<LearnerHeaderDates learnerId="132" />);
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2028-01-01' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save dates' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server');
    expect(screen.getByLabelText('Start date')).toHaveValue('2028-01-01');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save dates' })).toBeEnabled());
  });
});
