import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  fetchApplicationReview, recordApplicationReview,
  type ApplicationReview, type ReviewAction, type ReviewStatus,
} from '@/api/eligibilityApplications';
import { btnPrimary, btnSecondary, inputClass } from '../components/ui';
import { REVIEW_LABELS } from './reviewLabels';
const label = (value: string | null) => value == null ? 'Not reviewed' : value === 'pending_assessment' ? 'Pending assessment' : REVIEW_LABELS[value as ReviewStatus] || value;

function Confirmation({ action, busy, error, onConfirm, onCancel }: {
  action: ReviewAction; busy: boolean; error: string; onConfirm: () => void; onCancel: () => void;
}) {
  const title = useId();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.querySelector('button')?.focus();
    return () => { document.body.style.overflow = overflow; if (previous?.isConnected) previous.focus(); };
  }, []);
  return createPortal(<div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 p-4">
    <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={title} className="w-full max-w-lg space-y-4 rounded-2xl bg-background-50 p-6 shadow-xl"
      onKeyDown={event => {
        if (event.key === 'Escape' && !busy) { event.preventDefault(); onCancel(); }
        if (event.key === 'Tab') {
          const buttons = panel.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
          const first = buttons[0], last = buttons[buttons.length - 1];
          if (!first) { event.preventDefault(); return; }
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      }}>
      <h2 id={title} className="text-lg font-semibold">Record {REVIEW_LABELS[action.action].toLowerCase()}?</h2>
      <p>This records an audited staff decision. No learner account, invitation or email will be created.</p>
      <p className="whitespace-pre-wrap break-words rounded-lg bg-background-100 p-3 text-sm">{action.notes}</p>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex justify-end gap-2">
        <button className={btnSecondary} type="button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className={btnPrimary} type="button" onClick={onConfirm} disabled={busy}>{busy ? 'Recording…' : 'Record decision'}</button>
      </div>
    </div>
  </div>, document.body);
}

export function ReviewPanel({ id }: { id: string }) {
  const [review, setReview] = useState<ApplicationReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [decisionError, setDecisionError] = useState('');
  const [notes, setNotes] = useState('');
  const [action, setAction] = useState<ReviewAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [success, setSuccess] = useState('');
  const [attempt, setAttempt] = useState(0);
  const inFlight = useRef(false);
  const notesId = useId();
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setLoadError(''); setReview(null); setAction(null); setConflict(false);
    fetchApplicationReview(id).then(result => { if (!cancelled) setReview(result); })
      .catch(reason => { if (!cancelled) setLoadError(reason instanceof Error ? reason.message : 'Could not load review history.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id, attempt]);
  const choose = (decision: ReviewStatus) => {
    setSuccess('');
    if (!notes.trim()) { setDecisionError('Decision notes are required.'); return; }
    setDecisionError('');
    setAction({ action: decision, notes: notes.trim(), expectedVersion: review!.version, requestId: crypto.randomUUID() });
  };
  const record = async () => {
    if (!action || inFlight.current) return;
    inFlight.current = true; setBusy(true); setDecisionError('');
    try {
      const result = await recordApplicationReview(id, action);
      setReview(result); setAction(null); setNotes('');
      setSuccess('Review recorded. No account, invitation or email was created.');
    } catch (reason) {
      const status = (reason as { status?: number })?.status;
      if (status === 409) { setConflict(true); setAction(null); }
      setDecisionError(reason instanceof Error ? reason.message : 'Could not record the review. Retry this decision.');
      // A network/503 retry retains this exact request id and payload.
    } finally { inFlight.current = false; setBusy(false); }
  };
  return <section className="space-y-4 rounded-2xl border border-foreground-200 bg-background-50 p-5" aria-labelledby="manual-review-heading">
    <h2 id="manual-review-heading" className="font-semibold">Staff review decision</h2>
    <p className="text-sm text-foreground-500">Manual review is separate from the original screening result. Approval awaits a separate invitation action.</p>
    {loading && <p role="status">Loading review history…</p>}
    {loadError && <div role="alert"><p>{loadError}</p><button type="button" className={btnSecondary} onClick={() => setAttempt(value => value + 1)}>Retry review history</button></div>}
    {review && <>
      <p>Current review status: <strong>{label(review.status)}</strong></p>
      {!review.available ? <p>Review history is not available in this environment.</p> : !review.enabled && <p>Review decisions are disabled in this environment.</p>}
      {success && <p role="status" className="text-emerald-700">{success}</p>}
      {decisionError && !action && <p role="alert" className="text-red-700">{decisionError}</p>}
      {conflict ? <button type="button" className={btnSecondary} onClick={() => { setDecisionError(''); setAttempt(value => value + 1); }}>Reload review history</button> : review.enabled && review.allowedActions.length > 0 && <div className="space-y-3">
        <label htmlFor={notesId} className="block text-sm font-semibold">Decision notes (required; staff only)</label>
        <textarea id={notesId} className={inputClass} rows={4} maxLength={2000} value={notes} disabled={busy} onChange={event => setNotes(event.target.value)} />
        <div className="flex flex-wrap gap-2">{review.allowedActions.map(decision => <button type="button" key={decision} className={btnSecondary} disabled={busy} onClick={() => choose(decision)}>{REVIEW_LABELS[decision]}</button>)}</div>
      </div>}
      <h3 className="font-semibold">Review history</h3>
      {review.history.length === 0 ? <p className="text-sm text-foreground-500">No staff review decisions recorded.</p> : <ol className="space-y-3">
        {review.history.map(entry => <li key={entry.id} className="rounded-xl border border-foreground-200 p-4 text-sm">
          <p className="font-semibold">{label(entry.previousStatus)} → {label(entry.status)}</p>
          <p className="mt-1 text-foreground-500">{entry.reviewerName} · Reviewer #{entry.reviewerId} · {new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/London' }).format(new Date(entry.decidedAt))} (UK time)</p>
          <p className="mt-2 whitespace-pre-wrap break-words">{entry.notes}</p>
        </li>)}
      </ol>}
    </>}
    {action && <Confirmation action={action} busy={busy} error={decisionError} onConfirm={() => void record()} onCancel={() => { setAction(null); setDecisionError(''); }} />}
  </section>;
}
