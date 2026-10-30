/**
 * The staff wizard's "You are viewing" banner is solid purple with a visible
 * Close wizard button.
 *
 * Regression: it used an inline primary-950/900 gradient, which index.css
 * replaces on workspace surfaces with the shared hero gradient — fading to
 * near-white on the right, where the white Close wizard button then vanished.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';

vi.mock('@/components/feature/WorkspaceShell', () => ({
  WorkspaceShell: ({ children }: { children: ReactNode }) => <div className="workspace-main">{children}</div>,
}));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/api/enrolmentUsers', () => ({
  fetchEnrolmentBoard: vi.fn(async () => ({ user: { name: 'Test enrolment student', reference: 'Test org' } })),
  updateEnrolmentUser: vi.fn(),
}));
vi.mock('@/api/commercialUsers', () => ({ fetchCommercialBoard: vi.fn(), updateCommercialBoard: vi.fn() }));
// Only the banner is under test: the shell renders just its header, and the
// wizard state is a stub.
vi.mock('../WizardShell', () => ({ WizardShell: ({ header }: { header: ReactNode }) => <>{header}</> }));
vi.mock('../WizardContext', () => ({
  WizardProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock('../WizardContext.helpers', () => ({
  useWizard: () => ({ userId: '41', isCommercial: false, board: { user: { name: 'Test enrolment student', reference: 'Test org' } }, draft: {}, saveIlr: vi.fn() }),
}));

import WizardPage from '../WizardPage';

beforeEach(() => { vi.stubGlobal('AppIcon', AppIcon); });

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      { path: '/users/:userId/wizard/:stepSlug', element: <WizardPage /> },
      { path: '*', element: <p>Left the wizard</p> },
    ],
    { initialEntries: [path] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe('staff wizard banner', () => {
  it('is solid purple, not an inline gradient the global hero rule can replace', async () => {
    renderAt('/users/41/wizard/introduction');
    const banner = await screen.findByTestId('staff-wizard-banner');

    expect(banner).toHaveClass('bg-primary-800');
    expect(banner.getAttribute('style') ?? '').not.toMatch(/primary-9[05]0|gradient/);
    expect(banner).toHaveTextContent('You are viewing: Test enrolment student (Test org)');
  });

  it('has an outlined Close wizard button that leaves the wizard', async () => {
    const router = renderAt('/users/41/wizard/introduction');
    const close = await screen.findByRole('button', { name: /Close wizard/ });

    expect(close).toHaveClass('border', 'border-white/70', 'text-white');
    await userEvent.click(close);
    expect(router.state.location.pathname).not.toMatch(/\/wizard\//);
  });
});
