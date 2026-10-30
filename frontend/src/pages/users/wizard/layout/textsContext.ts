import { createContext, useContext } from 'react';
import { textFor } from './texts';

/**
 * The published wording edits (layout.texts), provided by WizardProvider.
 * Kept apart from the wizard's own context so wording can be read anywhere —
 * including a step rendered on its own, where it is simply the standard wording.
 */
export const WizardTextsContext = createContext<Record<string, string> | undefined>(undefined);

/** `t(slot)` — the wording for a text slot: the published edit, else the standard wording. */
export function useText(): (key: string) => string {
  const texts = useContext(WizardTextsContext);
  return (key) => textFor(texts, key);
}
