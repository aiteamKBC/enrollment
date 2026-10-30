// The public "book a one-to-one LMS introduction" page opened from the account
// invitation. The signed token in the emailed link is the only credential.
export interface LmsIntroductionRequest {
  date: string | null;
  time: string | null;
  note: string;
  // "requested": saved before bookings were immediate, waiting for the case owner.
  status: 'requested' | 'scheduled';
  // Whether Microsoft Teams accepted the booking (the learner was invited).
  inviteSent: boolean;
  meetingLink: string;
}
export interface LmsIntroductionState {
  learnerName: string;
  caseOwner: string;
  durationMinutes: number;
  times: string[];
  request: LmsIntroductionRequest | null;
  // Saved, but the Teams invitation was not sent: never shown as plain success.
  warning?: string;
}
export interface LmsIntroductionTimes { date: string; times: string[] }
export class LmsIntroductionError extends Error {
  readonly status: number;
  readonly state: LmsIntroductionState | null;
  constructor(message: string, status: number, state: LmsIntroductionState | null) { super(message); this.status = status; this.state = state; }
}

// A calendar date, not an instant: formatted in UTC so no time-zone shift can move the day.
export function slotLabel(date: string | null, time: string | null) {
  if (!date) return '';
  const [year, month, day] = date.split('-').map(Number);
  const instant = new Date(Date.UTC(year, month - 1, day));
  const weekday = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', weekday: 'long' }).format(instant);
  const label = `${weekday} ${new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', day: 'numeric', month: 'long', year: 'numeric' }).format(instant)}`;
  return time ? `${label} at ${time} (UK time)` : label;
}
const url = '/login_api/public/lms-introduction/';
async function call<T>(input: string, init?: globalThis.RequestInit): Promise<T> {
  const response = await fetch(input, { credentials: 'omit', ...init,
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...init?.headers } });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    throw new LmsIntroductionError(data?.error || 'Could not reach the booking service. Please retry.', response.status, data?.request !== undefined ? data : null);
  }
  return data;
}
export const getLmsIntroduction = (token: string, signal?: AbortSignal) =>
  call<LmsIntroductionState>(`${url}?token=${encodeURIComponent(token)}`, { signal });
// The start times the case owner is free for on one day.
export const getLmsIntroductionTimes = (token: string, date: string, signal?: AbortSignal) =>
  call<LmsIntroductionTimes>(`${url}?token=${encodeURIComponent(token)}&date=${encodeURIComponent(date)}`, { signal });
export const requestLmsIntroduction = (token: string, body: { date: string; time: string; note: string }) =>
  call<LmsIntroductionState>(url, { method: 'POST', body: JSON.stringify({ token, ...body }) });
