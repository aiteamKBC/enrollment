import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { Booking, Waiting } from '@/components/feature/FirstSessionGate';
import { roleNavMap } from '@/mocks/navigation';
import { useMyLearner } from '@/hooks/useMyLearner';
import { fetchLearnerFirstSession, type LearnerFirstSession } from '@/api/learnerCalendar';
import { btnSecondary } from '@/pages/users/components/ui';
import { LockedSessionPanel } from './LockedSessionPanel';
import styles from './firstSession.module.css';

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
      <main className={styles.page}>
        <div className={state && !canBook && !error ? undefined : `${styles.workflow} ${error || !state ? styles.feedback : ''}`}>
          {error ? (
            <div className="text-center">
              <p className="text-[13px] text-red-600"><i className="ri-error-warning-line mr-1.5" aria-hidden="true" />{error}</p>
              <button className={`${btnSecondary} mt-4`} onClick={load}><i className="ri-refresh-line" aria-hidden="true" />Retry</button>
            </div>
          ) : !state ? (
            <RowsSkeleton rows={3} />
          ) : !canBook ? (
            <LockedSessionPanel key={`${kind}:${id}`} learnerId={id} onCompliance={() => navigate('/learner/compliance-documents')} />
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
