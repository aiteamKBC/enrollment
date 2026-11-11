import { useEffect, useState, type FormEvent } from 'react';
import {
  approveEligibilityException, fetchEligibilityException, type EligibilityExceptionPreview,
} from '@/api/eligibilityApplications';
import { btnPrimary, btnSecondary, inputClass } from '../components/ui';

export function ExceptionPanel({ applicationId, email }: { applicationId: string; email: string }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<EligibilityExceptionPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reason, setReason] = useState('');
  const [confirmedEmail, setConfirmedEmail] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState('');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchEligibilityException(applicationId)
      .then(value => { if (!cancelled) { setPreview(value); setReason(value.overrideReason || ''); } })
      .catch((cause: unknown) => { if (!cancelled) setError(cause instanceof Error ? cause.message : 'Could not load the learner link.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, applicationId]);

  async function approve(event: FormEvent) {
    event.preventDefault();
    setError(''); setResult(''); setSaving(true);
    try {
      const outcome = await approveEligibilityException(applicationId, reason.trim(), confirmedEmail.trim());
      setResult(outcome.message || (outcome.invitation === 'alreadySent'
        ? 'The learner is linked and an invitation has already been sent.' : 'The learner is linked.'));
      setPreview(current => current && { ...current, linkedLearnerId: outcome.learnerId,
        overrideReason: reason.trim(), activationVerified: outcome.invitation === 'alreadyActivated' });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not approve the exception.');
    } finally {
      setSaving(false);
    }
  }

  return <section className="rounded-2xl border border-amber-200 bg-background-50 p-5">
    <h2 className="text-lg font-semibold">Onboarding exception</h2>
    <p className="mt-1 text-sm text-foreground-600">The original screening result remains unchanged. An administrator can authorise this applicant to enter onboarding.</p>
    {!open ? <button type="button" className={`${btnSecondary} mt-4`} onClick={() => setOpen(true)}>Review exception</button> : <>
      {loading && <p role="status" className="mt-3">Checking learner account…</p>}
      {error && <p role="alert" className="mt-3 text-red-700">{error}</p>}
      {preview && <div className="mt-4 space-y-3">
        {preview.linkedLearnerId && <p className="text-sm">Linked learner ID: {preview.linkedLearnerId}{preview.activationVerified ? ' · Account activated' : ' · Awaiting activation'}</p>}
        <p className="text-sm">{preview.existingLearner
          ? `Existing apprenticeship learner: ${preview.existingLearner.name} (${preview.existingLearner.email}). This account will be linked.`
          : 'No learner has this email. A new apprenticeship learner will be created and linked.'}</p>
        {!result && <form onSubmit={approve} className="space-y-3">
          <div><label htmlFor="exception-reason" className="mb-1 block text-sm font-medium">Reason for exception</label>
            <textarea id="exception-reason" className={inputClass} rows={3} minLength={10} maxLength={1000} required value={reason} onChange={e => setReason(e.target.value)} /></div>
          <div><label htmlFor="exception-email" className="mb-1 block text-sm font-medium">Confirm applicant email: {email}</label>
            <input id="exception-email" className={inputClass} type="email" autoComplete="off" required value={confirmedEmail} onChange={e => setConfirmedEmail(e.target.value)} /></div>
          <button type="submit" className={btnPrimary} disabled={saving || reason.trim().length < 10 || confirmedEmail.trim().toLowerCase() !== email.trim().toLowerCase()}>
            {saving ? 'Saving…' : 'Approve exception and invite learner'}
          </button>
        </form>}
        {result && <p role="status" className="rounded-lg bg-green-50 p-3 text-green-800">{result}</p>}
      </div>}
    </>}
  </section>;
}
