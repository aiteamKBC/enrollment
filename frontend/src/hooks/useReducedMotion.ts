import { useEffect, useState } from 'react';

/**
 * Whether the viewer has asked their OS for less movement.
 *
 * Returns false until the effect has run, so the server/first paint is the
 * animated default and the preference is applied on mount. Read it wherever an
 * animation carries meaning — the answer is not "drop the cue", it is "show the
 * same cue without moving it".
 */
export function useReducedMotion() {
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const updatePreference = () => setReduceMotion(query.matches);
    updatePreference();
    query.addEventListener('change', updatePreference);
    return () => query.removeEventListener('change', updatePreference);
  }, []);

  return reduceMotion;
}
