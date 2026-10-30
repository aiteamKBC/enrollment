import { useCallback, useEffect, useRef, useState } from 'react';
import { saveLearningReflectionSubmission, type LearningReflectionSubmissionInput } from '@/api/reflectionSubmission';

// Keep ordering when a learner closes and reopens the same activity while a
// write is still in flight. Completed queues are removed to avoid retaining data.
const writes = new Map<string, Promise<unknown>>();

/** Serialize draft and submit writes so a slow draft cannot replace a submission. */
export function useReflectionAutosave(payload: LearningReflectionSubmissionInput | null, enabled: boolean) {
  const serialized = payload ? JSON.stringify(payload) : '';
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const paused = useRef(false);
  const mounted = useRef(true);
  const [pending, setPending] = useState(0);
  const [savedSnapshot, setSavedSnapshot] = useState('');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const pause = useCallback(() => {
    paused.current = true;
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const resume = useCallback(() => {
    paused.current = false;
    setRetry(value => value + 1);
  }, []);

  const save = useCallback(async (snapshot: LearningReflectionSubmissionInput) => {
    if (timer.current) clearTimeout(timer.current);
    const text = JSON.stringify(snapshot);
    setPending(count => count + 1);
    setError('');
    // A failed earlier write must not block a later retry.
    const key = JSON.stringify([snapshot.learnerKind, snapshot.learnerId, snapshot.activityType, snapshot.activityId]);
    const previous = writes.get(key) || Promise.resolve();
    const request = previous.catch(() => undefined).then(() => saveLearningReflectionSubmission(snapshot));
    writes.set(key, request);
    try {
      const result = await request;
      if (mounted.current) setSavedSnapshot(text);
      return result;
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : 'Could not save your reflection. Please retry.');
      throw cause;
    } finally {
      if (writes.get(key) === request) writes.delete(key);
      if (mounted.current) setPending(count => count - 1);
    }
  }, []);

  useEffect(() => {
    if (!enabled || !serialized || paused.current) return;
    timer.current = setTimeout(() => {
      if (!paused.current) {
        // save() displays the failure; the local recovery copy remains intact.
        void save(JSON.parse(serialized) as LearningReflectionSubmissionInput).catch(() => undefined);
      }
    }, 900);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [enabled, serialized, save, retry]);

  useEffect(() => {
    if (!enabled) return;
    window.addEventListener('online', resume);
    return () => window.removeEventListener('online', resume);
  }, [enabled, resume]);

  return { save, pause, resume, saving: pending > 0, saved: Boolean(serialized && serialized === savedSnapshot), error };
}
