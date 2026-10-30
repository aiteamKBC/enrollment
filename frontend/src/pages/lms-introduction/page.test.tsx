import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Page from './page';
import { getLmsIntroduction, getLmsIntroductionTimes, LmsIntroductionError, requestLmsIntroduction, slotLabel, type LmsIntroductionState } from '@/api/lmsIntroduction';
vi.mock('@/api/lmsIntroduction', async original => ({ ...await original<typeof import('@/api/lmsIntroduction')>(), getLmsIntroduction: vi.fn(), getLmsIntroductionTimes: vi.fn(), requestLmsIntroduction: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const base: LmsIntroductionState = { learnerName: 'Example Learner', caseOwner: 'Example Coach', durationMinutes: 30, times: ['09:00', '09:30', '10:30'], request: null };
const booked = { date: '2026-11-09', time: '10:30', note: 'Mornings', status: 'scheduled' as const, inviteSent: true, meetingLink: 'https://teams.example/join' };
const show = () => render(<MemoryRouter initialEntries={['/lms-introduction?token=SIGNED']}><Routes><Route path="/lms-introduction" element={<Page />} /></Routes></MemoryRouter>);

it('offers only the case owner\'s free times and books the chosen one', async () => {
  vi.mocked(getLmsIntroduction).mockResolvedValue(base);
  vi.mocked(getLmsIntroductionTimes).mockResolvedValue({ date: '2026-11-09', times: ['10:30'] });
  vi.mocked(requestLmsIntroduction).mockResolvedValue({ ...base, request: booked, warning: '' });
  show();
  await screen.findByRole('heading', { name: 'Book your LMS introduction with Example Coach' });
  expect(getLmsIntroduction).toHaveBeenCalledWith('SIGNED', expect.anything());
  const submit = screen.getByRole('button', { name: /Book this time/ });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-11-09' } });
  expect(await screen.findByRole('option', { name: '10:30' })).toBeInTheDocument();
  expect(getLmsIntroductionTimes).toHaveBeenCalledWith('SIGNED', '2026-11-09', expect.anything());
  expect(screen.queryByRole('option', { name: '09:00' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Start time'), { target: { value: '10:30' } });
  fireEvent.change(screen.getByLabelText(/Anything your case owner should know/), { target: { value: 'Mornings' } });
  fireEvent.click(submit);
  expect(await screen.findByRole('heading', { name: 'Your LMS introduction is booked' })).toBeInTheDocument();
  expect(requestLmsIntroduction).toHaveBeenCalledWith('SIGNED', { date: '2026-11-09', time: '10:30', note: 'Mornings' });
  expect(screen.getByText('Monday 9 November 2026 at 10:30 (UK time)')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /Open the Teams meeting/ })).toHaveAttribute('rel', 'noopener noreferrer');
});

it('says when the case owner has no free time that day and shows booking errors', async () => {
  vi.mocked(getLmsIntroduction).mockResolvedValue(base);
  vi.mocked(getLmsIntroductionTimes).mockResolvedValueOnce({ date: '2026-11-09', times: [] }).mockResolvedValueOnce({ date: '2026-11-10', times: ['09:00'] });
  vi.mocked(requestLmsIntroduction).mockRejectedValue(new LmsIntroductionError('Your case owner is not available at that time. Please choose another time.', 409, null));
  show();
  fireEvent.change(await screen.findByLabelText('Date'), { target: { value: '2026-11-09' } });
  expect(await screen.findByText(/has no free times on this day/)).toBeInTheDocument();
  expect(screen.getByLabelText('Start time')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-11-10' } });
  await screen.findByRole('option', { name: '09:00' });
  fireEvent.change(screen.getByLabelText('Start time'), { target: { value: '09:00' } });
  fireEvent.click(screen.getByRole('button', { name: /Book this time/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent('not available at that time');
});

it('never shows a booking Teams did not accept as success, and can resend it', async () => {
  const failed = { ...booked, inviteSent: false, meetingLink: '' };
  vi.mocked(getLmsIntroduction).mockResolvedValue({ ...base, request: failed, warning: 'Your slot is saved, but no calendar invite or email was sent.' });
  vi.mocked(requestLmsIntroduction).mockResolvedValue({ ...base, request: booked, warning: '' });
  show();
  expect(await screen.findByRole('heading', { name: 'Your booking is saved' })).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('no calendar invite');
  expect(screen.queryByLabelText('Date')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Send the Teams invitation again' }));
  expect(await screen.findByRole('heading', { name: 'Your LMS introduction is booked' })).toBeInTheDocument();
  expect(requestLmsIntroduction).toHaveBeenCalledWith('SIGNED', { date: '2026-11-09', time: '10:30', note: 'Mornings' });
  expect(getLmsIntroductionTimes).not.toHaveBeenCalled();
});

it('explains an invalid or expired link and can retry', async () => {
  vi.mocked(getLmsIntroduction).mockRejectedValueOnce(new LmsIntroductionError('This booking link is invalid or has expired.', 404, null));
  vi.mocked(getLmsIntroduction).mockResolvedValueOnce(base);
  show();
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid or has expired');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByLabelText('Date')).toBeInTheDocument();
});

it('formats the booked day without shifting it across time zones', () => {
  expect(slotLabel('2026-03-29', '09:00')).toBe('Sunday 29 March 2026 at 09:00 (UK time)');
  expect(slotLabel(null, null)).toBe('');
});
