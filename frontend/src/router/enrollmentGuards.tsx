import { useEffect, useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth.helpers';
import { mayAccessRoute } from '@/lib/routeAccess';
import { fetchFirstLoginDetails } from '@/api/firstLoginDetails';
import { fetchLearnerFirstSession } from '@/api/learnerCalendar';
import { useMyLearner, rememberLearner } from '@/hooks/useMyLearner';
import { fetchEnrolmentBoard } from '@/api/enrolmentUsers';
import { canManageEnrolment, hasOwnEnrolment } from '@/lib/enrollmentAccess';


export function AccessGate() {
  const { auth, isInitialized, initializationError, retryInitialization } = useAuth();
  const { pathname, search } = useLocation();
  if (!isInitialized) return <p role="status" className="p-6">Checking your session…</p>;
  if (initializationError) return <div role="alert" className="p-6"><p>{initializationError}</p><button onClick={retryInitialization}>Try again</button></div>;
  if (!auth.account) return <Navigate to="/login" replace />;
  const isStaff = ['admin', 'staff'].includes(auth.account.role);
  const manages = canManageEnrolment(auth.account);
  const own = hasOwnEnrolment(auth.account);
  const ownPath = pathname === '/learner' || pathname.startsWith('/learner/') || pathname.startsWith('/workspace/learner') || ['/', '/choose-workspace', '/enrollment', '/lms'].includes(pathname);
  if (isStaff && own && (!manages || new URLSearchParams(search).get('self') === '1')) {
    rememberLearner(auth.account.learnerRecordKind || 'commercial', String(auth.account.learnerRecordId));
  }
  if ((isStaff && !manages && !(own && ownPath)) ||
      (!['/', '/choose-workspace', '/enrollment', '/lms'].includes(pathname) && !mayAccessRoute(pathname, auth.account))) {
    return <div role="alert" className="p-6">Your account does not have access to this enrolment page.</div>;
  }
  return <Outlet />;
}

export function LearnerStart() {
  const { kind, id } = useMyLearner();
  const [destination, setDestination] = useState('');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError('');
    async function resolve() {
      if (kind === 'apprenticeship') {
        const details = await fetchFirstLoginDetails(id);
        if (details.required) return '/learner/welcome';
        const board = await fetchEnrolmentBoard(id);
        const status = (board.programme.status || '').toLowerCase();
        if (status === 'fresh user' || status === 'onboarding') return '/learner/onboarding';
      }
      const session = await fetchLearnerFirstSession(kind, id);
      if (session.access === 'open') return '/lms';
      if (kind === 'apprenticeship' && session.canBook === false) return '/learner/compliance-documents';
      return '/learner/first-session';
    }
    resolve().then(value => { if (!cancelled) setDestination(value); }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load your enrolment.'); });
    return () => { cancelled = true; };
  }, [kind, id, attempt]);
  if (error) return <div role="alert" className="p-6"><p>{error}</p><button onClick={() => setAttempt(value => value + 1)}>Try again</button></div>;
  return destination ? <Navigate to={destination} replace /> : <p role="status" className="p-6">Loading your enrolment…</p>;
}

export function Home() {
  const { auth } = useAuth();
  if (auth.account?.role === 'learner') return <LearnerStart />;
  if (auth.account && hasOwnEnrolment(auth.account) && !canManageEnrolment(auth.account)) return <LearnerStart />;
  if (auth.account?.role === 'employer') return <Navigate to={`/employers/${auth.account.subjectId}/documents`} replace />;
  return <Navigate to="/users" replace />;
}
