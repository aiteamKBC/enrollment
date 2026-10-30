/**
 * Moving to another enrolment step opens it at the top.
 *
 * The workspace shell stays mounted between steps (only the URL's slug
 * changes), so its scrolling <main> used to keep the previous step's position:
 * Next at the foot of a long step landed the learner at the foot of the next.
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
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: 'apprenticeship', id: '41' }) }));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
// The wizard itself is not under test: a failed load keeps the page to its
// chrome, which is all the scroll reset needs.
vi.mock('@/api/extendedIlr', () => ({ fetchWizardBootstrap: vi.fn(async () => { throw new Error('offline'); }) }));

import LearnerOnboardingPage from '../page';

// Production auto-imports AppIcon (vite.config.ts); tests have no such plugin.
beforeEach(() => { vi.stubGlobal('AppIcon', AppIcon); });

describe('learner enrolment step scroll', () => {
  it('opens each step at the top instead of keeping the previous step’s scroll', async () => {
    const router = createMemoryRouter(
      [{ path: '/learner/onboarding/:stepSlug', element: <LearnerOnboardingPage /> }],
      { initialEntries: ['/learner/onboarding/introduction'] },
    );
    render(<RouterProvider router={router} />);
    await screen.findByText('offline');

    const scroller = screen.getByTestId('scroller');
    scroller.scrollTop = 1200;
    await act(async () => { await router.navigate('/learner/onboarding/personal-details'); });

    expect(scroller.scrollTop).toBe(0);
  });

  it('leaves the scroll alone when the step has not changed', async () => {
    const router = createMemoryRouter(
      [{ path: '/learner/onboarding/:stepSlug', element: <LearnerOnboardingPage /> }],
      { initialEntries: ['/learner/onboarding/introduction'] },
    );
    render(<RouterProvider router={router} />);
    await screen.findByText('offline');

    const scroller = screen.getByTestId('scroller');
    scroller.scrollTop = 300;
    await act(async () => { await router.navigate('/learner/onboarding/introduction?tab=1'); });

    expect(scroller.scrollTop).toBe(300);
  });
});
