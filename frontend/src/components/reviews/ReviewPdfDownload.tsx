import { useRef, useState } from 'react';
import { Download } from 'lucide-react';

export function ReviewPdfDownload({ availability, onDownload, label = 'Download signed PDF' }: {
  availability?: { available: boolean; reason: string } | null;
  onDownload?: () => Promise<void>;
  label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  if (!availability || !onDownload) return null;

  async function download() {
    if (!availability?.available || !onDownload || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    try { await onDownload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not download the signed PDF.'); }
    finally { inFlight.current = false; setBusy(false); }
  }

  return <div className="space-y-2">
    <button type="button" onClick={() => { void download(); }} disabled={busy || !availability.available}
      className="inline-flex items-center gap-2 rounded-[4px] border border-[#204d66] bg-[#204d66] px-4 py-2.5 text-[13px] font-bold text-white shadow-sm transition hover:border-[#15384c] hover:bg-[#15384c] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#204d66] disabled:cursor-not-allowed disabled:border-foreground-300 disabled:bg-foreground-300 disabled:opacity-60">
      <Download size={17}/>{busy ? 'Preparing PDF...' : label}
    </button>
    {!availability.available && <p className="text-sm text-foreground-600">{availability.reason}</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
  </div>;
}
