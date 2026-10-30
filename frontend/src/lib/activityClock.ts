/** Count elapsed study time independently of browser interval throttling.
 * The caller starts/stops media clocks on actual playing/waiting/pause events.
 */
export function startActivityClock({ countInBackground, pauseOnUnload = false, onElapsed, remainder, onInterval }: {
  onInterval?: (start:number,end:number)=>void;
  countInBackground: boolean;
  /** Reading stops as soon as refresh starts, before the old page is hidden. */
  pauseOnUnload?: boolean;
  onElapsed: (seconds: number) => void;
  remainder: { milliseconds: number };
}) {
  let last = performance.now();
  let visible = document.visibilityState === 'visible';
  let suspended = false;
  let unloading = false;
  let stopped = false;
  const flush = () => {
    const now = performance.now();
    if (!stopped && !suspended && (countInBackground || visible)) {
      onInterval?.(last,now);
      remainder.milliseconds += Math.max(0, now - last);
      const seconds = Math.floor(remainder.milliseconds / 1000);
      remainder.milliseconds -= seconds * 1000;
      if (seconds > 0) onElapsed(seconds);
    }
    last = now;
  };
  const visibility = () => { flush(); visible = document.visibilityState === 'visible'; };
  const hide = () => { flush(); unloading = false; suspended = true; };
  const show = () => { last = performance.now(); unloading = false; suspended = false; };
  const beforeUnload = () => { flush(); unloading = true; suspended = true; };
  // A cancelled/failed refresh leaves the old document open. Resume from now,
  // excluding the wait, without interfering with any browser confirmation.
  const navigation = pauseOnUnload ? (window as Window & { navigation?: EventTarget }).navigation : undefined;
  const navigationFailed = () => { if (unloading) show(); };
  // Some browsers do not signal a cancelled toolbar reload. Interacting with
  // the still-visible reading is also a safe point to resume from zero wait.
  const resumeReading = () => { if (unloading && document.visibilityState === 'visible') show(); };
  const interval = window.setInterval(flush, 1000);
  document.addEventListener('visibilitychange', visibility);
  window.addEventListener('pagehide', hide);
  window.addEventListener('pageshow', show);
  if (pauseOnUnload) {
    window.addEventListener('beforeunload', beforeUnload);
    document.addEventListener('pointerdown', resumeReading);
    document.addEventListener('keydown', resumeReading);
  }
  navigation?.addEventListener('navigateerror', navigationFailed);
  return {
    stop: () => {
      flush();
      stopped = true;
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', hide);
      window.removeEventListener('pageshow', show);
      if (pauseOnUnload) {
        window.removeEventListener('beforeunload', beforeUnload);
        document.removeEventListener('pointerdown', resumeReading);
        document.removeEventListener('keydown', resumeReading);
      }
      navigation?.removeEventListener('navigateerror', navigationFailed);
    },
  };
}
