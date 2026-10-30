import { useCallback, useEffect, useState } from 'react';
import { loadLearningReflectionSubmission } from '@/api/reflectionSubmission';

type Status = 'checking' | 'available' | 'missing' | 'error';

/** A removed brief must not hide owned work that was already saved. */
export function useSavedAssignmentAccess(kind: string | undefined, learnerId: string | undefined,
  componentId: string | undefined, enabled: boolean) {
  const key = JSON.stringify([kind, learnerId, componentId]);
  const [state, setState] = useState<{ key: string; status: Status } | null>(null);
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!enabled || !learnerId || !componentId || (kind !== 'commercial' && kind !== 'apprenticeship')) return;
    let active = true;
    setState({ key, status: 'checking' });
    loadLearningReflectionSubmission({ learnerKind: kind, learnerId, activityType: 'assignment', activityId: componentId })
      .then(submission => {
        if (active) setState({ key, status: submission?.id && submission.activityId === componentId ? 'available' : 'missing' });
      })
      .catch(() => { if (active) setState({ key, status: 'error' }); });
    return () => { active = false; };
  }, [kind, learnerId, componentId, key, enabled, revision]);

  return { status: enabled ? (state?.key === key ? state.status : 'checking') : 'missing', retry };
}
