import { coachFetch } from '@/lib/coachFetch';

export interface EntraPerson { id: string; name: string; email: string }

export async function searchEntraPeople(
  query: string,
  signal?: AbortSignal,
  includeGuests = false,
): Promise<{ people: EntraPerson[]; hasMore: boolean }> {
  const base = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
  const guestQuery = includeGuests ? '&includeGuests=1' : '';
  const response = await coachFetch(`${base}/curriculum/teams-directory/?q=${encodeURIComponent(query.trim())}${guestQuery}`, { signal });
  // A crashed or restarting server answers with an HTML page, not JSON.
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(result?.error || 'Entra search is unavailable. Retry or enter a full email address.');
  if (!result || !Array.isArray(result.people) || !result.people.every((person: EntraPerson) => person &&
    typeof person.id === 'string' && typeof person.name === 'string' && typeof person.email === 'string')) {
    throw new Error('Entra search results could not be read.');
  }
  return result;
}
