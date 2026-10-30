import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { Booking, Waiting } from '@/components/feature/FirstSessionGate';
import { roleNavMap } from '@/mocks/navigation';
import { useMyLearner } from '@/hooks/useMyLearner';
import { fetchLearnerFirstSession, type LearnerFirstSession } from '@/api/learnerCalendar';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';

const learnerNav = roleNavMap.learner;

/**
 * First Learning Session — a Delivery apprentice books the session that starts
 * their programme, once they have signed all four compliance documents.
 *
 * Whether they may is the server's call (`canBook`), so a stale sidebar or a
 * typed address cannot book early; the booking endpoint refuses it too.
 */
export default function LearnerFirstSessionPage() {
  const { kind, id } = useMyLearner();
  const navigate = useNavigate();
  const [state, setState] = useState<LearnerFirstSession | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchLearnerFirstSession(kind, id)
      .then(setState)
      .catch((e: Error) => setError(e.message));
  }, [kind, id]);

  useEffect(load, [load]);

  // An older server does not send canBook; access then says it all.
  const canBook = state ? (state.canBook ?? state.access !== 'enrolling') : false;

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      navItems={learnerNav.items}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="First Learning Session"
      pageSubtitle="Book the session that starts your programme"
    >
      <main className="page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
        <div className="mx-auto max-w-xl rounded-2xl border border-foreground-200 bg-background-50 p-6 sm:p-8">
          {error ? (
            <div className="text-center">
              <p className="text-[13px] text-red-600"><i className="ri-error-warning-line mr-1.5" aria-hidden="true" />{error}</p>
              <button className={`${btnSecondary} mt-4`} onClick={load}><i className="ri-refresh-line" aria-hidden="true" />Retry</button>
            </div>
          ) : !state ? (
            <RowsSkeleton rows={3} />
          ) : !canBook ? (
            <div className="text-center">
              <i className="ri-lock-line text-3xl text-primary-600" aria-hidden="true" />
              <h2 className="mt-3 text-lg font-heading font-semibold text-foreground-900">Sign your compliance documents first</h2>
              <p className="mt-2 text-[13px] leading-relaxed text-foreground-600">
                Your first learning session can be booked once you have signed all four of your compliance documents:
                the Apprenticeship Agreement, Individual Learner Record, Training Plan and Written Agreement.
              </p>
              <button className={`${btnPrimary} mt-5`} onClick={() => navigate('/learner/compliance-documents')}>
                <i className="ri-shield-check-line" aria-hidden="true" />Go to Compliance documents
              </button>
            </div>
          ) : state.booked ? (
            <Waiting state={state} />
          ) : (
            <Booking state={state} kind={kind} learnerId={id} onBooked={load} />
          )}
        </div>
      </main>
    </WorkspaceShell>
  );
}
