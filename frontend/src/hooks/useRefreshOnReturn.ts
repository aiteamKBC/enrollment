import { useEffect, useRef } from 'react';
import { subscribeCurriculumRemoteWrites, UNKNOWN_WRITE_PATH } from '@/lib/curriculumApi';

/**
 * Re-reads when the reader comes back to the tab, and when another tab writes.
 *
 * A page reads once, in a mount effect. Leave that tab open while the record is
 * edited somewhere else -- the next tab along, a colleague on another machine --
 * and it keeps showing what was true when it mounted. Pressing refresh was the
 * only way to find out, which is not a thing a reader should have to think to do.
 *
 * Two triggers, because they cover different cases. Coming back to the tab is
 * the moment the reader expects to be looking at the truth, and it works no
 * matter who wrote or from where. A cross-tab write is the same browser only,
 * but it lands while both tabs are on screen, which is the case that reads as
 * broken.
 *
 * The refresh is expected to be silent and to skip the client cache: the rows
 * stay on screen with the previous data rather than collapsing to skeletons
 * every time the reader alt-tabs. Generalised from the pattern proven on the
 * programme list and the question bank.
 */

/** Focus/visibility bursts arrive in pairs; one read is enough. */
const RETURN_THROTTLE_MS = 1_000;
/** One user action can be several writes (a tree save is many calls). */
const REMOTE_WRITE_DEBOUNCE_MS = 300;

interface RefreshOptions {
  /** Off while the page has nothing to refresh yet (no id, not authorised). */
  enabled?: boolean;
}

interface RemoteWriteOptions extends RefreshOptions {
  /**
   * Narrows which written paths this page cares about. Defaults to all.
   *
   * Never consulted for a write that reached us through the epoch poll: that
   * one carries no path, so there is nothing to match on and the only safe
   * reading of it is "something changed".
   */
  match?: (path: string) => boolean;
}

function documentHidden(): boolean {
  return typeof document !== 'undefined'
    && typeof document.visibilityState === 'string'
    && document.visibilityState !== 'visible';
}

/**
 * Read through a ref so the listeners bind once. `refresh` is a fresh closure on
 * every render, and re-subscribing on each one would swap the handlers under the
 * events they are meant to catch.
 */
function useLatest<T>(value: T) {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}

/** Re-reads when the tab regains focus, becomes visible, or comes back online. */
export function useRefreshOnReturn(refresh: () => void, options: RefreshOptions = {}): void {
  const { enabled = true } = options;
  const refreshRef = useLatest(refresh);

  useEffect(() => {
    if (!enabled) return;
    let lastRun = 0;
    const onReturn = () => {
      if (documentHidden()) return;
      const now = Date.now();
      if (now - lastRun < RETURN_THROTTLE_MS) return;
      lastRun = now;
      refreshRef.current();
    };
    window.addEventListener('focus', onReturn);
    window.addEventListener('online', onReturn);
    document.addEventListener('visibilitychange', onReturn);
    return () => {
      window.removeEventListener('focus', onReturn);
      window.removeEventListener('online', onReturn);
      document.removeEventListener('visibilitychange', onReturn);
    };
  }, [enabled, refreshRef]);
}

/**
 * Re-reads when somebody else writes curriculum data -- another tab of this
 * browser, or, through the epoch poll, another person on another machine. The
 * writing tab does not hear its own write: it already refreshes on the save
 * path, and firing here too would double every post-save read.
 */
export function useRefreshOnRemoteWrite(refresh: () => void, options: RemoteWriteOptions = {}): void {
  const { enabled = true, match } = options;
  const refreshRef = useLatest(refresh);
  const matchRef = useLatest(match);

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = subscribeCurriculumRemoteWrites(path => {
      if (path !== UNKNOWN_WRITE_PATH && matchRef.current && !matchRef.current(path)) return;
      // A hidden tab re-reads when it is looked at again, not while it sits in
      // the background -- the return listener above already covers that, and
      // reading now would spend a rebuild nobody is waiting for.
      if (documentHidden()) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        refreshRef.current();
      }, REMOTE_WRITE_DEBOUNCE_MS);
    });
    return () => {
      if (timer) clearTimeout(timer);
      unsubscribe();
    };
  }, [enabled, matchRef, refreshRef]);
}

/** Both triggers. What a curriculum page that reads shared records wants. */
export function useLiveRefresh(refresh: () => void, options: RemoteWriteOptions = {}): void {
  useRefreshOnReturn(refresh, options);
  useRefreshOnRemoteWrite(refresh, options);
}
