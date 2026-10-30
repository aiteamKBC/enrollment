import { createContext, useContext } from 'react';
import type { CustomAnswer, LayoutStep, WizardLayout } from './layout/types';
import { type EnrolmentBoard, type WizardDraft } from '../types';


/** DD/MM/YYYY -> YYYY-MM-DD (for native date inputs); returns '' if unparseable. */
export function ddmmToIso(s?: string): string {
  if (!s) return '';
  const m = s.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : '';
}


/**
 * Whole years between a YYYY-MM-DD date of birth and today.
 *
 * Age is derived, never typed: the field is read-only in Personal Details, so
 * this is the only thing that sets it. Computed from date parts rather than a
 * millisecond difference so leap years and DST can't shift a birthday.
 * `undefined` for anything not usable — unparseable, a future date, or a value
 * so old it must be a typo — which leaves the step's required-check failing
 * rather than showing a nonsense age.
 */
export function ageFromDob(iso?: string): number | undefined {
  if (!iso) return undefined;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return undefined;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dob = new Date(y, mo - 1, d);
  // Rejects impossible calendar dates (31 Feb rolls over to March).
  if (dob.getFullYear() !== y || dob.getMonth() !== mo - 1 || dob.getDate() !== d) return undefined;
  const now = new Date();
  let age = now.getFullYear() - y;
  // Birthday hasn't come round yet this year.
  if (now.getMonth() < mo - 1 || (now.getMonth() === mo - 1 && now.getDate() < d)) age -= 1;
  if (age < 0 || age > 120) return undefined;
  return age;
}


export interface WizardContextValue {
  userId: string;
  /** Which table this learner lives in — decides where wizard edits are saved. */
  isCommercial: boolean;
  board: EnrolmentBoard;
  draft: WizardDraft;
  setSection: <K extends keyof WizardDraft>(key: K, value: WizardDraft[K]) => void;
  /** Answer one of the wizard builder's custom fields. */
  setCustom: (fieldKey: string, value: CustomAnswer) => void;
  /**
   * The published wizard layout (see layout/), made whole against the
   * built-in registry — the default layout until one is published or while it
   * is loading.
   */
  layout: WizardLayout;
  /** The steps the learner walks through, in order — the layout's visible steps. */
  steps: LayoutStep[];
  completed: boolean[];
  markComplete: (index: number, done: boolean) => void;
  /**
   * True on the staff side for learner-owned steps (currently the Skills Radar
   * self-assessment): staff review it but must not overwrite the learner's own
   * answers. Set from the provider's `readOnlyLearnerSteps` prop.
   */
  readOnly: boolean;
  /** Persist the Extended ILR answers to enrolment."Extended_ILR". */
  saveIlr: () => Promise<void>;
  ilrSaving: boolean;
  /** Last successful save (ISO), or '' if nothing saved this session. */
  ilrSavedAt: string;
  /**
   * True once the saved answers have been loaded (or the load has failed).
   * Anything that judges completeness — step gating, deep-link guards — must
   * wait for it, or a returning learner is measured against a blank draft.
   *
   * Note this covers the saved answers only. The programme's competencies arrive
   * in a second request after it — see `ready` for the whole picture.
   */
  hydrated: boolean;
  /**
   * True once the draft is actually worth measuring: the saved answers have
   * loaded *and* the programme's competencies have been seeded (or settled as
   * having none).
   *
   * `hydrated` alone is not enough. The Skills Radar's completeness is judged
   * against the seeded assessment rows, and an empty map has nothing unrated in
   * it — so between hydration and seeding the step reads as finished, which both
   * flickered the progress rail and briefly let the gating wave a learner past
   * their own self-assessment.
   *
   * It waits for the published layout too: which fields are required comes
   * from there, so judging the draft against the default would gate a learner
   * on questions the enrolment team has since removed (or let them past new ones).
   */
  ready: boolean;
  /**
   * Render the Extended ILR to PDF and file it in the enrolment-docs container,
   * so it shows up under Compliance documents on the learner's board.
   */
  fileIlrDocument: () => Promise<void>;
  ilrFiling: boolean;
  /**
   * The Inclusion step's check against the Safeguarding database. Its result
   * is draft.inclusion; this is the check itself, so the step can show that it
   * is running or failed and ask again (the learner answers in another tab).
   */
  inclusionCheck: { checking: boolean; error: string | null; refresh: () => void };
}


export const WizardContext = createContext<WizardContextValue | null>(null);


export function useWizard(): WizardContextValue {
  const ctx = useContext(WizardContext);
  if (!ctx) throw new Error('useWizard must be used within WizardProvider');
  return ctx;
}
