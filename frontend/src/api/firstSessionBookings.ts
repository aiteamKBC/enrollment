// ============================================================================
// First-session bookings — enrolment workspace, read only.
//
// Backed by GET /learner_api/first-session-bookings/
// (backend/learner_api/first_session_bookings.py), which reads every
// "first-session" row of "Coach".coach_calendar_event — past, cancelled and
// failed-to-sync bookings included — and ties each to its enrolment learner.
// ============================================================================

export type FirstSessionState =
  | 'scheduled'
  | 'requested'
  | 'in-progress'
  | 'awaiting-signature'
  | 'completed'
  | 'cancelled'
  | 'sync-pending'
  | 'sync-failed'
  | 'needs-reconciliation'
  | string;

export interface FirstSessionBooking {
  id: number;
  /** Enrolment id; null when the calendar row could not be tied to a learner. */
  learnerId: number | null;
  source: 'apprenticeship' | 'commercial' | null;
  learnerName: string;
  learnerEmail: string;
  programme: string;
  caseOwner: { name: string; email: string };
  /** When the booking was made — an absolute instant. */
  bookedAt: string | null;
  /** The UK day it was booked on, YYYY-MM-DD. */
  bookedDate: string | null;
  /** UK wall clock. */
  sessionDate: string | null;
  sessionTime: string | null;
  durationMinutes: number | null;
  meetingLink: string;
  state: FirstSessionState;
  status: string;
  syncState: string;
}

export interface FirstSessionBookingFilters {
  caseOwner: string;
  programme: string;
  sessionFrom: string;
  sessionTo: string;
  bookedFrom: string;
  bookedTo: string;
}

export const FILTER_KEYS: (keyof FirstSessionBookingFilters)[] = [
  'caseOwner', 'programme', 'sessionFrom', 'sessionTo', 'bookedFrom', 'bookedTo',
];

export async function fetchFirstSessionBookings(signal?: AbortSignal): Promise<{ count: number; results: FirstSessionBooking[] }> {
  let response: Response;
  try {
    response = await fetch('/learner_api/first-session-bookings/', {
      credentials: 'include',
      signal,
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    });
  } catch (reason) {
    if (signal?.aborted) throw reason;
    throw new Error('Could not reach the server. Please try again.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    throw new Error(data?.error || `Could not load first-session bookings (${response.status}).`);
  }
  return data;
}

/** The value a case-owner filter matches on: the email when known, else the name. */
export function caseOwnerKey(booking: Pick<FirstSessionBooking, 'caseOwner'>): string {
  return booking.caseOwner.email.trim().toLowerCase() || booking.caseOwner.name.trim();
}

/** Within an inclusive YYYY-MM-DD range; a missing date fails any bound that is set. */
function inRange(value: string | null, from: string, to: string): boolean {
  if (!from && !to) return true;
  if (!value) return false;
  return (!from || value >= from) && (!to || value <= to);
}

export function filterFirstSessionBookings(
  bookings: FirstSessionBooking[],
  filters: FirstSessionBookingFilters,
): FirstSessionBooking[] {
  return bookings.filter(booking =>
    (!filters.caseOwner || caseOwnerKey(booking) === filters.caseOwner)
    && (!filters.programme || booking.programme === filters.programme)
    && inRange(booking.sessionDate, filters.sessionFrom, filters.sessionTo)
    && inRange(booking.bookedDate, filters.bookedFrom, filters.bookedTo));
}

/** Case owners present in the list, as {value, label}, sorted by label. */
export function caseOwnerOptions(bookings: FirstSessionBooking[]): { value: string; label: string }[] {
  const options = new Map<string, string>();
  for (const booking of bookings) {
    const value = caseOwnerKey(booking);
    if (value && !options.has(value)) options.set(value, booking.caseOwner.name.trim() || booking.caseOwner.email.trim());
  }
  return [...options].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
}

export function programmeOptions(bookings: FirstSessionBooking[]): string[] {
  return [...new Set(bookings.map(booking => booking.programme).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}
