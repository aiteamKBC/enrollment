import { useCallback, useEffect, useRef, useState } from 'react';
import {
  previewTutorAvailabilityRoster,
  type CurriculumTutorAvailabilityInput,
  type CurriculumTutorAvailabilityVerdict,
} from '@/lib/curriculumApi';

/**
 * Who is already teaching in the slot the user is filling in.
 *
 * A tutor cannot take two modules that run at the same time, and the save
 * refuses one that would. Finding that out *from* the refusal is the worst
 * moment to find it out — the form is already filled in and the choice was never
 * going to work. This asks the same question ahead of the write so a screen can
 * mark the busy names in the picker and explain the clash while it is still one
 * click to avoid.
 *
 * The whole roster comes back in one request rather than one call per tutor:
 * the answer changes with the *slot*, not with the name, so re-asking per name
 * would be the same query many times over.
 *
 * Verdicts are advisory. The backend is still the authority — a slot can be
 * taken between the preview and the save — so callers must keep handling the
 * 409 (see `tutorConflictMessage`) rather than trusting this to have caught it.
 */
export interface TutorAvailability {
  /** Verdict by tutor key (see `tutorAvailabilityKey`). Empty until the first load. */
  byTutor: Map<string, CurriculumTutorAvailabilityVerdict>;
  /** The dates the slot occupies, for explaining a clash without a second call. */
  sessionDates: string[];
  /** False when the slot books nothing — no verdict here means anything yet. */
  bookable: boolean;
  loading: boolean;
  /**
   * The check could not be made. Nobody's availability is known, so a screen
   * must say so rather than read the silence as "everyone is free".
   */
  failed: boolean;
  /** Look one tutor up. Returns null while loading, unknown, or not bookable. */
  verdictFor: (tutor: string) => CurriculumTutorAvailabilityVerdict | null;
}

/**
 * The key the save matches a tutor on (`staff_assignment_key` in the backend):
 * lower-cased, letters and digits only. Looking names up by anything looser
 * would let a spelling the save treats as the same person read as unchecked.
 */
export function tutorAvailabilityKey(name: string): string {
  return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

/** Matches the module form's session-plan preview, so the two land together. */
const DEBOUNCE_MS = 300;

const EMPTY: TutorAvailability = {
  byTutor: new Map(),
  sessionDates: [],
  bookable: false,
  loading: false,
  failed: false,
  verdictFor: () => null,
};

export function useTutorAvailability(
  slot: CurriculumTutorAvailabilityInput | null,
  options: { enabled?: boolean } = {},
): TutorAvailability {
  const enabled = options.enabled !== false && Boolean(slot);
  const [state, setState] = useState<Omit<TutorAvailability, 'verdictFor'>>(EMPTY);

  // The slot is rebuilt on every render by most callers, so the effect keys off
  // its contents rather than its identity — otherwise every keystroke in an
  // unrelated field would refire the request.
  const key = enabled && slot
    ? JSON.stringify([
      slot.startDate,
      slot.sessionsNumber,
      slot.weekDays,
      slot.weeklySchedule,
      slot.startTime,
      slot.endTime,
      slot.moduleCatalogueId,
      // Part of the slot, not decoration: the cohort's ticked holidays move the
      // session dates, so a changed cohort (or a changed tick) is a changed
      // question. Left out, the picker kept answering about the old dates.
      slot.cohortId,
      (slot.holidays || []).map(holiday => `${holiday.id}:${holiday.startDate}:${holiday.endDate}`),
    ])
    : '';
  const slotRef = useRef(slot);
  slotRef.current = slot;

  useEffect(() => {
    if (!key) {
      setState(EMPTY);
      return undefined;
    }
    let live = true;
    setState(previous => ({ ...previous, loading: true }));
    // Debounced, because the slot moves with a number field: typing "18" into a
    // weeks box passes through 1 and then 18, and only the answer for 18 is worth
    // asking for.
    const timer = setTimeout(() => {
      previewTutorAvailabilityRoster(slotRef.current || {})
        .then(response => {
          if (!live) return;
          const byTutor = new Map<string, CurriculumTutorAvailabilityVerdict>();
          response.results.forEach(item => {
            const name = tutorAvailabilityKey(item.tutor);
            // The first spelling wins; the backend gives every spelling of one
            // key the same verdict anyway.
            if (name && !byTutor.has(name)) byTutor.set(name, item);
          });
          setState({
            byTutor,
            sessionDates: response.sessionDates,
            bookable: response.bookable,
            loading: false,
            failed: false,
          });
        })
        .catch(() => {
          // Advisory only: a failed preview must never block the form -- the save
          // still enforces the rule. But it is reported as failed, not as an
          // empty answer, so no name is shown as free on the strength of it.
          if (live) setState({ ...EMPTY, loading: false, failed: true });
        });
    }, DEBOUNCE_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [key]);

  // Stable while the answers are: callers memoise a whole annotated picker off
  // this, and a fresh function every render would rebuild it on every keystroke
  // in an unrelated field.
  const verdictFor = useCallback((tutor: string) => {
    const name = tutorAvailabilityKey(tutor);
    if (!name || !state.bookable) return null;
    return state.byTutor.get(name) || null;
  }, [state.bookable, state.byTutor]);

  return { ...state, verdictFor };
}
