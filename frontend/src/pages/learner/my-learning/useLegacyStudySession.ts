import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { TimeTrackingSession } from '@/api/timeTracking';

type Answers = Record<string, string[]>;
type Draft = { key: string; attemptId: string; timing: TimeTrackingSession; elapsedSeconds: number;
  answers: Answers; confirmed: boolean };

function restore(key: string): Draft | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null') as Draft | null;
    return value?.key === key && typeof value.attemptId === 'string' && value.timing?.trackingToken
      && typeof value.timing.startedAt === 'string' && Number.isFinite(value.elapsedSeconds) && value.elapsedSeconds >= 0
      && value.answers && typeof value.answers === 'object' ? value : null;
  } catch { return null; }
}

/** One draft per learner/course/type/activity. Never count hidden or paused time. */
export function useLegacyStudySession(key: string, enabled: boolean, suspended: boolean, element?: RefObject<HTMLElement | null>) {
  const [draft, setDraft] = useState<Draft | null>(() => restore(key));
  const [paused, setPaused] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [storageError, setStorageError] = useState('');
  const current = draft?.key === key ? draft : null;
  const hasDraft = !!current;
  const currentRef = useRef(current);
  currentRef.current = current;
  const visibleRef = useRef(document.visibilityState === 'visible');
  useEffect(() => { setDraft(restore(key)); setPaused(false); setPlaying(false); setStorageError(''); }, [key]);
  const persist = useCallback((value: Draft | null) => {
    try {
      if (value) localStorage.setItem(value.key, JSON.stringify(value));
      else localStorage.removeItem(key);
      setStorageError('');
    } catch { setStorageError('Your timing draft could not be saved on this device. Keep this page open until you submit.'); }
  }, [key]);
  useEffect(() => { if (current) persist(current); }, [current, persist]);
  const running = !!current && enabled && !suspended && !paused
    && (current.timing.countingMode !== 'active_playback' || playing);
  useEffect(() => {
    let last = performance.now();
    const visibility = () => { visibleRef.current = document.visibilityState === 'visible'; last = performance.now(); };
    document.addEventListener('visibilitychange', visibility);
    if (!running) return () => document.removeEventListener('visibilitychange', visibility);
    const timer = window.setInterval(() => {
      const now = performance.now();
      // A background-suspended tab must not turn its long callback delay into
      // study time. Sub-second deltas are retained until final submission.
      const delta = Math.max(0, Math.min(1, (now - last) / 1000));
      last = now;
      // Workspaces keep previously opened activities mounted behind hidden
      // panels. Only the currently displayed activity can accrue time.
      let displayed = true;
      for (let node = element?.current; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (node.hidden || style.display === 'none' || style.visibility === 'hidden') { displayed = false; break; }
      }
      if (running && visibleRef.current && displayed) setDraft(value => value?.key === key
        ? { ...value, elapsedSeconds: value.elapsedSeconds + delta } : value);
    }, 1000);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [key, running, element]);
  useEffect(() => {
    if (!hasDraft || !enabled) return;
    const save = () => { if (currentRef.current) persist(currentRef.current); };
    const warn = (event: BeforeUnloadEvent) => { save(); event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('pagehide', save); window.addEventListener('beforeunload', warn);
    return () => { window.removeEventListener('pagehide', save); window.removeEventListener('beforeunload', warn); };
  }, [hasDraft, enabled, persist]);
  const begin = (attemptId: string, timing: TimeTrackingSession) => {
    if (!timing?.trackingToken || !timing.startedAt) throw new Error('The server did not start study timing. Please retry.');
    const next = { key, attemptId, timing, elapsedSeconds: 0, answers: {}, confirmed: false };
    setDraft(next); persist(next); setPaused(false);
  };
  const clear = () => { setDraft(null); persist(null); setPlaying(false); };
  const setAnswers = (update: (previous: Answers) => Answers) => setDraft(value => value?.key === key
    ? { ...value, answers: update(value.answers) } : value);
  const setConfirmed = (confirmed: boolean) => setDraft(value => value?.key === key ? { ...value, confirmed } : value);
  return { draft: current, begin, clear, setAnswers, setConfirmed, paused, setPaused, setPlaying, running, storageError };
}
