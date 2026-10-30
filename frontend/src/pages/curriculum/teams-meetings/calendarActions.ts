import { coachFetch } from '@/lib/coachFetch';
import { clearCurriculumGetCache } from '@/lib/curriculumApi';

export interface ActionSession {
  sessionNumber: number; startDateTimeUtc: string; endDateTimeUtc: string; joinUrl: string;
  newStartDateTimeUtc?: string; newEndDateTimeUtc?: string;
  calendarVerified?: boolean;
}
export interface ActionReview {
  reviewToken: string; title: string; organizer: string; timeZone: string; action: 'cancel' | 'reschedule';
  scope: 'series' | 'occurrence' | 'leftover'; notificationRequired: boolean; calendarRequests: number; sessions: ActionSession[];
  warnings?: string[];
}
export interface ActionResult {
  status: 'done' | 'failed' | 'uncertain' | 'processing' | 'incomplete' | 'none'; completed?: number; total?: number; message: string;
  /** Once every change is confirmed: the server-signed before/after record the optional email is sent from. */
  changeNotice?: string;
  /** Cancellation emails are dispatched server-side; errors retain the signed notice for retry. */
  scheduleEmail?: { total?: number; accepted?: number; queued?: number; failed?: number; uncertain?: number; status?: 'complete' | 'pending'; error?: string; code?: string };
}

export async function calendarAction<T extends ActionReview | ActionResult>(liveId: string, body: Record<string, unknown>): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120000);
  try {
    const base = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
    const response = await coachFetch(`${base}/curriculum/teams-meetings/${encodeURIComponent(liveId)}/actions/`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The calendar action could not be completed.');
    const valid = body.stage === 'review'
      ? result && typeof result.reviewToken === 'string' && result.reviewToken.length > 0
        && typeof result.timeZone === 'string' && typeof result.notificationRequired === 'boolean'
        && ['cancel', 'reschedule'].includes(result.action) && ['series', 'occurrence', 'leftover'].includes(result.scope)
        && Number.isInteger(result.calendarRequests) && result.calendarRequests > 0
        && Array.isArray(result.sessions) && result.sessions.length > 0
        // A slot outside the plan has no session number, and a whole weekday
        // series has no single date; every other review names real sessions.
        && result.sessions.every((session: ActionSession) => session && Number.isInteger(session.sessionNumber)
          && (result.scope === 'leftover' || (Number.isFinite(Date.parse(session.startDateTimeUtc)) && Number.isFinite(Date.parse(session.endDateTimeUtc)))))
        && (result.scope !== 'leftover' || (result.action === 'cancel' && result.calendarRequests === 1))
      : result && ['done', 'failed', 'uncertain', 'processing', 'incomplete', 'none'].includes(result.status)
        && typeof result.message === 'string';
    if (!valid) throw new Error('The calendar response could not be verified. Check action status before confirming again.');
    if (body.stage !== 'review') clearCurriculumGetCache();
    return result;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('Confirmation timed out. Use Check action status before sending another request.');
    }
    throw error;
  } finally { window.clearTimeout(timeout); }
}
