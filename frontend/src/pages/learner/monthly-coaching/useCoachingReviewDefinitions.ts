import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchLearnerEventReviewInstance, fetchMigratedReviewForParty, type LearnerCalendarEvent, type LearnerReviewDefinition } from '@/api/learnerCalendar';
import type { LearnerKind } from '@/api/learnerDetail';
import type { CoachingReviewDefinitions } from './coachingOverview';

export interface CoachingReviewDefinitionsState {
  definitions: CoachingReviewDefinitions;
  loading: boolean;
  error: string;
  refresh: () => void;
}

const LOAD_ERROR = 'Some meeting signature details could not be loaded. Try again to check whether you need to sign.';

/** Calendar statuses cannot identify which participant still needs to sign. */
export function useCoachingReviewDefinitions(kind: LearnerKind, id: string, sessions: LearnerCalendarEvent[]): CoachingReviewDefinitionsState {
  const candidates = useMemo(() => sessions.filter(session => (!session.importedReview || session.migratedForm)
    && (session.migratedForm || session.reviewTemplateId || session.reviewInstanceId)
    && ['completed', 'awaiting-signature'].includes(session.status.trim().toLowerCase().replace(/[ _]+/g, '-'))), [sessions]);
  const identity = `${kind}:${id}`;
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const [state, setState] = useState<Omit<CoachingReviewDefinitionsState, 'refresh'> & { identity: string }>({
    identity: '', definitions: {}, loading: false, error: '',
  });
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    if (!candidates.length) { setState({ identity, definitions: {}, loading: false, error: '' }); return; }
    const keys = new Set(candidates.map(session => session.eventKey || session.id));
    setState(previous => ({
      identity,
      // Retain loaded summaries while retrying, but never across learners or
      // after an occurrence has left the set of submitted reviews.
      definitions: previous.identity === identity
        ? Object.fromEntries(Object.entries(previous.definitions).filter(([key]) => keys.has(key))) : {},
      loading: true, error: '',
    }));
    void Promise.all(candidates.map(async session => {
      const key = session.eventKey || session.id;
      try {
        const definition = session.migratedForm
          ? await fetchMigratedReviewForParty(session.formEventKey || key, controller.signal)
          : await fetchLearnerEventReviewInstance(kind, id, key, controller.signal);
        const hasSignatureDefinition = 'signatures' in definition;
        return {
          key,
          definition: hasSignatureDefinition ? definition as LearnerReviewDefinition : null,
          // A successful blank response is valid for a completed legacy
          // meeting: it predates Curriculum review forms, so there are no
          // canonical signatures to load. An awaiting-signature row still
          // needs resolving because the learner may have an action to take.
          unresolved: !hasSignatureDefinition
            && session.status.trim().toLowerCase().replace(/[ _]+/g, '-') === 'awaiting-signature',
        };
      } catch {
        return { key, definition: null, unresolved: true };
      }
    })).then(entries => {
      if (!active) return;
      setState(previous => ({
        identity,
        definitions: {
          ...(previous.identity === identity ? previous.definitions : {}),
          ...Object.fromEntries(entries.filter(entry => entry.definition).map(entry => [entry.key, entry.definition])),
        },
        loading: false,
        error: entries.some(entry => entry.unresolved) ? LOAD_ERROR : '',
      }));
    });
    return () => { active = false; controller.abort(); };
  }, [kind, id, identity, candidates, revision]);
  return state.identity === identity
    ? { definitions: state.definitions, loading: state.loading, error: state.error, refresh }
    : { definitions: {}, loading: candidates.length > 0, error: '', refresh };
}
