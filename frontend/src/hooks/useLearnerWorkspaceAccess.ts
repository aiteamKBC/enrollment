import { useEffect, useState } from 'react';
import { useAuth } from "./useAuth.helpers";
import { parsePersonalLearning } from '@/lib/personalLearning';

/**
 * Whether the person looking at a learner's workspace may act *as* that learner.
 *
 * Learners, including staff with a linked enrolment, can work on their own
 * record. Administrators, and coaches for learners on their own caseload,
 * can use the same actions for a selected
 * learner, with their own account retained in the server audit trail. Other
 * staff and employer previews remain read-only.
 * `login.permissions.learner_self_or_admin` enforces the same rule at the API.
 */
export interface LearnerWorkspaceAccess {
  /**
   * May the viewer open and complete training-plan activities, upload evidence
   * and submit reflections for this learner? Available to the owner and admin.
   */
  canProgress: boolean;
  /**
   * Show the "read-only" explanation. Separate from `!canProgress` so the
   * banner does not flash on the learner's own page during the first render,
   * while the session is still resolving and `canProgress` is fail-closed.
   */
  showReadOnlyNotice: boolean;
}

// One question per coach and learner, shared by every component on the page.
// A failed answer is dropped so the next mount asks again.
const coachAnswers = new Map<string, Promise<boolean>>();
function coachCanAct(key: string, learnerId: string): Promise<boolean> {
  let answer = coachAnswers.get(key);
  if (!answer) {
    answer = fetch(`/login_api/learner-action-access/?learnerId=${encodeURIComponent(learnerId)}`, { credentials: 'include' })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
      .then((body) => body?.canAct === true)
      .catch(() => { coachAnswers.delete(key); return false; });
    coachAnswers.set(key, answer);
  }
  return answer;
}

/**
 * @param learnerId The learner whose workspace is open — the `:id` route param,
 *   or the remembered learner on the paramless self-view pages.
 */
export function useLearnerWorkspaceAccess(
  learnerId?: string | number | null,
): LearnerWorkspaceAccess {
  const { auth, isInitialized } = useAuth();
  const account = auth.account;
  const personal = parsePersonalLearning(learnerId);
  const validLearner = Number.isSafeInteger(Number(learnerId)) && Number(learnerId) > 0;

  const isSelf = Boolean(
    account
      && account.role === 'learner'
      && account.subjectType === 'learner'
      // Matched on id alone, like the server's gate: ids are unique across the
      // single Created_users table, so the learner kind is not part of it.
      && String(account.subjectId) === String(learnerId ?? ''),
  );
  // /me resolves this link on the server while retaining the staff account.
  // The API independently checks ownership again on every learning action.
  const isStaffSelf = validLearner && account?.role === 'staff' && account.subjectType === 'staff'
    && String(account.learnerRecordId) === String(learnerId);

  // No server account once the session has resolved means the landing page's
  // "explore this section" shortcut (`useAuth.previewAs`), which sets local UI
  // state and holds no session. Those pages are a demo of the learner flow, so
  // they keep it — and they cannot write anything regardless, because every
  // progress endpoint now answers an unauthenticated POST with a 401.
  const isDemoPreview = isInitialized && !account;

  // Fail closed until the session resolves: a staff viewer who clicks in the
  // first frame must not get through. The learner loses nothing by it, since
  // every one of these actions is a deliberate click, not an auto-submit.
  const isAdmin = account?.role === 'admin' && validLearner;

  // Whether a learner is on a coach's caseload is only known to the server, so
  // a coach asks it. Fail closed: no answer yet, or any error, is read-only.
  const coachKey = isInitialized && !personal && !isStaffSelf && validLearner && account?.role === 'staff'
    && (account.accesses ?? [account.access]).includes('coach')
    ? `${account.id}:${learnerId}` : null;
  const [coachAnswer, setCoachAnswer] = useState<{ key: string; canAct: boolean } | null>(null);
  useEffect(() => {
    if (!coachKey) return;
    let current = true;
    coachCanAct(coachKey, String(learnerId)).then((canAct) => {
      if (current) setCoachAnswer({ key: coachKey, canAct });
    });
    return () => { current = false; };
  }, [coachKey, learnerId]);
  const coachAnswered = coachKey !== null && coachAnswer?.key === coachKey;
  const isOwnCoach = coachAnswered && coachAnswer.canAct;

  const canProgress = personal
    ? isInitialized && account?.role === 'admin' && account.id === personal.accountId
    : isDemoPreview || (isInitialized && (isSelf || isStaffSelf || isAdmin || isOwnCoach));

  // Like the session itself: no read-only banner while the coach answer is pending.
  const pending = coachKey !== null && !coachAnswered;
  return { canProgress, showReadOnlyNotice: isInitialized && !canProgress && !pending };
}
