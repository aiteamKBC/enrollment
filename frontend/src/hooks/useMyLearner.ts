import type { LearnerKind } from '@/api/learnerDetail';
import { useLocation } from 'react-router-dom';
import { ownPersonalLearning, parsePersonalLearning, rememberPersonalLearning } from '@/lib/personalLearning';
import { learnerIdentityFromPath } from '@/lib/learnerRoutes';

/**
 * Learner sessions always resolve to the account's enrolment id. Staff review
 * follows the learner selected by URL, remembered for paramless sidebar links.
 * The server remains responsible for authorising every request.
 */
// Current source id for the default demo learner after the enrolment-table
// merge. Explicitly selected learners still override this value. The previous
// fallback (19) belonged to the pre-merge table and no longer exists in the
// current enrolment source, so every overview read for a fresh browser would
// otherwise return 404.
const MY_LEARNER: { kind: LearnerKind; id: string } = { kind: 'commercial', id: '125' };
const STORAGE_KEY = 'my_learner';
// Keep session identity independent of shared browser storage: another tab or
// an old deep link must not switch a learner onto somebody else's record.
let signedInLearner: { kind: LearnerKind; id: string } | null = null;

function isKind(v: unknown): v is LearnerKind {
  return v === 'commercial' || v === 'apprenticeship';
}

function readOverride(): { kind: LearnerKind; id: string } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (isKind(parsed.kind) && parsed.id) {
      return { kind: parsed.kind, id: String(parsed.id) };
    }
  } catch { /* ignore malformed override */ }
  return null;
}

/** Session identity wins over the staff review selection and demo fallback. */
export function getRememberedLearner(): { kind: LearnerKind; id: string } | null {
  const personal = ownPersonalLearning();
  if (personal) return { kind: 'commercial', id: personal.id };
  return signedInLearner || readOverride() || MY_LEARNER;
}

/**
 * Pin the *signed-in* learner as the active one.
 *
 * Called from the auth layer for every sign-in, session restore and refresh.
 * Writing localStorage alone is insufficient: an old URL or another tab can
 * overwrite it immediately after sign-in, causing every API read to return 404.
 * Clear the pin on sign-out or a switch to staff so their review links work.
 */
export function rememberSignedInLearner(
  subjectType: string | undefined,
  subjectId: number | string | undefined,
  learnerType?: string | null,
): void {
  signedInLearner = subjectType === 'learner' && subjectId != null && subjectId !== ''
    ? { kind: learnerType === 'commercial' ? 'commercial' : 'apprenticeship', id: String(subjectId) }
    : null;
  if (signedInLearner) rememberLearner(signedInLearner.kind, signedInLearner.id);
  else {
    // A staff/admin session has no implicit learner target.  Remove any
    // persisted review selection so a parameterless learner route cannot
    // silently inherit the previous user's learner.
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* storage unavailable */ }
  }
}

/** Persist the active learner so paramless /learner/* pages resolve to it. */
export function rememberLearner(kind: string | undefined, id: string | undefined): void {
  if (parsePersonalLearning(id)) { rememberPersonalLearning(id!); return; }
  if (!isKind(kind) || !id) return;
  if (signedInLearner) {
    if (signedInLearner.id !== String(id)) return;
    // The summary may supply the current type after an older /me response.
    if (signedInLearner.kind !== kind) signedInLearner = { kind, id: String(id) };
  }
  const current = readOverride();
  if (current && current.kind === kind && current.id === String(id)) return; // no-op if unchanged
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ kind, id: String(id) }));
  } catch { /* storage unavailable — fall back to default */ }
}

/** Resolve which real learner the bare /learner/* self-view pages should load. */
export function useMyLearner(): { kind: LearnerKind; id: string } {
  const { pathname } = useLocation();
  const fromUrl = learnerIdentityFromPath(pathname);
  if (signedInLearner) return signedInLearner;
  return fromUrl || getRememberedLearner()!;
}

/** Preserve an explicit learner when following a Training Plan booking link. */
export function useLinkedLearner(): { kind: LearnerKind; id: string } {
  const { search, pathname } = useLocation();
  if (signedInLearner) return signedInLearner;
  const fromUrl = learnerIdentityFromPath(pathname);
  if (fromUrl) { rememberLearner(fromUrl.kind, fromUrl.id); return fromUrl; }
  const params = new URLSearchParams(search);
  const kind = params.get('kind');
  const id = params.get('learner');
  if (isKind(kind) && id && /^[1-9]\d*$/.test(id)) {
    rememberLearner(kind, id);
    return { kind, id };
  }
  return readOverride() || MY_LEARNER;
}

/**
 * Resolve the learner a page should show: the signed-in learner's account wins.
 * Staff URL selections are persisted for subsequent paramless navigation.
 *
 * Replaces the `urlId ?? myLearner?.id` idiom at every learner-page call site.
 */
export function useResolvedLearner(
  urlKind: string | undefined,
  urlId: string | undefined,
): { kind: LearnerKind | undefined; id: string | undefined } {
  const personal = ownPersonalLearning();
  if (personal && (!urlId || urlId === personal.id)) return { kind: 'commercial' as const, id: personal.id };
  if (signedInLearner) return signedInLearner;
  // Persist synchronously while the URL still carries the learner — an effect
  // could fire after the user has already clicked a paramless sidebar link, so
  // the write must happen during this render (rememberLearner is idempotent).
  if (isKind(urlKind) && urlId) {
    rememberLearner(urlKind, urlId);
    return { kind: urlKind, id: urlId };
  }
  // No params — fall back to the remembered/default learner. (readOverride is
  // called directly, not via the useMyLearner hook, to keep this branch-safe.)
  const my = readOverride() || (import.meta.env.MODE === 'test' ? MY_LEARNER : null);
  return my ? { kind: my.kind, id: my.id } : { kind: undefined, id: undefined };
}
