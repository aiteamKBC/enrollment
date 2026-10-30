/**
 * The staff enrolment wizard opens each step at the top, as the learner's does.
 * See useResetWorkspaceScroll for why the shell keeps the old position.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';

vi.mock('@/components/feature/WorkspaceShell', () => ({
  WorkspaceShell: ({ children }: { children: ReactNode }) => (
    <div data-testid="scroller" className="workspace-main">{children}</div>
  ),
}));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
// The wizard itself is not under test: a failed load keeps the page to its
// retry state, which is all the scroll reset needs.
vi.mock('@/api/enrolmentUsers', () => ({
  fetchEnrolmentBoard: vi.fn(async () => { throw new Error('offline'); }),
  updateEnrolmentUser: vi.fn(),
}));
vi.mock('@/api/commercialUsers', () => ({
  fetchCommercialBoard: vi.fn(async () => { throw new Error('offline'); }),
  updateCommercialBoard: vi.fn(),
}));

import WizardPage from '../WizardPage';

// Production auto-imports AppIcon (vite.config.ts); tests have no such plugin.
beforeEach(() => { vi.stubGlobal('AppIcon', AppIcon); });

function renderAt(path: string) {
  const router = createMemoryRouter(
    [{ path: '/users/:userId/wizard/:stepSlug', element: <WizardPage /> }],
    { initialEntries: [path] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe('staff enrolment wizard step scroll', () => {
  it('opens each step at the top instead of keeping the previous step’s scroll', async () => {
    const router = renderAt('/users/41/wizard/introduction');
    await screen.findByText('offline');

    const scroller = screen.getByTestId('scroller');
    scroller.scrollTop = 1200;
    await act(async () => { await router.navigate('/users/41/wizard/personal-details'); });

    expect(scroller.scrollTop).toBe(0);
  });

  it('leaves the scroll alone when the step has not changed', async () => {
    const router = renderAt('/users/41/wizard/introduction');
    await screen.findByText('offline');

    const scroller = screen.getByTestId('scroller');
    scroller.scrollTop = 300;
    await act(async () => { await router.navigate('/users/41/wizard/introduction?source=apprenticeship'); });

    expect(scroller.scrollTop).toBe(300);
  });
});
