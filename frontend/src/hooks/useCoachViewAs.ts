import { useSyncExternalStore } from 'react';
import { coachViewAs, subscribeCoachViewAs, type CoachViewAs } from '@/lib/coachViewAs';

/**
 * The coach whose workspace an administrator currently has open, or null.
 *
 * Subscribes to the store in `@/lib/coachViewAs` so switching coach — here or
 * in another tab — re-renders every coach page rather than leaving stale
 * caseload data on screen next to the new name.
 */
export function useCoachViewAs(): CoachViewAs | null {
  return useSyncExternalStore(subscribeCoachViewAs, coachViewAs, () => null);
}
