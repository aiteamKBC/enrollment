import { useEffect, useState } from 'react';
import { readLearnerJson } from './learnerRead';
export interface ExtraActivity {
  activityId: string; title: string; status: string; submittedAt: string | null; month: string;
  answer: string; reflection: string; ksbs: string[]; hours: string;
  coachFeedback: string | null; reviewedBy: string | null; reviewedAt: string | null;
}
export function useExtraActivities(kind: string | undefined, learnerId: string | undefined, month?: string, enabled = true) {
  const [activities, setActivities] = useState<ExtraActivity[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [loadedFor, setLoadedFor] = useState('');
  const identity = `${kind}:${learnerId}:${month || ''}`;
  useEffect(() => {
    if (!enabled || !kind || !learnerId) return;
    const controller = new AbortController();
    setActivities([]); setLoading(true); setError('');
    const query = new URLSearchParams({ learnerKind: kind, learnerId, ...(month ? { month } : {}) });
    void readLearnerJson<{ activities: ExtraActivity[] }>(`/learner_api/reflection/extra-activities/?${query}`, { signal: controller.signal, revalidate: true })
      .then(data => { if (!Array.isArray(data.activities)) throw new Error('Invalid activities response'); if (!controller.signal.aborted) setActivities(data.activities); })
      .catch(() => { if (!controller.signal.aborted) setError('Could not load extra activities. Please retry.'); })
      .finally(() => { if (!controller.signal.aborted) { setLoadedFor(identity); setLoading(false); } });
    return () => controller.abort();
  }, [kind, learnerId, month, enabled, version, identity]);
  return { activities: loadedFor === identity ? activities : [], loading: enabled && (loading || loadedFor !== identity), error: loadedFor === identity ? error : '', refresh: () => setVersion(value => value + 1) };
}
