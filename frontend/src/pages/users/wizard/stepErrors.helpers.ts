import { createContext, useContext } from 'react';


/**
 * What the current step is still missing, once the learner has pressed Next.
 *
 * WizardShell provides it from the same missingForStep() list that blocks the
 * move, so a box is red exactly when it is one of the reasons Next refused —
 * the highlighting and the gate cannot disagree. Empty (nothing red) until the
 * learner tries to move on, and always on the staff side, which is not gated.
 */
export interface StepErrors {
  show: boolean;
  missing: string[];
}


export const StepErrorsContext = createContext<StepErrors>({ show: false, missing: [] });


/** The step's outstanding entry for `key`, or null. Matches `key` itself and `key — <why>`. */
export function findEntry({ show, missing }: StepErrors, key?: string): string | null {
  if (!show || !key) return null;
  return missing.find((m) => m === key || m.startsWith(`${key} — `)) ?? null;
}


/** The outstanding entry for one field's validation label, or null. */
export function useMissing(key?: string): string | null {
  return findEntry(useContext(StepErrorsContext), key);
}


/** For steps that check several hand-built fields: key → outstanding entry, or null. */
export function useMissingCheck(): (key: string) => string | null {
  const errors = useContext(StepErrorsContext);
  return (key) => findEntry(errors, key);
}


/** Whether the learner has tried to leave the step with something outstanding. */
export function useShowStepErrors(): boolean {
  const { show, missing } = useContext(StepErrorsContext);
  return show && missing.length > 0;
}


export type Verb = 'enter' | 'select' | 'choose';


// Keep acronyms (ULN, CV) as they are; lower-case the rest for mid-sentence use.
export const lowerWords = (s: string) => s.split(' ').map((w) => (w.length > 1 && w === w.toUpperCase() ? w : w.toLowerCase())).join(' ');


/**
 * "Please enter line manager name." for a field's label. A label that is a
 * question or an instruction ("Please state your nationality", "Do you…?") does
 * not read as the object of "Please enter", so it gets a plain prompt instead.
 */
export function requiredMessage(label: string, verb: Verb): string {
  if (verb === 'choose') return 'Please choose Yes or No.';
  const plain = label.replace(/[\s*?:.]+$/, '').trim();
  const isQuestion = /\?|^(please|do|are|have|has|how|what|if|could|is|would|i)\b/i.test(label.trim());
  if (!plain || isQuestion || plain.length > 40) {
    return verb === 'select' ? 'Please select an option.' : 'Please answer this question.';
  }
  return `Please ${verb} ${lowerWords(plain)}.`;
}


/**
 * The message under a red field: why this entry is outstanding. A bare key
 * means "not answered"; `key — <why>` carries its own reason (a bad format).
 */
export function missingMessage(entry: string, key: string, label: string, verb: Verb): string {
  if (entry === key) return requiredMessage(label, verb);
  const why = entry.slice(key.length + 3).trim();
  const sentence = why.charAt(0).toUpperCase() + why.slice(1);
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}
