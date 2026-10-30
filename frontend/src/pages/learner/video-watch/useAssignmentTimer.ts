import { useEffect, useRef, useState } from 'react';

export function useAssignmentTimer(key: string) {
  const [elapsed, setElapsed] = useState(0);
  const [running, setRunning] = useState(false);
  const seconds = useRef(0);
  const tickAt = useRef<number | null>(null);
  const latestKey = useRef(key);
  latestKey.current = key;
  const persist = () => {
    try { localStorage.setItem(latestKey.current, String(seconds.current)); } catch { /* The server draft also carries elapsed time. */ }
  };
  const flush = () => {
    if (tickAt.current !== null) {
      const now = Date.now();
      const delta = Math.max(0, Math.floor((now - tickAt.current) / 1000));
      seconds.current += delta;
      tickAt.current += delta * 1000;
      setElapsed(seconds.current);
      persist();
    }
    return seconds.current;
  };
  const pause = () => { flush(); tickAt.current = null; setRunning(false); };
  const resume = () => { if (tickAt.current === null) tickAt.current = Date.now(); setRunning(true); };
  const restore = (serverSeconds: number, start: boolean) => {
    let local = 0;
    try { local = Number(localStorage.getItem(key)) || 0; } catch { /* Use server draft. */ }
    flush();
    seconds.current = Math.max(seconds.current, 0, Math.floor(Number(serverSeconds) || 0), Number.isFinite(local) ? local : 0);
    setElapsed(seconds.current); tickAt.current = start ? Date.now() : null; setRunning(start);
  };
  const reset = () => { seconds.current = 0; setElapsed(0); tickAt.current = null; setRunning(false); persist(); };
  useEffect(() => {
    const interval = window.setInterval(flush, 1000);
    const hide = () => { flush(); tickAt.current = null; setRunning(false); };
    window.addEventListener('pagehide', hide);
    return () => { flush(); window.clearInterval(interval); window.removeEventListener('pagehide', hide); };
    // Recreate at assignment identity boundaries; background tabs keep counting by elapsed instants.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { elapsed, running, pause, resume, restore, reset, flush };
}
