import { useEffect, useState } from 'react';
import { useAuth } from "./useAuth.helpers";
import { useCoachViewAs } from './useCoachViewAs';
import { coachFetch } from '@/lib/coachFetch';

const SUPER_ADMIN_ACCESS = 'super-admin';

/**
 * Whose caseload the coach pages should show.
 *
 * For a coach that is always themselves: the signed-in staff account is the
 * single source of truth, which prevents these pages from silently falling back
 * to the legacy demo account.
 *
 * An administrator has no caseload of their own, so they choose a coach and read
 * that workspace instead — `hasCoachAccess` then means "there is a caseload to
 * show", not "this account is a coach". The server is the boundary either way:
 * it re-derives the identity from the session on every request and refuses to
 * write through a chosen one.
 */
export function useCoachIdentity() {
  const { auth, isInitialized } = useAuth();
  const viewAs = useCoachViewAs();
  const account = auth.account;
  const ownsCoachWorkspace = account?.access === 'coach';
  const canChooseCoach = account?.access === SUPER_ADMIN_ACCESS;
  // A stored selection belongs to the admin who made it; `syncCoachViewAsAccount`
  // clears anybody else's as soon as the account resolves, and this is the
  // second half of that guard for the render before it does.
  const viewingAs = canChooseCoach ? viewAs : null;

  return {
    email: viewingAs?.email || (ownsCoachWorkspace ? account?.email || '' : ''),
    name: viewingAs?.name || account?.displayName || auth.user?.fullName || 'Coach',
    hasCoachAccess: Boolean(viewingAs) || ownsCoachWorkspace,
    /** True while an admin reads somebody else's workspace — reads only. */
    isViewingAsCoach: Boolean(viewingAs),
    /** True for an admin, who picks whose workspace to open. */
    canChooseCoach,
    isInitialized,
  };
}

export function useCoachAssignedLearnerNames() {
  const coach = useCoachIdentity();
  const [assignedLearnerNames, setAssignedLearnerNames] = useState<Set<string>>(new Set());
  const [caseloadLoading, setCaseloadLoading] = useState(true);
  const [caseloadError, setCaseloadError] = useState('');

  useEffect(() => {
    if (!coach.isInitialized) return;
    if (!coach.email) {
      setAssignedLearnerNames(new Set());
      setCaseloadError('Coach access is required to load assigned learners.');
      setCaseloadLoading(false);
      return;
    }

    const controller = new AbortController();
    setCaseloadLoading(true);
    setCaseloadError('');

    coachFetch('/coach_api/coach/caseload?summary=1', {
      signal: controller.signal,
    })
      .then(async response => {
        const payload = await response.json().catch(() => ({})) as {
          detail?: string;
          learners?: Array<{ name?: string }>;
        };
        if (!response.ok) throw new Error(payload.detail || 'Unable to load coach caseload.');
        if (controller.signal.aborted) return;
        setAssignedLearnerNames(new Set(
          (payload.learners || [])
            .map(learner => String(learner.name || '').trim().toLocaleLowerCase())
            .filter(Boolean),
        ));
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setAssignedLearnerNames(new Set());
        setCaseloadError(error instanceof Error ? error.message : 'Unable to load coach caseload.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setCaseloadLoading(false);
      });

    return () => controller.abort();
  }, [coach.email, coach.isInitialized]);

  return { ...coach, assignedLearnerNames, caseloadLoading, caseloadError };
}
