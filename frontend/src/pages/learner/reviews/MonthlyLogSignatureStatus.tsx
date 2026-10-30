import { useRef, useState } from 'react';
import type { MonthlyLogSignatureSync } from '@/api/learnerCalendar';

export function MonthlyLogSignatureStatus({ sync, onRetry, previousReviewVersion = false }: {
  sync: MonthlyLogSignatureSync;
  onRetry?: () => Promise<void>;
  previousReviewVersion?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const needsRetry = sync.status === 'failed' || sync.status === 'pending';
  const month = sync.month
    ? new Date(`${sync.month}-01T00:00:00`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
    : 'this month';
  const message = needsRetry
    ? `Your MCM signature is saved. The Monthly Log for ${month} still needs its signature.`
    : sync.status === 'already-synced'
      ? `The Monthly Log for ${month} already has your signature. Its existing signature was kept.`
      : sync.status === 'skipped'
        ? `Your MCM signature is saved. The historical Monthly Log for ${month} was kept unchanged.`
        : `Your signature is saved on the MCM and the Monthly Log for ${month}.`;

  async function retry() {
    if (!onRetry || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    try {
      await onRetry();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The Monthly Log could not be signed. Please try again.');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div className={`space-y-2 rounded-xl border p-4 text-sm ${needsRetry ? 'border-amber-200 bg-amber-50 text-amber-950' : 'border-emerald-200 bg-emerald-50 text-emerald-950'}`}>
      <p role={needsRetry ? 'alert' : 'status'}>{previousReviewVersion
        ? `This Monthly Log signature request belongs to the previous review version. ${needsRetry ? `The Monthly Log for ${month} still needs its signature.` : `The Monthly Log for ${month} was kept unchanged.`} You must sign the corrected review again when it is submitted.`
        : message}</p>
      {needsRetry && onRetry && <button type="button" disabled={busy} onClick={() => { void retry(); }} className="min-h-11 rounded-lg border border-amber-300 bg-white px-4 py-2 font-semibold disabled:opacity-50">
        {busy ? 'Retrying Monthly Log…' : 'Retry Monthly Log signature'}
      </button>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
