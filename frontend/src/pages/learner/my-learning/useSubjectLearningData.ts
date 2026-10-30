import { useEffect, useRef, useState } from 'react';
import type { LearnerDetail, LearnerKind } from '@/api/learnerDetail';
import { fetchStudentActivity, peekStudentActivity, subjectRequest, type StudentActivityResponse } from '@/api/studentActivity';
import { peekLearnerJson } from '@/api/learnerRead';
import { buildUnifiedLearningSummary, subjectRefs, type CoverMetadata } from './learningSummary';

export function useSubjectMetadata(data: StudentActivityResponse | null, real: LearnerDetail | null, kind?: string, learnerId?: string, enabled = true, assignedOnly = false) {
  // Raw, sorted IDs are independent of Builder renames and the merged cards.
  // The endpoint always resolves assigned Builder modules, titles and dates
  // from the learner ID. Their metadata can load alongside the plan/history;
  // historical covers already travel with the student-activity response.
  const refs = assignedOnly ? '' : subjectRefs(data, real);
  const key = `${kind}:${learnerId}:${refs}`;
  const url = `/learner_api/subject-covers/${encodeURIComponent(learnerId || '')}/?refs=${encodeURIComponent(refs)}`;
  const [state, setState] = useState<{ key: string; real: LearnerDetail | null; data: CoverMetadata | null; error: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const previousRequest = useRef<{ key: string; real: LearnerDetail | null } | null>(null);
  useEffect(() => {
    if (!enabled || !learnerId) return;
    const controller = new AbortController();
    // The assigned-only endpoint resolves assignments by learner ID. Arrival
    // of the first detail response does not change that resource. Subsequent
    // detail refreshes can change membership and must still revalidate it.
    const revalidate = retry > 0 || (previousRequest.current?.key === key
      && previousRequest.current.real !== real && (!assignedOnly || previousRequest.current.real !== null));
    previousRequest.current = { key, real };
    void subjectRequest<CoverMetadata>(`/learner_api/subject-covers/${encodeURIComponent(learnerId)}/?refs=${encodeURIComponent(refs)}`, { signal: controller.signal, revalidate })
      .then((result) => { if (!controller.signal.aborted) setState({ key, real, data: result, error: '' }); })
      .catch(() => { if (!controller.signal.aborted) setState({ key, real, data: null, error: 'Could not load your subject details. Please try again.' }); });
    return () => controller.abort();
  }, [enabled, learnerId, refs, key, retry, real, assignedOnly]);
  const current = state?.key === key && state.real === real ? state : null;
  return { metadata: current?.data ?? (enabled && learnerId ? peekLearnerJson<CoverMetadata>(url) : undefined), error: current?.error || '', retry: () => { setState(null); setRetry((value) => value + 1); } };
}

export function useUnifiedLearningSummary(
  real: LearnerDetail | null,
  kind?: LearnerKind,
  learnerId?: string,
  enabled = true,
) {
  const identity = `${kind}:${learnerId}`;
  const needsHistory = !!real?.studentActivityAvailable;
  const [state, setState] = useState<{
    identity: string;
    retry: number;
    data: StudentActivityResponse | null;
    error: string;
  } | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!enabled || !kind || !learnerId || !needsHistory) return;
    const controller = new AbortController();
    setState(null);
    void fetchStudentActivity(kind, learnerId, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ identity, retry, data, error: '' });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setState({
          identity,
          retry,
          data: null,
          error: failure instanceof Error ? failure.message : 'Could not load progress.',
        });
      });
    return () => controller.abort();
  }, [enabled, kind, learnerId, needsHistory, identity, retry]);

  const current = state?.identity === identity && state.retry === retry ? state : null;
  const data = needsHistory ? current?.data || (kind && learnerId ? peekStudentActivity(kind, learnerId) : null) || null : null;
  const metadataEnabled = enabled && !!learnerId && (!needsHistory || !!data);
  const { metadata, error: metadataError, retry: retryMetadata } = useSubjectMetadata(
    data,
    real,
    kind,
    learnerId,
    metadataEnabled,
  );
  const loading = enabled && !!learnerId && (
    (needsHistory && !current && !data) || (!current?.error && metadataEnabled && !metadata)
  );
  const summary = !enabled || loading || current?.error || metadataError
    ? null
    : buildUnifiedLearningSummary(data, real, metadata);
  return {
    data,
    metadata,
    summary,
    loading,
    error: current?.error || metadataError,
    retry: () => {
      setRetry((value) => value + 1);
      retryMetadata();
    },
  };
}
