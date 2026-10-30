import { useEffect, useRef, useState } from 'react';
import { useWizard } from "../WizardContext.helpers";
import { fetchIlrEmployerDetails, type IlrEmployerDetails, type LearnerKind } from '@/api/extendedIlr';

const blank = (v?: string | null) => !v || v.trim() === '';

/**
 * Fill the Extended ILR's empty Employer Details from the learner's assigned
 * employer and its organisation.
 *
 * Only blank fields are filled, and only once the saved answers have loaded —
 * so nothing the learner typed or saved is overwritten, and a saved answer
 * arriving late is never clobbered by the prefill. The filled values go
 * through setSection, so they are unsaved edits written on the next save, and
 * stay editable like any other answer.
 */
export function useEmployerDetailsPrefill() {
  const { userId, isCommercial, draft, setSection, hydrated } = useWizard();
  const kind: LearnerKind = isCommercial ? 'commercial' : 'apprenticeship';
  const [details, setDetails] = useState<IlrEmployerDetails | null>(null);
  const applied = useRef(false);

  useEffect(() => {
    let cancelled = false;
    applied.current = false;
    setDetails(null);
    fetchIlrEmployerDetails(kind, userId)
      .then((res) => { if (!cancelled) setDetails(res); })
      // A prefill is a convenience: without it the learner simply types the details.
      .catch(() => {});
    return () => { cancelled = true; };
  }, [kind, userId]);

  useEffect(() => {
    if (!details || !hydrated || applied.current) return;
    applied.current = true;
    const current = draft.ilr.employer;
    const fill: Partial<IlrEmployerDetails> = {};
    (Object.keys(current) as (keyof IlrEmployerDetails)[]).forEach((key) => {
      if (blank(current[key]) && !blank(details[key])) fill[key] = details[key].trim();
    });
    if (Object.keys(fill).length === 0) return;
    setSection('ilr', { ...draft.ilr, employer: { ...current, ...fill } });
  }, [details, hydrated, draft.ilr, setSection]);
}
