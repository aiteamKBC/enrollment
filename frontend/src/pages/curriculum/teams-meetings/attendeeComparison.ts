import { coachFetch } from '@/lib/coachFetch';

export interface ComparedPerson {
  email: string;
  name: string;
}

export interface AttendeeComparison {
  status: 'match' | 'different';
  lmsCount: number;
  teamsCount: number;
  matchingCount: number;
  extraCount: number;
  missingCount: number;
  matching: ComparedPerson[];
  extraOnTeams: ComparedPerson[];
  missingFromTeams: ComparedPerson[];
  /** Both lists are occupied, so one pair of them may be a single mailbox. */
  aliasPossible: boolean;
  checkedAt: string;
}

function people(value: unknown): ComparedPerson[] | null {
  if (!Array.isArray(value)) return null;
  const rows: ComparedPerson[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') return null;
    const person = item as Record<string, unknown>;
    if (typeof person.email !== 'string' || !person.email) return null;
    rows.push({ email: person.email, name: typeof person.name === 'string' ? person.name : '' });
  }
  return rows;
}

/**
 * Read Microsoft's current attendee list and compare it with the published one.
 *
 * A read on both sides. It sends no invitation, moves no session and writes
 * nothing to the meeting or the LMS, so it is safe to press at any time --
 * including while the form beside it holds edits that have not been saved.
 */
export async function compareTeamsAttendees(liveSessionId: string): Promise<AttendeeComparison> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 60000);
  try {
    const base = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
    const response = await coachFetch(
      `${base}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/compare-attendees/`,
      { method: 'GET', signal: controller.signal },
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result?.error || 'Unable to read the current attendee list from Microsoft.');
    const matching = people(result?.matching);
    const extraOnTeams = people(result?.extraOnTeams);
    const missingFromTeams = people(result?.missingFromTeams);
    const counts = ['lmsCount', 'teamsCount', 'matchingCount', 'extraCount', 'missingCount'] as const;
    if (!matching || !extraOnTeams || !missingFromTeams
      || (result.status !== 'match' && result.status !== 'different')
      || !counts.every(key => Number.isInteger(result[key]) && result[key] >= 0)
      || typeof result.checkedAt !== 'string' || !Number.isFinite(Date.parse(result.checkedAt))) {
      throw new Error('The attendee comparison could not be read. Try again.');
    }
    return {
      status: result.status, lmsCount: result.lmsCount, teamsCount: result.teamsCount,
      matchingCount: result.matchingCount, extraCount: result.extraCount, missingCount: result.missingCount,
      matching, extraOnTeams, missingFromTeams,
      aliasPossible: result.aliasPossible === true, checkedAt: result.checkedAt,
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('Reading the Microsoft attendee list timed out. Try the comparison again.');
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

/**
 * The addresses in the open form that no save has published yet.
 *
 * Microsoft was never asked about these, so they belong outside the comparison
 * entirely: counting one as "missing from Teams" would report an unsaved edit
 * as a lost invitation, which is the opposite of what happened.
 */
export function pendingInvitations(typed: string[], published: string[]): string[] {
  const sent = new Set(published.map(value => value.trim().toLowerCase()).filter(Boolean));
  const seen = new Set<string>();
  const waiting: string[] = [];
  for (const value of typed) {
    const address = value.trim().toLowerCase();
    if (!address || sent.has(address) || seen.has(address)) continue;
    seen.add(address);
    waiting.push(address);
  }
  return waiting;
}
