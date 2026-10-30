import { useCallback, useEffect, useState } from 'react';
import { fetchLearnerEventReviewInstance, fetchMigratedReviewForParty, type LearnerReviewDefinition } from '@/api/learnerCalendar';
import { prepareLearnerPersonalSupport } from '@/api/learnerPersonalSupport';
import type { LearnerKind } from '@/api/learnerDetail';


/**
 * The learner's half of the ONE Curriculum-driven Review form -- also reused
 * as-is for the Employer's half (see viewerRole below), since both are the
 * same "someone other than the coach reads/answers this Review" surface.
 *
 * Coach opens a Review instance through ReviewInstanceModal; the learner reads
 * the very same `review_instance_form_definition` here and hands it to the very
 * same ReviewFormRenderer. Neither side owns a per-Review-type form: sections,
 * fields, conditional fields, required rules and signatures all arrive from the
 * Curriculum template the occurrence was generated from.
 *
 * Read-only by default -- the Review is the coach's record of the meeting.
 * A field only becomes writable here when the Curriculum template's Form
 * Builder explicitly opts `viewerRole` into answering it (see
 * computeWritableFieldIds / reviews.FIELD_RESPONDENT_ROLES), and only once
 * the caller supplies `onSaveAnswers` -- omitted, the form stays exactly as
 * read-only as it always has.
 */

export interface LearnerReviewInstanceState {
  definition: LearnerReviewDefinition | null;
  loading: boolean;
  error: string;
  refresh: () => void;
}


/**
 * Loads a booked instance or an unscheduled occurrence's template preview.
 * Reads existing forms. An owned, unfinished Aptem PSP can explicitly opt in
 * to preparing its working copy automatically on open.
 */
export function useLearnerReviewInstance(
  kind: LearnerKind,
  learnerId: string,
  eventKey: string,
  preparePersonalSupport = false,
): LearnerReviewInstanceState {
  const [definition, setDefinition] = useState<LearnerReviewDefinition | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!eventKey) {
      setDefinition(null);
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setDefinition(null);
    setError('');
    (preparePersonalSupport
      ? prepareLearnerPersonalSupport(eventKey, controller.signal)
      : eventKey.startsWith('imported-review:')
      ? fetchMigratedReviewForParty(eventKey, controller.signal)
      : fetchLearnerEventReviewInstance(kind, learnerId, eventKey, controller.signal))
      .then((data) => {
        if (controller.signal.aborted) return;
        setDefinition('template' in data ? data : null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setDefinition(null);
        setError(reason instanceof Error ? reason.message : 'Could not load this review.');
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [kind, learnerId, eventKey, revision, preparePersonalSupport]);

  return { definition, loading, error, refresh };
}
