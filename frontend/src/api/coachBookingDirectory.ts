export const bookingTypes = [
  { key: 'first_session', label: 'First session', description: 'Book your first session with your coach.' },
  { key: 'support_session', label: 'Support sessions', description: 'Targeted support when you need it.' },
  { key: 'coaching_session', label: 'Coaching sessions', description: 'Book at least once a month to keep your learning on track.' },
  { key: 'progress_review', label: 'Progress review', description: 'Review your progress and next actions every 10 weeks.' },
  { key: 'one_to_one', label: 'One to one', description: 'A private one-to-one conversation with your coach.' },
] as const;
export type BookingLinks = Record<typeof bookingTypes[number]['key'], string>;
export interface PublicCoach { name: string; slug: string; links: BookingLinks }
export interface DirectoryCoach extends PublicCoach { id: number; version: number }
export const emptyLinks = (): BookingLinks => ({ first_session: '', support_session: '', coaching_session: '', progress_review: '', one_to_one: '' });
export const coachPagePath = (slug: string) => `/coach-booking/${encodeURIComponent(slug)}`;
async function request<T>(url: string, init?: globalThis.RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: url.includes('/public/') ? 'omit' : 'include', ...init,
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest', ...init?.headers } });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(data?.error || 'Could not load coach bookings. Please retry.');
  return data;
}
const base = '/login_api/admin/coach-directory/';
export const listCoaches = (signal?: AbortSignal) => request<{ coaches: DirectoryCoach[] }>(base, { signal });
export const getPublicCoach = (slug: string, signal?: AbortSignal) => request<PublicCoach>(`/login_api/public/coaches/${encodeURIComponent(slug)}/`, { signal });
export const saveCoach = (coach: { name: string; links: BookingLinks; id?: number; version?: number }) => request<DirectoryCoach>(coach.id ? `${base}${coach.id}/` : base, { method: coach.id ? 'PUT' : 'POST', body: JSON.stringify(coach) });
export const deleteCoach = (coach: DirectoryCoach) => request<{ deleted: boolean }>(`${base}${coach.id}/`, { method: 'DELETE', body: JSON.stringify({ version: coach.version }) });
