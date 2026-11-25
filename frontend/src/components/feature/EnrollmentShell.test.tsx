import { MemoryRouter } from 'react-router-dom';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

function renderShell(path = '/users') {
  return render(
    <MemoryRouter initialEntries={[path]}>
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

  it.each([
    '/users', '/users/compliance-documents', '/users/programme-structure',
    '/users/eligibility-builder', '/users/eligibility-applications',
  ])('uses the shared admin chrome and correct active link on %s', path => {
    auth.account.role = 'admin';
    const { container } = renderShell(path);
    expect(container.querySelector('.enrolment-admin-shell')).toBeInTheDocument();
    expect(container.querySelector('.enrolment-admin-content')).toHaveTextContent('Content');
    expect(container.querySelector(`nav a[href="${path}"]`)).toHaveAttribute('aria-current', 'page');
    expect(container.querySelector('.up-college-lockup img')).toHaveAttribute('src', '/images/Kent%20logo.png');
    expect(container.querySelector('.up-sidebar-art img')).toHaveAttribute('src', '/images/dashboard-sidebar-building.png');
  });

  it.each(['learner', 'employer'])('keeps the %s shell outside the fixed admin layout', role => {
    auth.account.role = role;
    const { container } = renderShell(role === 'learner' ? '/learner/onboarding' : '/employers/114/documents');
    expect(container.querySelector('.enrolment-admin-shell')).not.toBeInTheDocument();
    expect(container.querySelector('.enrolment-admin-content')).not.toBeInTheDocument();
    expect(container.querySelector('.up-sidebar-art')).not.toBeInTheDocument();
  });

  it('preserves the account menu and settings destination on other admin pages', async () => {
    const user = userEvent.setup();
    renderShell('/users/compliance-documents');
    const control = screen.getByRole('button', { name: 'Open account menu for Admin 114' });
    await user.click(control);
    expect(control).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('menuitem', { name: 'Account Settings' })).toHaveAttribute('href', '/profile');
    await user.keyboard('{Escape}');
    expect(control).toHaveAttribute('aria-expanded', 'false');
  });

  it('keeps the staff navigation order without the retired Report Templates link', () => {
    auth.account.role = 'admin';
    renderShell();
    const links = within(screen.getByRole('navigation', { name: 'Enrolment navigation' })).getAllByRole('link');
    expect(links.map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['Users', '/users'],
      ['Eligibility Applications', '/users/eligibility-applications'],
      ['Eligibility Form Builder', '/users/eligibility-builder'],
      ['Enrolment Form Builder', '/users/wizard-builder'],
      ['Programme Structure', '/users/programme-structure'],
      ['Documents', '/users/compliance-documents'],
      ['Coach Directory', '/users/coach-directory'],
      ['First-Session Bookings', '/users/first-sessions'],
      ['Open LMS', '/lms'],
    ]);
    expect(screen.queryByRole('link', { name: /report templates/i })).not.toBeInTheDocument();
  });
});
