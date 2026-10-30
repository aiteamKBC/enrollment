import { coachFetch } from '@/lib/coachFetch';

export interface ScheduleEmailStatus {
  total: number;
  accepted: number;
  queued: number;
  failed: number;
  uncertain: number;
  status: 'complete' | 'pending';
}

/**
 * Submit one batch of schedule emails. With `changeNotice` -- the signed
 * before/after record an update or cancellation returned -- the batch is the
 * "was ... now ..." change email instead of the full creation schedule. The
 * token is the server's own; nothing else about recipients or dates is sent.
 */
export async function sendScheduleEmailBatch(liveSessionId: string, retryFailed = false, changeNotice = '', addedPeople?: string[], resendKey = '', addedKey = ''): Promise<ScheduleEmailStatus> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120000);
  try {
    const base = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';
    // Reuse the existing session + CSRF transport; this URL receives no coach view-as parameter.
    const response = await coachFetch(`${base}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/schedule-email/`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changeNotice ? { retryFailed, changeNotice }
        : addedPeople ? { retryFailed, addedPeople, ...(addedKey ? { addedKey } : {}) }
        : resendKey ? { retryFailed, resendKey }
        : { retryFailed }), signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Schedule emails could not be confirmed.');
    if (!['total', 'accepted', 'queued', 'failed', 'uncertain'].every(key => Number.isInteger(result[key]) && result[key] >= 0)
      || result.total !== result.accepted + result.queued + result.failed + result.uncertain) {
      throw new Error('Microsoft email submission status could not be read.');
    }
    return result;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('The calendar is saved, but email submission timed out. Retry to check pending messages safely.');
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

/**
 * Submit batches until nothing is queued, reporting progress after each one.
 * Stops with an error when a batch makes no progress, rather than looping.
 */
export async function submitChangeEmails(
  liveSessionId: string,
  changeNotice: string,
  { retryFailed = false, onProgress, addedPeople, resendKey, addedKey }: { retryFailed?: boolean; onProgress?: (status: ScheduleEmailStatus) => void; addedPeople?: string[]; resendKey?: string; addedKey?: string } = {},
): Promise<ScheduleEmailStatus> {
  let previousQueued: number | undefined;
  let retry = retryFailed;
  let email: ScheduleEmailStatus;
  do {
    email = await sendScheduleEmailBatch(liveSessionId, retry, changeNotice, addedPeople, resendKey, addedKey);
    retry = false;
    onProgress?.(email);
    if (email.queued > 0 && previousQueued !== undefined && email.queued >= previousQueued) {
      throw new Error('Some emails are still pending. Retry to continue without changing the calendar.');
    }
    previousQueued = email.queued;
  } while (email.queued > 0);
  return email;
}

/**
 * The full schedule email, for the people an update just added and nobody else.
 * The server keeps only addresses the saved calendar invites. `addedKey` is the
 * key the update sent them under, so this continues that batch: a person added
 * back after a removal is not skipped as already sent, and nobody in the batch
 * is emailed twice.
 */
export function submitAddedPeopleEmails(
  liveSessionId: string,
  addedPeople: string[],
  options: { retryFailed?: boolean; onProgress?: (status: ScheduleEmailStatus) => void; addedKey?: string } = {},
): Promise<ScheduleEmailStatus> {
  return submitChangeEmails(liveSessionId, '', { ...options, addedPeople });
}

/**
 * The creation email again, to everyone the saved calendar invites -- the
 * people it already reached included. `resendKey` names this one press for the
 * delivery ledger, so "accepted once, never again" still stops a duplicate
 * inside the press while no longer silencing the press itself. The same key is
 * repeated across the press's batches, which is what makes a retry safe.
 */
export function submitResendEmails(
  liveSessionId: string,
  resendKey: string,
  options: { retryFailed?: boolean; onProgress?: (status: ScheduleEmailStatus) => void } = {},
): Promise<ScheduleEmailStatus> {
  return submitChangeEmails(liveSessionId, '', { ...options, resendKey });
}

/** A ledger name for one resend press: unique, and carrying nothing about who is emailed. */
export function newResendKey(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ? random.replace(/[^A-Za-z0-9-]/g, '') : `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}
