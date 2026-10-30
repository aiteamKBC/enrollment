import { useState } from 'react';
import type { ReviewInstanceFormDefinition } from '@/api/reviewInstances';

export function MigratedReviewHistory({ entries, onDownload, signatureFree = false }: {
  entries: ReviewInstanceFormDefinition['reopenHistory'];
  onDownload: (number: number) => Promise<void>;
  signatureFree?: boolean;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState('');
  if (!entries?.length) return null;

  async function download(number: number) {
    if (busy !== null) return;
    setBusy(number);
    setError('');
    try {
      await onDownload(number);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The archived PDF could not be downloaded.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <details className="rounded-xl border border-background-200 bg-white p-4">
      <summary className="cursor-pointer font-semibold text-foreground-900">Previous review versions ({entries.length})</summary>
      <p className="mt-2 text-sm text-foreground-600">These versions retain the answers and any signatures recorded before each correction.</p>
      <ul className="mt-3 divide-y divide-background-200">
        {entries.map(entry => (
          <li key={entry.number} className="flex flex-wrap items-start justify-between gap-3 py-3 text-sm">
            <div className="min-w-0 flex-1">
              <p className="font-semibold">Version {entry.number}</p>
              <p className="break-words text-foreground-600">
                Reopened {new Date(entry.reopenedAt).toLocaleString('en-GB', { timeZone: 'Europe/London' })} (UK) by {entry.reopenedBy}
              </p>
              <p className="mt-1 capitalize">{entry.reasonCode.replaceAll('-', ' ')}</p>
              {entry.note && <p className="mt-1 whitespace-pre-wrap break-words">{entry.note}</p>}
            </div>
            {entry.pdfAvailable ? (
              <button type="button" disabled={busy !== null} onClick={() => void download(entry.number)}
                className="min-h-11 rounded-lg border border-primary-200 px-3 py-2 font-semibold text-primary-700 disabled:opacity-50">
                {busy === entry.number ? 'Downloading…' : `Download version ${entry.number} PDF`}
              </button>
            ) : <span className="text-foreground-500">{signatureFree ? 'Not completed; no PDF' : 'Not completed; no signed PDF'}</span>}
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
    </details>
  );
}
