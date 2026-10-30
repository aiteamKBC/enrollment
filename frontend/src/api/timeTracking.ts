import { learningFetch } from '@/lib/personalLearning';
import { synchronizeTrackingClock } from '@/lib/trackingClock';
export type TrackingActivityKind = 'quiz' | 'video' | 'component';
export type TrackingCountingMode = 'active_quiz' | 'active_playback' | 'visible_page';

export interface TimeTrackingSession {
  persistent?: boolean;
  savedSeconds?: number;
  rewardTracking?: boolean;
  trackingToken: string;
  startedAt: string;
  sessionId: string;
  countingMode: TrackingCountingMode;
}

export async function startTimeTracking(
  activityKind: TrackingActivityKind,
  activityId: string | number,
  kind: 'commercial' | 'apprenticeship',
  learnerId: string,
  countingMode: TrackingCountingMode,
): Promise<TimeTrackingSession> {
  const response = await learningFetch(
    `/learner_api/time-tracking/start/?kind=${kind}&learnerId=${encodeURIComponent(learnerId)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ activityKind, activityId: String(activityId), countingMode, persistent: true }),
    },
  );
  const text = await response.text();
  let data: (TimeTrackingSession & { error?: string }) | null = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`The server returned an unexpected response (${response.status}).`);
  }
  if (!response.ok || !data) {
    throw new Error(data?.error || `Could not start activity timing (${response.status}).`);
  }
  if (data.rewardTracking) synchronizeTrackingClock(data);
  return data;
}

const checkpoints = new Map<string, Promise<TimeTrackingSession>>();

/** Same signed identity and observation offsets; the server renews its lease. */
export async function checkpointTimeTracking(session: TimeTrackingSession, activityKind: TrackingActivityKind,
  activityId: string, kind: string, learnerId: string, elapsedSeconds: number): Promise<TimeTrackingSession> {
  // Serialize, but do not coalesce away newer seconds supplied by Finish.
  const previous = checkpoints.get(session.sessionId);
  const pending = (async () => {
    if (previous) await previous.catch(() => undefined);
    const response = await learningFetch(`/learner_api/time-tracking/checkpoint/?kind=${encodeURIComponent(kind)}&learnerId=${encodeURIComponent(learnerId)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ trackingToken: session.trackingToken, activityKind, activityId, elapsedSeconds }),
    });
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(data.error || 'Your time could not sync. Your draft is kept; retry when connected.'), { code: data.code });
    if (data.sessionId !== session.sessionId || data.trackingToken !== session.trackingToken
        || data.startedAt !== session.startedAt || data.countingMode !== session.countingMode
        || !Number.isSafeInteger(data.savedSeconds) || data.savedSeconds < 0
        || !Number.isSafeInteger(data.elapsedMilliseconds) || data.elapsedMilliseconds < 0 || data.persistent !== true) {
      throw new Error('The LMS did not confirm your saved time. Your draft is kept.');
    }
    const restored = { ...session, persistent: true, savedSeconds: data.savedSeconds };
    if (restored.rewardTracking) synchronizeTrackingClock(restored, data.elapsedMilliseconds);
    return restored;
  })();
  checkpoints.set(session.sessionId, pending);
  try { return await pending; }
  finally { if (checkpoints.get(session.sessionId) === pending) checkpoints.delete(session.sessionId); }
}

/** Re-anchor a restored signed session without recreating it or its observations. */
export async function resumeTimeTracking(session: TimeTrackingSession, activityKind: TrackingActivityKind, activityId: string) {
  if (!session.rewardTracking) return session;
  await synchronizeTimeTrackingClock(session, activityKind, activityId);
  return session;
}

/** Confirm the signed session's current server offset without replacing it. */
export async function synchronizeTimeTrackingClock(session: TimeTrackingSession, activityKind: TrackingActivityKind, activityId: string): Promise<number> {
  const response = await learningFetch('/learner_api/time-tracking/clock/', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify({ trackingToken: session.trackingToken, activityKind, activityId }),
  });
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error || 'Study timing could not synchronize. Your draft is kept.'), { code: data.code });
  if (data.sessionId !== session.sessionId || !Number.isSafeInteger(data.elapsedMilliseconds)) {
    throw new Error('The LMS did not confirm the timing session. Your draft is kept.');
  }
  synchronizeTrackingClock(session, data.elapsedMilliseconds);
  return data.elapsedMilliseconds;
}
