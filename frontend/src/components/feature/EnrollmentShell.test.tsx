import { MemoryRouter } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceShell } from './WorkspaceShell';

const auth = {
  account: { id: 114, role: 'staff', displayName: 'Admin 114', email: 'admin@example.test' },
  isAdmin: true,
};

vi.mock('@/hooks/useAuth.helpers', () => ({
  useAuth: () => ({ auth, isAdmin: auth.isAdmin, logout: vi.fn() }),
}));
vi.mock('@/features/pending-signatures/usePendingSignatures', () => ({
  usePendingSignatures: () => ({ data: { count: 0 } }),
}));

function renderShell() {
  return render(
    <MemoryRouter initialEntries={['/users']}>
      <WorkspaceShell role="compliance" roleLabel="Enrolment Officer" navItems={[]} pageTitle="Users">
        <p>Content</p>
      </WorkspaceShell>
    </MemoryRouter>,
  );
}

describe('Admin navigation', () => {
  beforeEach(() => {
    auth.account.role = 'staff';
    auth.isAdmin = true;
  });

  it('shows Programme Structure to an authorized Admin account', () => {
    renderShell();
    expect(screen.getAllByRole('link', { name: 'Programme Structure' })[0]).toHaveAttribute('href', '/users/programme-structure');
  });

  it('does not show Programme Structure to a non-admin staff account', () => {
    auth.isAdmin = false;
    renderShell();
    expect(screen.queryByRole('link', { name: 'Programme Structure' })).not.toBeInTheDocument();
  });
});
