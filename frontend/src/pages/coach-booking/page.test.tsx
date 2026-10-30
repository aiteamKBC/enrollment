import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Page from './page';
import { emptyLinks, getPublicCoach } from '@/api/coachBookingDirectory';
vi.mock('@/api/coachBookingDirectory', async original => ({ ...await original<typeof import('@/api/coachBookingDirectory')>(), getPublicCoach: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const show = () => render(<MemoryRouter initialEntries={['/coach-booking/example']}><Routes><Route path="/coach-booking/:slug" element={<Page />} /></Routes></MemoryRouter>);
it('shows public booking choices without an account and opens only the selected booking provider', async () => {
  vi.mocked(getPublicCoach).mockResolvedValue({ name: 'Example Coach', slug: 'example', links: { ...emptyLinks(), first_session: 'https://example.org/book' } });
  show();
  await screen.findByRole('heading', { name: 'Book a session with Example Coach' });
  expect(screen.queryByTitle(/booking with/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Support sessions/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /First session/ }));
  expect(screen.getByTitle('First session booking with Example Coach')).toHaveAttribute('src', 'https://example.org/book');
  expect(screen.getByRole('link', { name: 'Open booking in new tab' })).toHaveAttribute('rel', 'noopener noreferrer');
  expect(screen.queryByRole('button', { name: /Edit|Remove/ })).not.toBeInTheDocument();
});
it('rejects unsafe booking URLs and displays load errors', async () => {
  vi.mocked(getPublicCoach).mockRejectedValue(new Error('Coach page not found.'));
  show();
  expect(await screen.findByRole('alert')).toHaveTextContent('Coach page not found.');
  vi.mocked(getPublicCoach).mockResolvedValue({ name: 'Example', slug: 'example', links: { ...emptyLinks(), first_session: 'javascript:alert(1)' } });
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('button', { name: /First session/ })).toBeDisabled();
});
it('opens the one-to-one booking directly from an emailed link', async () => {
  vi.mocked(getPublicCoach).mockResolvedValue({ name: 'Example Coach', slug: 'example', links: { ...emptyLinks(), one_to_one: 'https://example.org/one-to-one' } });
  render(<MemoryRouter initialEntries={['/coach-booking/example?session=one_to_one']}><Routes><Route path="/coach-booking/:slug" element={<Page />} /></Routes></MemoryRouter>);
  expect(await screen.findByTitle('One to one booking with Example Coach')).toHaveAttribute('src', 'https://example.org/one-to-one');
  expect(screen.getByRole('button', { name: /One to one/ })).toHaveAttribute('aria-pressed', 'true');
});
it('ignores an emailed session the coach does not offer', async () => {
  vi.mocked(getPublicCoach).mockResolvedValue({ name: 'Example Coach', slug: 'example', links: { ...emptyLinks(), first_session: 'https://example.org/book' } });
  render(<MemoryRouter initialEntries={['/coach-booking/example?session=one_to_one']}><Routes><Route path="/coach-booking/:slug" element={<Page />} /></Routes></MemoryRouter>);
  expect(await screen.findByRole('button', { name: /One to one/ })).toBeDisabled();
  expect(screen.queryByTitle(/booking with/)).not.toBeInTheDocument();
});
