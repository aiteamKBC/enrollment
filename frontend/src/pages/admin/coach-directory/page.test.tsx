import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import Page from './page';
import { deleteCoach, emptyLinks, listCoaches, saveCoach } from '@/api/coachBookingDirectory';
vi.mock('../_shared/AdminPage', () => ({ AdminPage: ({ children, actions }: { children: ReactNode; actions: ReactNode }) => <>{actions}{children}</> }));
vi.mock('@/api/coachBookingDirectory', async original => ({ ...await original<typeof import('@/api/coachBookingDirectory')>(), listCoaches: vi.fn(), saveCoach: vi.fn(), deleteCoach: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); });
it('combines name search and link filtering while retaining unavailable booking options', async () => {
  const coaches = [
    { id: 1, version: 1, name: 'Alpha Coach', slug: 'alpha', links: { ...emptyLinks(), support_session: 'https://example.test/support' } },
    { id: 2, version: 1, name: 'Beta Coach', slug: 'beta', links: emptyLinks() },
  ];
  vi.mocked(listCoaches).mockResolvedValue({ coaches });
  render(<MemoryRouter><Page /></MemoryRouter>);
  await screen.findByRole('heading', { name: 'Alpha Coach' });
  expect(screen.getAllByRole('link', { name: 'Open booking page' })[0]).toHaveAttribute('href', '/coach-booking/alpha');
  const options = screen.getByRole('list', { name: 'Booking options for Alpha Coach' });
  expect(within(options).getByText('First session').closest('li')).toHaveAttribute('data-available', 'false');
  expect(within(options).getByText('Support sessions').closest('li')).toHaveAttribute('data-available', 'true');
  expect(within(options).getByText('One to one').closest('li')).toHaveAttribute('data-available', 'false');
  fireEvent.change(screen.getByRole('combobox', { name: 'Filter coaches' }), { target: { value: 'without-links' } });
  expect(screen.queryByRole('heading', { name: 'Alpha Coach' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Beta Coach' })).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Search coaches' }), { target: { value: 'ALPHA' } });
  expect(screen.getByText('No coaches found.')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'Filter coaches' }), { target: { value: 'all' } });
  expect(screen.getByRole('heading', { name: 'Alpha Coach' })).toBeInTheDocument();
  expect(screen.getByText('Total coaches').parentElement).toHaveTextContent('2');
  expect(screen.getByText('Active booking options').parentElement).toHaveTextContent('1');
  expect(screen.getByText('Sessions offered').parentElement).toHaveTextContent('—');
  expect(saveCoach).not.toHaveBeenCalled();
  expect(deleteCoach).not.toHaveBeenCalled();
});
it('copies the same real page URL from both controls and preserves the edit values', async () => {
  const coach = { id: 7, version: 3, name: 'Example Coach', slug: 'example-coach', links: { ...emptyLinks(), one_to_one: 'https://example.test/private' } };
  const descriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  try {
    vi.mocked(listCoaches).mockResolvedValue({ coaches: [coach] });
    render(<MemoryRouter><Page /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Copy booking page link for Example Coach' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(new URL('/coach-booking/example-coach', window.location.origin).href));
    fireEvent.click(screen.getByRole('button', { name: 'Copy link' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
    const link = screen.getByRole('link', { name: 'Open booking page' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByRole('button', { name: 'Add coach' })).toBeDisabled();
    expect(screen.getByLabelText('Coach name')).toHaveValue(coach.name);
    expect(screen.getByLabelText('One to one URL')).toHaveValue(coach.links.one_to_one);
    expect(saveCoach).not.toHaveBeenCalled();
    expect(deleteCoach).not.toHaveBeenCalled();
  } finally {
    if (descriptor) Object.defineProperty(navigator, 'clipboard', descriptor);
    else Reflect.deleteProperty(navigator, 'clipboard');
  }
});
it('creates a coach with four booking links and shows its public page', async () => {
  const coach = { id: 1, version: 1, name: 'Example Coach', slug: 'example-coach', links: { ...emptyLinks(), first_session: 'https://example.org/book' } };
  vi.mocked(listCoaches).mockResolvedValueOnce({ coaches: [] }).mockResolvedValue({ coaches: [coach] });
  vi.mocked(saveCoach).mockResolvedValue(coach);
  render(<MemoryRouter><Page /></MemoryRouter>);
  await screen.findByText('No coaches found.');
  fireEvent.click(screen.getByRole('button', { name: 'Add coach' }));
  fireEvent.change(screen.getByLabelText('Coach name'), { target: { value: coach.name } });
  fireEvent.change(screen.getByLabelText('First session URL'), { target: { value: coach.links.first_session } });
  fireEvent.submit(screen.getByRole('form', { name: 'Coach editor' }));
  await waitFor(() => expect(saveCoach).toHaveBeenCalledWith({ name: coach.name, links: coach.links }));
  expect(await screen.findByRole('link', { name: 'Open booking page' })).toHaveAttribute('href', '/coach-booking/example-coach');
});
it('retains editor values if saving fails', async () => {
  vi.mocked(listCoaches).mockResolvedValue({ coaches: [] });
  vi.mocked(saveCoach).mockRejectedValue(new Error('Coach changed. Reload and retry.'));
  render(<MemoryRouter><Page /></MemoryRouter>);
  await screen.findByText('No coaches found.');
  fireEvent.click(screen.getByRole('button', { name: 'Add coach' }));
  fireEvent.change(screen.getByLabelText('Coach name'), { target: { value: 'Keep edits' } });
  fireEvent.submit(screen.getByRole('form', { name: 'Coach editor' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Coach changed');
  expect(screen.getByDisplayValue('Keep edits')).toBeInTheDocument();
});
it('explains removal and sends no delete until the administrator confirms', async () => {
  const coach = { id: 8, version: 3, name: 'Example Coach', slug: 'example-coach', links: emptyLinks() };
  vi.mocked(listCoaches).mockResolvedValue({ coaches: [coach] });
  vi.mocked(deleteCoach).mockResolvedValue({ deleted: true });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<MemoryRouter><Page /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('This does not delete their LMS account or cancel existing bookings.'));
  expect(deleteCoach).not.toHaveBeenCalled();
  expect(screen.getByText('Example Coach')).toBeInTheDocument();
  confirm.mockReturnValue(true);
  vi.mocked(listCoaches).mockResolvedValue({ coaches: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  await waitFor(() => expect(deleteCoach).toHaveBeenCalledWith(coach));
  await screen.findByText('No coaches found.');
});
it('edits a coach saved before the one-to-one link existed', async () => {
  const { one_to_one: _unused, ...olderLinks } = { ...emptyLinks(), first_session: 'https://example.org/book' };
  const coach = { id: 4, version: 2, name: 'Example Coach', slug: 'example-coach', links: olderLinks as ReturnType<typeof emptyLinks> };
  vi.mocked(listCoaches).mockResolvedValue({ coaches: [coach] });
  vi.mocked(saveCoach).mockResolvedValue({ ...coach, links: { ...emptyLinks(), ...olderLinks, one_to_one: 'https://example.org/1to1' }, version: 3 });
  render(<MemoryRouter><Page /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  expect(screen.getByLabelText('One to one URL')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('One to one URL'), { target: { value: 'https://example.org/1to1' } });
  fireEvent.submit(screen.getByRole('form', { name: 'Coach editor' }));
  await waitFor(() => expect(saveCoach).toHaveBeenCalledWith(expect.objectContaining({ id: 4, version: 2, links: { ...emptyLinks(), first_session: 'https://example.org/book', one_to_one: 'https://example.org/1to1' } })));
});
