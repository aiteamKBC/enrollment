import * as React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FirstSessionReschedule } from './FirstSessionReschedule';

const mocks = vi.hoisted(() => ({ fetchEvents: vi.fn(), reschedule: vi.fn() }));
vi.mock('@/api/learnerCalendar', () => ({
  fetchLearnerCalendarEvents: mocks.fetchEvents,
  rescheduleLearnerCalendarSession: mocks.reschedule,
}));

/** A date in UK time, offset by whole days from today. */
function ukDay(offsetDays: number): string {
  const day = new Date();
  day.setDate(day.getDate() + offsetDays);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(day);
}

function event(overrides: Record<string, unknown> = {}) {
  return {
    eventKey: 'first-session:7:1:2026-10-05',
    source: 'first-session',
    status: 'scheduled',
    scheduledDate: ukDay(7),
    scheduledTime: '10:00:00',
    durationMinutes: 60,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('React', React);
  mocks.fetchEvents.mockResolvedValue({ events: [event()] });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const view = () => render(<FirstSessionReschedule kind="commercial" learnerId="7" />);

describe('FirstSessionReschedule', () => {
  it('shows the booked session and offers to move it', async () => {
    view();

    expect(await screen.findByText(/10:00 AM/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Reschedule/ })).toBeTruthy();
  });

  it('renders nothing for a learner with no first session', async () => {
    // Enrolled before the feature, or imported from Aptem — both are skipped
    // by the booking code, and an empty control would imply otherwise.
    mocks.fetchEvents.mockResolvedValue({ events: [] });
    const { container } = view();

    await waitFor(() => expect(mocks.fetchEvents).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('ignores a cancelled session, which is not one to move', async () => {
    mocks.fetchEvents.mockResolvedValue({ events: [event({ status: 'cancelled' })] });
    const { container } = view();

    await waitFor(() => expect(mocks.fetchEvents).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('will not move a session whose day has passed', async () => {
    mocks.fetchEvents.mockResolvedValue({ events: [event({ scheduledDate: ukDay(-1) })] });
    view();

    // The date is still shown — it is when the learner's programme started —
    // but there is nothing to rearrange.
    expect(await screen.findByText(/already held/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Reschedule/ })).toBeNull();
  });

  it('still offers to move a session later the same day', async () => {
    // "Past" is the whole day having gone, not the hour: a session at 4pm can
    // still be moved at 9am on the same date.
    mocks.fetchEvents.mockResolvedValue({ events: [event({ scheduledDate: ukDay(0) })] });
    view();

    expect(await screen.findByRole('button', { name: /Reschedule/ })).toBeTruthy();
  });

  it('sends the new slot and reports that both people were re-invited', async () => {
    const moved = event({ scheduledDate: ukDay(10), scheduledTime: '14:30:00' });
    mocks.reschedule.mockResolvedValue({ event: moved, warning: '' });
    view();

    fireEvent.click(await screen.findByRole('button', { name: /Reschedule/ }));
    fireEvent.change(screen.getByLabelText(/New date/), { target: { value: ukDay(10) } });
    fireEvent.click(within(screen.getByRole('list', { name: 'Hour' })).getByRole('button', { name: '14' }));
    fireEvent.click(within(screen.getByRole('list', { name: 'Minute' })).getByRole('button', { name: '30' }));
    fireEvent.click(screen.getByRole('button', { name: /Move session/ }));

    await waitFor(() => expect(mocks.reschedule).toHaveBeenCalledWith('commercial', '7', expect.objectContaining({
      eventKey: 'first-session:7:1:2026-10-05',
      scheduledDate: ukDay(10),
      scheduledTime: '14:30',
      durationMinutes: 60,
    })));
    expect(await screen.findByText(/have been sent the new invitation/)).toBeTruthy();
  });

  it('says the Teams meeting is unconfirmed when Graph warns', async () => {
    // The row moved but Microsoft has not confirmed it. Reporting a clean
    // move would imply an invitation nobody has received.
    mocks.reschedule.mockResolvedValue({ event: event(), warning: 'Microsoft rejected the change.' });
    view();

    fireEvent.click(await screen.findByRole('button', { name: /Reschedule/ }));
    fireEvent.click(within(screen.getByRole('list', { name: 'Hour' })).getByRole('button', { name: '11' }));
    fireEvent.click(screen.getByRole('button', { name: /Move session/ }));

    expect(await screen.findByText(/Microsoft rejected the change/)).toBeTruthy();
  });

  it('reports a failure instead of implying the session moved', async () => {
    mocks.reschedule.mockRejectedValue(new Error('That time overlaps another session.'));
    view();

    fireEvent.click(await screen.findByRole('button', { name: /Reschedule/ }));
    fireEvent.click(within(screen.getByRole('list', { name: 'Hour' })).getByRole('button', { name: '11' }));
    fireEvent.click(screen.getByRole('button', { name: /Move session/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('That time overlaps another session.');
  });

  it('stays quiet when the calendar cannot be read', async () => {
    // The rest of the enrolment record is still usable, and the session can
    // still be moved from the learner's own calendar.
    mocks.fetchEvents.mockRejectedValue(new Error('offline'));
    const { container } = view();

    await waitFor(() => expect(mocks.fetchEvents).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
