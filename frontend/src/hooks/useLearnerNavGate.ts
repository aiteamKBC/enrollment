import { useEffect, useState } from 'react';
import type { SidebarNavItem } from '@/components/feature/Sidebar';
import { fetchLearnerSummary, type LearnerKind, type LearnerSummary } from '@/api/learnerDetail';
import { canViewAssignedProgramme } from '@/utils/learnerAccessGate';
import { getRememberedLearner, rememberLearner } from './useMyLearner';
import { isDeliveryStatus, isEnrolmentSubmitted, isOnboardingStatus, navItemsForLearnerKind, navItemsForStatus } from './useOnboardingRedirect';

// ============================================================================
// Restricts the learner sidebar to match their programme status.
//
// Onboarding and Delivery are pre-teaching states: there is no running training
// plan, so evidence, attendance, quizzes and progress pages would render as
// empty shells. Rather than gate this in each of the ~40 learner pages, the
// workspace shell applies it once for every one of them.
//
// The status is fetched once per learner and cached for the browser session, so
// neither switching pages nor reloading re-requests it.
// ============================================================================

/** Cached per learner so navigating between pages doesn't refetch. */
const statusCache = new Map<string, string>();

const storageKey = (cacheKey: string) => `learner_status:${cacheKey}`;
const learnerKindKey = (id: string) => `learner_kind:${id}`;
const historyKey = (key: string) => `learner_previous_learning:${key}`;
const readyKey = (key: string) => `learner_ready_for_learning:${key}`;
const readyCache = new Map<string, boolean>();
const onboardingKey = (key: string) => `learner_onboarding_status:${key}`;
const onboardingCache = new Map<string, string>();

/** Last known enrolment-wizard status ('Submitted', 'Completed'…), or undefined if never seen. */
function cachedOnboarding(key: string): string | undefined {
  if (onboardingCache.has(key)) return onboardingCache.get(key);
  try {
    const value = sessionStorage.getItem(onboardingKey(key));
    if (value !== null) onboardingCache.set(key, value);
    return value ?? undefined;
  } catch { return undefined; }
}

/**
 * Stored when the summary was fetched but carried no enrolment status (an
 * older payload), so it is not looked up again on every page. Counts as
 * submitted: an unknown never locks the learner out.
 */
const ONBOARDING_UNKNOWN = '(not provided)';

/** Whether the learner may use Reviews, from a cached enrolment status. */
function reviewsOpen(onboardingStatus: string | undefined): boolean {
  return onboardingStatus === undefined || onboardingStatus === ONBOARDING_UNKNOWN || isEnrolmentSubmitted(onboardingStatus);
}

const firstSessionKey = (key: string) => `learner_first_session_unlocked:${key}`;

/** Whether a Delivery apprentice's First Learning Session tab is open; undefined if never seen. */
function cachedFirstSession(key: string): boolean | undefined {
  try {
    const value = sessionStorage.getItem(firstSessionKey(key));
    return value === null ? undefined : value === 'true';
  } catch { return undefined; }
}

function rememberFirstSession(key: string, unlocked: boolean): void {
  try { sessionStorage.setItem(firstSessionKey(key), String(unlocked)); } catch { /* storage is optional */ }
}

function rememberOnboarding(key: string, value: string): void {
  onboardingCache.set(key, value);
  try { sessionStorage.setItem(onboardingKey(key), value); } catch { /* storage is optional */ }
}

function cachedReady(key: string): boolean | undefined {
  if (readyCache.has(key)) return readyCache.get(key);
  try {
    const value = sessionStorage.getItem(readyKey(key));
    return value === null ? undefined : value === 'true';
  } catch { return undefined; }
}

function cachedHistory(key: string): boolean | undefined {
  try {
    const value = sessionStorage.getItem(historyKey(key));
    return value === null ? undefined : value === 'true';
  } catch { return undefined; }
}

/**
 * Last known status for this learner, from the module cache or — after a
 * reload, which empties it — from sessionStorage.
 *
 * Read synchronously into the initial state rather than in an effect: an
 * onboarding learner whose status arrives a frame late is shown the full
 * delivery menu first and watches it collapse to their two items.
 */
function cachedStatus(cacheKey: string): string | null {
  const inMemory = statusCache.get(cacheKey);
  if (inMemory !== undefined) return inMemory;
  try {
    const stored = sessionStorage.getItem(storageKey(cacheKey));
    if (stored !== null) {
      statusCache.set(cacheKey, stored);
      return stored;
    }
  } catch {
    /* storage unavailable — fall back to fetching */
  }
  return null;
}

/** Mounted gates, so a status correction re-renders the sidebar immediately. */
const listeners = new Set<() => void>();

function rememberStatus(cacheKey: string, status: string): void {
  statusCache.set(cacheKey, status);
  try {
    sessionStorage.setItem(storageKey(cacheKey), status);
  } catch {
    /* storage unavailable — the module cache still covers this session */
  }
}

/**
 * Reconcile the cache with a status the caller has just seen live.
 *
 * The cache is never expired — that is deliberate, since re-requesting the
 * status on every navigation would be wasteful. But it means a status changed
 * by staff would otherwise not reach the learner until they opened a new
 * browser session, and for a learner sitting at 'Fresh user' the whole
 * workspace is one waiting page: a stale entry is not a cosmetic menu problem,
 * it keeps them on that page after their enrolment has actually started.
 *
 * The learner's own overview fetches the real record anyway, so it calls this
 * with what it found. A no-op when nothing changed.
 */
export function syncLearnerStatus(
  kind: string | undefined,
  id: string | undefined,
  status: string | null | undefined,
  summary?: Pick<LearnerSummary, 'accessGate' | 'learningAccess' | 'studentActivityAvailable'> & Partial<Pick<LearnerSummary, 'onboardingStatus' | 'firstSessionUnlocked'>>,
): void {
  if (!kind || !id || status == null) return;
  const cacheKey = `${kind}:${id}`;
  let changed = statusCache.get(cacheKey) !== status;
  if (summary?.onboardingStatus !== undefined && cachedOnboarding(cacheKey) !== summary.onboardingStatus) {
    rememberOnboarding(cacheKey, summary.onboardingStatus);
    changed = true;
  }
  if (summary?.firstSessionUnlocked !== undefined && cachedFirstSession(cacheKey) !== summary.firstSessionUnlocked) {
    rememberFirstSession(cacheKey, summary.firstSessionUnlocked);
    changed = true;
  }
  if (summary) {
    // Not gated on learningAccess.blocked: that flag is only "the cohort start
    // date has not arrived", and a learner waiting for their start date still
    // gets their full sidebar.
    const ready = canViewAssignedProgramme(kind, summary.accessGate);
    changed ||= cachedReady(cacheKey) !== ready || cachedHistory(cacheKey) !== !!summary.studentActivityAvailable;
    readyCache.set(cacheKey, ready);
    try {
      sessionStorage.setItem(readyKey(cacheKey), String(ready));
      sessionStorage.setItem(historyKey(cacheKey), String(!!summary.studentActivityAvailable));
    } catch { /* storage is optional */ }
  }
  if (!changed) return;
  rememberStatus(cacheKey, status);
  listeners.forEach((notify) => notify());
}

/**
 * Record that the learner has just submitted their enrolment, so the locked
 * Reviews item opens at once rather than on their next browser session.
 */
export function markEnrolmentSubmitted(kind: string | undefined, id: string | undefined): void {
  if (!kind || !id) return;
  rememberOnboarding(`${kind}:${id}`, 'Submitted');
  listeners.forEach((notify) => notify());
}

/**
 * Record that the learner has just signed their last compliance document, so
 * their First Learning Session tab opens at once.
 */
export function markFirstSessionUnlocked(kind: string | undefined, id: string | undefined): void {
  if (!kind || !id) return;
  rememberFirstSession(`${kind}:${id}`, true);
  listeners.forEach((notify) => notify());
}

/**
 * Whether this learner's enrolment is submitted: null while it is being looked
 * up. A failed lookup counts as submitted, for the same lock-out reason as the
 * sidebar's fallback to the full menu.
 */
export function useEnrolmentSubmitted(kind: string | undefined, id: string | undefined): boolean | null {
  const cacheKey = kind && id ? `${kind}:${id}` : '';
  const read = () => {
    const value = cacheKey ? cachedOnboarding(cacheKey) : undefined;
    return value === undefined ? null : reviewsOpen(value);
  };
  const [submitted, setSubmitted] = useState<boolean | null>(read);

  useEffect(() => {
    if (!cacheKey) return;
    const notify = () => setSubmitted(read());
    listeners.add(notify);
    notify();
    let cancelled = false;
    if (cachedOnboarding(cacheKey) === undefined) {
      fetchLearnerSummary(kind as LearnerKind, id!)
        .then((detail) => {
          const value = detail.onboardingStatus ?? ONBOARDING_UNKNOWN;
          rememberOnboarding(cacheKey, value);
          if (!cancelled) setSubmitted(reviewsOpen(value));
        })
        .catch(() => { if (!cancelled) setSubmitted(true); });
    }
    return () => {
      cancelled = true;
      listeners.delete(notify);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey]);

  return submitted;
}

export function useLearnerNavGate(role: string, navItems: SidebarNavItem[], reviewingLearner = false): SidebarNavItem[] {
  const learner = role === 'learner' ? getRememberedLearner() : null;
  const cacheKey = learner ? `${learner.kind}:${learner.id}` : '';
  const [status, setStatus] = useState<string | null>(
    cacheKey ? cachedStatus(cacheKey) : null,
  );
  const [history, setHistory] = useState({ key: cacheKey, available: cachedHistory(cacheKey) === true });
  const [ready, setReady] = useState({ key: cacheKey, available: cachedReady(cacheKey) === true });
  const [onboarding, setOnboarding] = useState({ key: cacheKey, status: cachedOnboarding(cacheKey) });
  const [firstSession, setFirstSession] = useState({ key: cacheKey, unlocked: cachedFirstSession(cacheKey) });

  // Re-read the cache whenever syncLearnerStatus corrects it, so a learner
  // whose status changed mid-session gets their menu back without a reload.
  useEffect(() => {
    if (!cacheKey) return;
    const notify = () => {
      setStatus(cachedStatus(cacheKey));
      setHistory({ key: cacheKey, available: cachedHistory(cacheKey) === true });
      setReady({ key: cacheKey, available: cachedReady(cacheKey) === true });
      const onboardingNow = cachedOnboarding(cacheKey);
      setOnboarding((previous) => previous.key === cacheKey && previous.status === onboardingNow
        ? previous : { key: cacheKey, status: onboardingNow });
      const firstSessionNow = cachedFirstSession(cacheKey);
      setFirstSession((previous) => previous.key === cacheKey && previous.unlocked === firstSessionNow
        ? previous : { key: cacheKey, unlocked: firstSessionNow });
    };
    listeners.add(notify);
    return () => {
      listeners.delete(notify);
    };
  }, [cacheKey]);

  useEffect(() => {
    if (!learner || !cacheKey) return;
    const cached = cachedStatus(cacheKey);
    if (cached !== null) {
      setStatus(cached);
      // Status is cached by kind, but older sessions stored every signed-in
      // learner as apprenticeship. Verify the source type once per session so
      // a commercial learner's restricted menu cannot be bypassed by that old
      // browser value.
      try {
        if (sessionStorage.getItem(learnerKindKey(learner.id)) === learner.kind
          && (!isDeliveryStatus(cached) || (cachedHistory(cacheKey) !== undefined && cachedReady(cacheKey) !== undefined))
          // Onboarding apprentices also need to know whether they have
          // submitted (their Reviews unlock then); commercial learners have none.
          && (!isOnboardingStatus(cached) || learner.kind === 'commercial' || cachedOnboarding(cacheKey) !== undefined)
          // Delivery apprentices also need to know whether their First Learning Session tab is open.
          && (!isDeliveryStatus(cached) || learner.kind === 'commercial' || cachedFirstSession(cacheKey) !== undefined)) return;
      } catch {
        // Storage is optional; verify from the API below.
      }
    }
    let cancelled = false;
    fetchLearnerSummary(learner.kind, learner.id)
      .then((detail) => {
        // A learner account used to be remembered as apprenticeship by default.
        // Trust the API's stored learner type and repair that stale browser
        // value, otherwise commercial-only sidebar rules never take effect.
        if (detail.learnerType && detail.learnerType !== learner.kind) {
          rememberLearner(detail.learnerType, learner.id);
        }
        try {
          sessionStorage.setItem(learnerKindKey(learner.id), detail.learnerType || learner.kind);
          sessionStorage.setItem(historyKey(cacheKey), String(!!detail.studentActivityAvailable));
        } catch {
          /* storage unavailable */
        }
        const value = detail?.programmeStatus || '';
        syncLearnerStatus(learner.kind, learner.id, value, detail);
        if (detail?.onboardingStatus === undefined && cachedOnboarding(cacheKey) === undefined) {
          rememberOnboarding(cacheKey, ONBOARDING_UNKNOWN);
        }
        if (!cancelled) {
          setStatus(value);
          // Only a real change re-renders: a new object every time would loop
          // (render → effect → fetch → render) for a payload without the field.
          const onboardingNow = cachedOnboarding(cacheKey);
          setOnboarding((previous) => previous.key === cacheKey && previous.status === onboardingNow
            ? previous : { key: cacheKey, status: onboardingNow });
          // A summary without the flag (an older server) is remembered as open,
          // so it is not looked up again on every page — the page itself checks.
          if (cachedFirstSession(cacheKey) === undefined) rememberFirstSession(cacheKey, detail?.firstSessionUnlocked ?? true);
          const firstSessionNow = cachedFirstSession(cacheKey);
          setFirstSession((previous) => previous.key === cacheKey && previous.unlocked === firstSessionNow
            ? previous : { key: cacheKey, unlocked: firstSessionNow });
          const readyNow = cachedReady(cacheKey) === true;
          setReady((previous) => previous.key === cacheKey && previous.available === readyNow
            ? previous : { key: cacheKey, available: readyNow });
          setHistory((previous) => previous.key === cacheKey && previous.available === !!detail.studentActivityAvailable
            ? previous : { key: cacheKey, available: !!detail.studentActivityAvailable });
        }
      })
      .catch(() => {
        // A failed lookup must not lock the learner out of their own workspace,
        // so fall back to the full nav rather than a guess. Deliberately NOT
        // cached: a dropped request would otherwise pin the full menu on an
        // onboarding learner for the rest of the session.
        if (!cancelled) setStatus('');
      });
    return () => {
      cancelled = true;
    };
  }, [learner, cacheKey]);

  // Not a learner — the gate doesn't apply.
  if (!learner) return navItems;
  // Staff can review programme pages at every stage from any learner page.
  // Learner-type rules still apply: commercial enrolment would simply redirect
  // back to Dashboard and must not appear as a second destination.
  if (reviewingLearner) return navItemsForLearnerKind(navItems, learner.kind);
  // First visit of the session, status still in flight. An empty rail for that
  // moment is honest; showing the full menu would be showing the wrong one, and
  // an onboarding learner would see it visibly collapse once the status lands.
  if (status === null) return [];
  const hasPreviousLearning = history.key === cacheKey ? history.available : cachedHistory(cacheKey) === true;
  const readyForLearning = ready.key === cacheKey ? ready.available : cachedReady(cacheKey) === true;
  const onboardingStatus = onboarding.key === cacheKey ? onboarding.status : cachedOnboarding(cacheKey);
  // Unknown (an older payload, or not looked up yet) leaves Reviews open.
  const enrolmentSubmitted = reviewsOpen(onboardingStatus);
  const firstSessionUnlocked = (firstSession.key === cacheKey ? firstSession.unlocked : cachedFirstSession(cacheKey)) ?? true;
  return navItemsForStatus(status, navItems, learner.kind, hasPreviousLearning, readyForLearning, enrolmentSubmitted, firstSessionUnlocked);
}
