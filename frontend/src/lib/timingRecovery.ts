import type { TimeTrackingSession } from '@/api/timeTracking';

export function timingSessionExpired(error: unknown): boolean {
  const code = error && typeof error === 'object' && 'code' in error ? error.code : '';
  return code === 'expired_tracking_session' || code === 'tracking_session_expired'
    || (error instanceof Error && (error.message.includes('The activity timing session has expired')
      || error.message.includes('tracking session expired')));
}

/** Keep old evidence separate: never relabel old seconds with a fresh signature. */
export function archiveTimingDraft(key: string, session: TimeTrackingSession | null, elapsedSeconds: number, answers?: unknown): void {
  if (!key || !session) throw new Error('The old timing draft could not be identified. Please keep this page open.');
  const archiveKey = `${key}:expired:${session.sessionId}`;
  try {
    localStorage.setItem(archiveKey, JSON.stringify({ session, elapsedSeconds, answers, archivedAt: new Date().toISOString() }));
  } catch {
    throw new Error('Your old timing draft could not be preserved. Free some browser storage and retry; your current work is still open.');
  }
}
