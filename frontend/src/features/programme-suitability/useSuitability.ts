import { useCallback, useEffect, useMemo, useState } from 'react';
import type { LearnerKind } from '@/api/extendedIlr';
import { getAssessment, peekAssessment, prefetchAssessmentSecurity, primeAssessment } from './api';
import type { AssessmentState, SuitabilityCompletion } from './types';

export const ASSESSMENT_LABEL = 'Programme Suitability Assessment';
export const REQUIRED_MESSAGE = 'Please complete the Programme Suitability Assessment before continuing.';
export function completion(state: AssessmentState | null): SuitabilityCompletion['status'] {
  return state?.completionStatus ?? (state?.confirmedAt && (state.selectedProgramme || ['FURTHER_REVIEW_REQUIRED', 'INSUFFICIENT_INFORMATION'].includes(state.result?.outcome ?? '')) ? 'SUBMITTED' : state?.revision ? 'IN_PROGRESS' : 'NOT_STARTED');
}

export function selectionConfirmed(state: AssessmentState | null): boolean {
  return completion(state) === 'SUBMITTED' && state?.selectionStatus === 'SELECTION_CONFIRMED' && Boolean(state.selectionConfirmedAt);
}

export function useSuitability(kind: LearnerKind, learnerId: string, enabled: boolean, version?: number | null, legacyComplete = false) {
  const key = JSON.stringify([kind, learnerId, version]);
  const [snapshot, setSnapshot] = useState(() => ({ key, state: peekAssessment(kind, learnerId, version) ?? null }));
  const [loading, setLoading] = useState(enabled && !snapshot.state);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [continueToken, setContinueToken] = useState(0);
  const state = snapshot.key === key ? snapshot.state : null;
  const accept = useCallback((saved: AssessmentState) => {
    primeAssessment(kind, learnerId, version, saved);
    setSnapshot({ key, state: saved }); setError('');
  }, [kind, learnerId, version, key]);
  const refresh = useCallback(async () => {
    setLoading(true); setError('');
    try { const saved = await getAssessment(kind, learnerId, version, true); accept(saved); return saved; }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load the assessment. Please retry.'); return null; }
    finally { setLoading(false); }
  }, [kind, learnerId, version, accept]);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(!peekAssessment(kind, learnerId, version)); setError('');
    // Both requests are shared across StrictMode mounts and stay independent of CV saving.
    void prefetchAssessmentSecurity().catch(() => {});
    getAssessment(kind, learnerId, version).then((saved) => { if (!cancelled) accept(saved); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [enabled, kind, learnerId, version, accept]);
  return useMemo(() => ({ state, loading: enabled && (loading || (!state && !error)), error, open, continueToken, enabled,
    required: enabled && !legacyComplete && (state?.required ?? true), status: completion(state), selectionConfirmed: selectionConfirmed(state),
    refresh, accept, start: () => setOpen(true), close: () => setOpen(false),
    submitted: accept,
    continued: (saved: AssessmentState) => { accept(saved); if (selectionConfirmed(saved)) { setOpen(false); setContinueToken((n) => n + 1); } },
  }), [state, loading, error, open, continueToken, enabled, legacyComplete, refresh, accept]);
}

export type SuitabilityController = ReturnType<typeof useSuitability>;
