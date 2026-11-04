import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';

const account = { id: 1, role: 'learner', subjectType: 'learner', subjectId: 41, displayName: 'Radwa Sara', email: 'radwa@example.test' };
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account } }) }));
vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock('./SignatureSettings', () => ({ SignatureSettings: () => <div>Signature editor</div> }));

const fetchAccountSettings = vi.fn();
const saveAccountSettings = vi.fn();
vi.mock('@/api/accountSettings', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/accountSettings')>()),
  fetchAccountSettings: (...args: unknown[]) => fetchAccountSettings(...args),
  saveAccountSettings: (...args: unknown[]) => saveAccountSettings(...args),
  fetchProfilePhoto: vi.fn(async () => null),
  uploadProfilePhoto: vi.fn(),
}));

const apiChangePassword = vi.fn();
vi.mock('@/api/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/auth')>()),
  apiChangePassword: (...args: unknown[]) => apiChangePassword(...args),
}));

import ProfilePage from './page';
import { AccountSettingsApiError } from '@/api/accountSettings';

const settings = {
  learnerId: 41, learnerKind: 'apprenticeship', csrfToken: 'csrf',
  profile: { title: 'Ms', fullName: 'Radwa Sara', pronouns: 'she/her', postNominals: '', dateOfBirth: '1998-06-02' },
  contact: { emailAddress: 'radwa@example.test', mobileNumber: '07700 900123', landlineNumber: '' },
  address: { addressLine1: '10 High Street', addressLine2: '', addressLine3: 'Canterbury', addressLine4: 'Kent', residentialPostcode: 'CT1 1AA', country: 'United Kingdom' },
  editable: { profile: [], contact: [], address: [] },
};

function renderView(view: 'home' | 'about' | 'security' = 'home') {
  return render(<MemoryRouter><ProfilePage view={view} /></MemoryRouter>);
}

describe('learner account settings', () => {
  beforeEach(() => {
    fetchAccountSettings.mockReset().mockResolvedValue(settings);
    saveAccountSettings.mockReset().mockResolvedValue(settings);
    apiChangePassword.mockReset().mockResolvedValue(undefined);
  });

  it('offers only the three requested settings areas', () => {
    renderView();
    expect(screen.getByRole('link', { name: /About You/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Signature Sample/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Security Settings/ })).toBeInTheDocument();
    expect(screen.queryByText(/Notifications/i)).not.toBeInTheDocument();
  });

  it('shows profile, contact and address without any learner edit controls', async () => {
    renderView('about');
    expect(await screen.findByText('Radwa Sara')).toBeInTheDocument();
    expect(screen.getAllByText('Protected').length).toBeGreaterThan(0);
    expect(screen.getByText('07700 900123')).toBeInTheDocument();
    expect(screen.getByText('CT1 1AA')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Edit|Save changes|Change photo/i })).not.toBeInTheDocument();
    expect(saveAccountSettings).not.toHaveBeenCalled();
  });

  it('offers sign-in recovery for an expired session without leaking response content', async () => {
    fetchAccountSettings.mockRejectedValue(new AccountSettingsApiError(401, 'Your session has expired. Sign in again to view your account settings.'));
    renderView('about');
    expect(await screen.findByRole('link', { name: 'Sign in again' })).toHaveAttribute('href', '/login');
    expect(screen.queryByText(/Unexpected token|DOCTYPE|stack trace/i)).not.toBeInTheDocument();
  });

  it('validates password confirmation before calling the server', async () => {
    const user = userEvent.setup();
    renderView('security');
    await user.type(screen.getByLabelText('Current password'), 'Current-Pass-1!');
    await user.type(screen.getByLabelText(/^New password/), 'Replacement-Pass-2!');
    await user.type(screen.getByLabelText('Confirm new password'), 'Different-Pass-3!');
    await user.click(screen.getByRole('button', { name: 'Change password' }));
    expect(await screen.findByText('The new passwords do not match.')).toBeInTheDocument();
    expect(apiChangePassword).not.toHaveBeenCalled();
  });
});
