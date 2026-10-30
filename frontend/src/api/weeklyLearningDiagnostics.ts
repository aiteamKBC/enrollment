/** Temporary, opt-in development tracing. No payloads, identities or credentials. */
type WeeklyTraceEvent = {
  event: string; key: string; at: number; elapsed: number; detail?: string; stack?: string;
};
declare global {
  interface Window { __weeklyLearningTrace?: WeeklyTraceEvent[] }
}
const keys = new Map<string, string>();

export function traceWeeklyLearning(event: string, identity: string, detail?: string): void {
  if (!import.meta.env.DEV || typeof window === 'undefined') return;
  try {
    if (sessionStorage.getItem('lms:weekly-trace') !== '1') return;
  } catch { return; } // Diagnostics must never affect the request.
  let key = keys.get(identity);
  if (!key) { key = `key-${keys.size + 1}`; keys.set(identity, key); }
  const events = window.__weeklyLearningTrace ??= [];
  events.push({ event, key, at: Date.now(), elapsed: performance.now(), detail,
    stack: event === 'read' || event === 'invalidate' ? new Error().stack : undefined });
  if (events.length > 200) events.shift();
}
