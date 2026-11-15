import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchEnrolmentBoard } from '@/api/enrolmentUsers';
import { useAuth } from '@/hooks/useAuth.helpers';
import { useMyLearner } from '@/hooks/useMyLearner';

/** Signed-in Fresh apprentices wait here until an invitation or staff release. */
export default function PendingOnboarding() {
  const { logout } = useAuth();
  const { id } = useMyLearner();
  const navigate = useNavigate();
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState('');

  async function checkAgain() {
    setChecking(true); setMessage('');
    try {
      const board = await fetchEnrolmentBoard(id);
      if ((board.programme.status || '').toLowerCase() !== 'fresh user') {
        navigate('/learner/onboarding', { replace: true });
      } else {
        setMessage('Your enrolment is still being prepared. Please check again later.');
      }
    } catch {
      setMessage('Could not check your status. Please try again.');
    } finally {
      setChecking(false);
    }
  }

  return <main className="min-h-screen bg-background-100 px-4 py-8">
    <div className="mx-auto max-w-2xl rounded-2xl border border-foreground-200 bg-background-50 p-6 shadow-sm">
      <p className="text-sm font-semibold text-primary-700">Enrolment Corner</p>
      <h1 className="mt-5 text-2xl font-semibold">Your enrolment is being prepared</h1>
      <p className="mt-3">Your account is ready, but onboarding is not available yet. We will let you know when you can continue.</p>
      <p className="mt-3">If you have a question about your application, contact <a className="text-primary-700 underline" href="mailto:office@kentbusinesscollege.org">Admissions</a>.</p>
      <div className="mt-6 flex flex-wrap gap-3">
        <button type="button" onClick={checkAgain} disabled={checking} className="rounded-xl bg-primary-700 px-4 py-2 text-white">{checking ? 'Checking…' : 'Check status'}</button>
        <button type="button" onClick={() => logout()} className="rounded-xl border border-foreground-200 px-4 py-2">Sign out</button>
      </div>
      {message && <p role="status" className="mt-4 text-sm">{message}</p>}
    </div>
  </main>;
}
