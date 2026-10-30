import type { TimeTrackingSession } from '@/api/timeTracking';

const anchors = new Map<string, { at: number; elapsed: number }>();

/** Anchor at response receipt: network transit is never added as study time. */
export function synchronizeTrackingClock(session: TimeTrackingSession, elapsed = 0) {
  if (!Number.isFinite(elapsed) || elapsed < 0) throw new Error('Invalid timing clock response.');
  anchors.set(session.sessionId, { at: performance.now(), elapsed });
}

export function trackingOffsets(session: TimeTrackingSession, start: number, end: number) {
  const anchor = anchors.get(session.sessionId);
  if (!anchor) throw new Error('Study timing needs to synchronize with the LMS. Keep your draft and retry.');
  return {
    start: Math.max(0, Math.floor(anchor.elapsed + start - anchor.at)),
    end: Math.floor(anchor.elapsed + end - anchor.at),
  };
}

export function clearTrackingClock(session: TimeTrackingSession) {
  anchors.delete(session.sessionId);
}
