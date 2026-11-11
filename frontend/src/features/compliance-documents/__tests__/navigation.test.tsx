import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/EnrollmentShell';

const auth = vi.hoisted(() => ({ role: 'staff' }));
vi.mock('@/hooks/useAuth.helpers', () => ({ useAuth: () => ({ auth: { account: { role: auth.role, displayName: 'Synthetic User', subjectId: '1', learnerType: 'apprenticeship' } }, logout: vi.fn() }) }));
describe('existing enrolment navigation', () => {
  it.each(['staff', 'learner', 'employer'])('keeps the document builder limited to the staff navigation: %s', (role) => {
    auth.role = role;
    render(<MemoryRouter><WorkspaceShell pageTitle="Test" role="test" roleLabel="Test" navItems={[]}>Content</WorkspaceShell></MemoryRouter>);
    const link = screen.queryByRole('link', { name: 'Compliance Documents' });
    if (role === 'staff') expect(link).toHaveAttribute('href', '/users/compliance-documents');
    else expect(link).not.toBeInTheDocument();
  });
});
