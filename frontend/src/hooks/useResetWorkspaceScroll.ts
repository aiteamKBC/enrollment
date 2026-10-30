import { useLayoutEffect, useRef } from 'react';

/**
 * Scroll the workspace back to the top whenever `key` changes.
 *
 * WorkspaceShell scrolls inside its own `main.workspace-main`, not the window,
 * and it stays mounted while only a route parameter changes. A page that moves
 * between views by URL — the enrolment wizard's steps, say — therefore keeps the
 * previous view's scroll position unless it resets it: Next at the foot of a
 * long step would land at the foot of the next one.
 *
 * Attach the returned ref to any element inside the shell. A layout effect, so
 * the new view is never painted at the old position.
 */
export function useResetWorkspaceScroll<T extends HTMLElement>(key: unknown) {
  const ref = useRef<T>(null);
  useLayoutEffect(() => {
    const scroller = ref.current?.closest('.workspace-main');
    if (scroller) scroller.scrollTop = 0;
    else window.scrollTo?.(0, 0);
  }, [key]);
  return ref;
}
