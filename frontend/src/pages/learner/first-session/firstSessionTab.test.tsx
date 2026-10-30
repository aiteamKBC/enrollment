/**
 * First Learning Session: a Delivery apprentice's sidebar tab, locked until they
 * have signed all four compliance documents, then the page to book the session.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { LearnerFirstSession } from '@/api/learnerCalendar';
import { FIRST_SESSION_ROUTE, navItemsForStatus } from '@/hooks/useOnboardingRedirect';

vi.mock('@/components/feature/WorkspaceShell', () => ({ WorkspaceShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock('@/hooks/useMyLearner', () => ({ useMyLearner: () => ({ kind: 'apprenticeship', id: '670' }) }));
const api = vi.hoisted(() => ({ fetchLearnerFirstSession: vi.fn() }));
vi.mock('@/api/learnerCalendar', () => api);
vi.mock('@/components/feature/FirstSessionGate', () => ({
  Booking: () => <p>booking form</p>,
  Waiting: () => <p>booked session</p>,
}));

import LearnerFirstSessionPage from './page';

const tab = (items: { href?: string; locked?: boolean }[]) => items.find((i) => i.href === FIRST_SESSION_ROUTE);
const session = (overrides: Partial<LearnerFirstSession>): LearnerFirstSession =>
  ({ caseOwner: { name: 'Case Owner', email: 'owner@example.test' }, booked: false, event: null, startsOn: null, access: 'enrolling', canBook: false, ...overrides });

beforeEach(() => vi.clearAllMocks());

describe('the sidebar tab', () => {
  it('is locked for a Delivery apprentice until their documents are signed', () => {
    expect(tab(navItemsForStatus('Delivery', [], 'apprenticeship', false, false, true, false))).toMatchObject({ locked: true });
    expect(tab(navItemsForStatus('Delivery', [], 'apprenticeship', false, false, true, true))?.locked).toBeUndefined();
  });

  it('is only offered to Delivery apprentices', () => {
    expect(tab(navItemsForStatus('Onboarding', [], 'apprenticeship'))).toBeUndefined();
    expect(tab(navItemsForStatus('Active', [], 'apprenticeship'))).toBeUndefined();
    expect(tab(navItemsForStatus('Delivery', [], 'commercial'))).toBeUndefined();
  });
});

describe('the page', () => {
  const renderPage = () => render(<MemoryRouter><LearnerFirstSessionPage /></MemoryRouter>);

  it('stays locked until the learner has signed their documents', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: false }));
    renderPage();

    expect(await screen.findByText('Sign your compliance documents first')).toBeInTheDocument();
    expect(screen.queryByText('booking form')).not.toBeInTheDocument();
  });

  it('offers the booking once they have', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: true }));
    renderPage();

    expect(await screen.findByText('booking form')).toBeInTheDocument();
    expect(api.fetchLearnerFirstSession).toHaveBeenCalledWith('apprenticeship', '670');
  });

  it('shows the booked session once it is booked', async () => {
    api.fetchLearnerFirstSession.mockResolvedValue(session({ canBook: true, booked: true, startsOn: '2026-10-05' }));
    renderPage();

    expect(await screen.findByText('booked session')).toBeInTheDocument();
  });
});
