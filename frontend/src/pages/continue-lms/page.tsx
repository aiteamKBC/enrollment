import { useState } from 'react';
import { useAuth } from '@/hooks/useAuth.helpers';
import { useLocation } from 'react-router-dom';
import { hasOwnEnrolment } from '@/lib/enrollmentAccess';

export default function ContinueInLms() {
  const { auth } = useAuth();
  const { search } = useLocation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const raw = import.meta.env.VITE_LMS_URL || '';
  let url = '';
  try {
    const parsed = new URL(raw);
    if (['http:', 'https:'].includes(parsed.protocol) && parsed.origin !== window.location.origin) url = parsed.href;
  } catch { /* The missing destination is displayed below. */ }
  const isLearner = auth.account?.role === 'learner' || Boolean(auth.account && hasOwnEnrolment(auth.account) && new URLSearchParams(search).get('self') === '1');
  async function prepare() {
    setBusy(true);
    setError('');
    try {
      const csrfResponse = await fetch('/learner_api/enrollment-csrf/', { credentials: 'include' });
      const csrf = await csrfResponse.json() as { csrfToken?: string };
      if (!csrfResponse.ok || !csrf.csrfToken) throw new Error('Your session could not be verified. Please sign in again.');
      const response = await fetch('/learner_api/lms-handoff/', {
        method: 'POST', credentials: 'include', headers: { 'X-CSRFToken': csrf.csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
      });
      const result = await response.json() as { status?: string; error?: string };
      if (!response.ok || result.status !== 'accepted') throw new Error(result.error || 'The LMS has not confirmed your transfer. Please retry.');
      setConfirmed(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The LMS could not be reached. Please retry.');
    } finally { setBusy(false); }
  }
  return <main className="mx-auto max-w-xl p-8 space-y-4">
    <h1 className="font-heading text-2xl font-semibold">Continue your learning</h1>
    <p>Your courses, learning materials and study progress are in the LMS.</p>
    {!url ? <p role="alert">The learning portal link has not been configured. Contact your enrolment team.</p>
      : isLearner && !confirmed
        ? <button disabled={busy} onClick={() => void prepare()} className="rounded-lg bg-primary-600 px-5 py-3 text-white disabled:opacity-50">{busy ? 'Preparing your LMS access…' : 'Continue to LMS'}</button>
        : <><p>Sign in to the LMS to start learning.</p><a className="inline-block rounded-lg bg-primary-600 px-5 py-3 text-white" href={url}>Open LMS</a></>}
    {error && <p role="alert">{error}</p>}
  </main>;
}
