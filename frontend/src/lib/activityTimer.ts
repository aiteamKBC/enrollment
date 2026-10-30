import type { TimeTrackingSession, TrackingCountingMode } from '@/api/timeTracking';

const STORAGE_PREFIX = 'learner_activity_timer:v1:';
// Server tracking tokens expire after 24 hours. Leave a small margin so the
// page starts a clean session instead of restoring a token about to expire.
const SESSION_MAX_AGE_MS = (24 * 60 * 60 - 60) * 1000;

export interface PersistedActivityTimer {
  elapsedSeconds: number;
  session: TimeTrackingSession | null;
}

export function activityTimerStorageKey(
  kind: string | undefined,
  learnerId: string | undefined,
  activityId: string | undefined,
): string {
  if (!kind || !learnerId || !activityId) return '';
  return `${STORAGE_PREFIX}${encodeURIComponent(kind)}:${encodeURIComponent(learnerId)}:${encodeURIComponent(activityId)}`;
}

function validSession(value: unknown): value is TimeTrackingSession {
  if (!value || typeof value !== 'object') return false;
  const session = value as Partial<TimeTrackingSession>;
  return typeof session.trackingToken === 'string'
    && Boolean(session.trackingToken)
    && typeof session.startedAt === 'string'
    && Boolean(session.startedAt)
    && typeof session.sessionId === 'string'
    && Boolean(session.sessionId)
    && ['active_quiz', 'active_playback', 'visible_page'].includes(String(session.countingMode));
}

export function readActivityTimer(key: string): PersistedActivityTimer | null {
  if (!key) return null;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { elapsedSeconds?: unknown; session?: unknown };
    const elapsedSeconds = Number(parsed?.elapsedSeconds);
    if (!Number.isFinite(elapsedSeconds) || elapsedSeconds < 0) return null;
    return {
      elapsedSeconds: Math.floor(elapsedSeconds),
      session: validSession(parsed.session) ? parsed.session : null,
    };
  } catch {
    return null;
  }
}

function writeActivityTimer(key: string, timer: PersistedActivityTimer): void {
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(timer));
  } catch {
    // Storage can be unavailable in a private/restricted browser. The live
    // counter continues to work for the current page in that case.
  }
}

export function saveActivityTimerElapsed(key: string, elapsedSeconds: number): void {
  if (!key || !Number.isFinite(elapsedSeconds)) return;
  const current = readActivityTimer(key);
  writeActivityTimer(key, {
    elapsedSeconds: Math.max(0, Math.floor(elapsedSeconds)),
    session: current?.session ?? null,
  });
}

export function saveActivityTimerSession(key: string, session: TimeTrackingSession): void {
  if (!key || !validSession(session)) return;
  const current = readActivityTimer(key);
  writeActivityTimer(key, {
    elapsedSeconds: current?.elapsedSeconds ?? 0,
    session,
  });
}

export function canResumeActivityTimer(
  timer: PersistedActivityTimer | null,
  countingMode: TrackingCountingMode,
  now = Date.now(),
): boolean {
  if (!timer?.session || timer.session.countingMode !== countingMode) return false;
  const startedAt = Date.parse(timer.session.startedAt);
  if (!Number.isFinite(startedAt)) return false;
  // Reward sessions are revalidated/re-anchored by the server on restore.
  // A device's wall clock cannot decide the lifetime of a signed server token.
  if (timer.session.rewardTracking || timer.session.persistent) return true;
  const age = now - startedAt;
  return age >= 0 && age < SESSION_MAX_AGE_MS;
}

export function clearActivityTimer(key: string): void {
  if (!key) return;
  try {
    localStorage.removeItem(key);
  } catch {
    // Nothing else to clear when storage is unavailable.
  }
}
