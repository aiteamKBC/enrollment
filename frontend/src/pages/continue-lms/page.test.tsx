import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ContinueInLms from './page';
vi.mock('react-router-dom', () => ({ useLocation: () => ({ search: '' }) }));
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { role: 'learner' } } }) }));
beforeEach(() => { vi.stubEnv('VITE_LMS_URL', 'https://lms.example.invalid'); });
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
it('opens LMS only after the backend confirms the transfer', async () => {
  const request = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ csrfToken: 'synthetic-csrf' }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'accepted' }) });
  vi.stubGlobal('fetch', request);
  render(<ContinueInLms />);
  expect(screen.queryByRole('link', { name: 'Open LMS' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Continue to LMS' }));
  expect(await screen.findByRole('link', { name: 'Open LMS' })).toHaveAttribute('href', 'https://lms.example.invalid/');
  expect(request.mock.calls[1][1].headers['X-CSRFToken']).toBe('synthetic-csrf');
});
it('keeps conflicts visible and permits retry without claiming success', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ csrfToken: 'synthetic-csrf' }) })
    .mockResolvedValueOnce({ ok: false, json: async () => ({ error: 'Identity conflict needs review.' }) }));
  render(<ContinueInLms />);
  fireEvent.click(screen.getByRole('button', { name: 'Continue to LMS' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Identity conflict needs review.');
  expect(screen.queryByRole('link', { name: 'Open LMS' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Continue to LMS' })).toBeEnabled();
});
it('refuses a missing or unsafe LMS destination', () => {
  vi.stubEnv('VITE_LMS_URL', 'javascript:alert(1)');
  render(<ContinueInLms />);
  expect(screen.getByRole('alert')).toHaveTextContent('has not been configured');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
