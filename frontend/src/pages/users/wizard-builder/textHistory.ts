/**
 * Undo/redo history for one wording box in the builder.
 *
 * The browser's own undo cannot be relied on here: the toolbar (bold, heading…)
 * and "Reset to standard wording" change the text programmatically, which a
 * controlled React input does not record. So the editor keeps its own history.
 *
 * Typing is grouped: keystrokes less than TYPING_PAUSE_MS apart are one step,
 * so one undo takes back a burst of typing rather than a single letter. A
 * toolbar action or a reset is always a step of its own.
 */
export const TYPING_PAUSE_MS = 1000;
export const MAX_STEPS = 100;

export type ChangeKind = 'type' | 'action';

export interface TextHistory {
  past: string[];
  future: string[];
  lastKind: ChangeKind | null;
  lastAt: number;
}

export const EMPTY_HISTORY: TextHistory = { past: [], future: [], lastKind: null, lastAt: 0 };

/** Note that the text is about to change from `before`. */
export function record(history: TextHistory, before: string, kind: ChangeKind, now: number): TextHistory {
  const continuesTyping = kind === 'type' && history.lastKind === 'type' && now - history.lastAt < TYPING_PAUSE_MS;
  const past = continuesTyping ? history.past : [...history.past, before].slice(-MAX_STEPS);
  return { past, future: [], lastKind: kind, lastAt: now };
}

/** Step back from `current`; null when there is nothing to undo. */
export function undo(history: TextHistory, current: string): { history: TextHistory; value: string } | null {
  if (history.past.length === 0) return null;
  const value = history.past[history.past.length - 1];
  return {
    value,
    history: { past: history.past.slice(0, -1), future: [current, ...history.future], lastKind: null, lastAt: 0 },
  };
}

/** Step forward again from `current`; null when there is nothing to redo. */
export function redo(history: TextHistory, current: string): { history: TextHistory; value: string } | null {
  if (history.future.length === 0) return null;
  const [value, ...future] = history.future;
  return {
    value,
    history: { past: [...history.past, current].slice(-MAX_STEPS), future, lastKind: null, lastAt: 0 },
  };
}
