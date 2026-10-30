import { useEffect, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { fetchProgressReviewPreview } from '@/api/progressReviews';

/** Full-screen, in-page view of a generated deck. The server renders the deck
 * to PDF and the browser's own viewer shows it — the file never goes to an
 * outside viewing service. */
export default function PptxSlidesViewer({
  reviewId,
  title,
  onClose,
  onDownload,
  onEdit,
}: {
  reviewId: string;
  title: string;
  onClose: () => void;
  onDownload: () => void;
  /** Only the deck's owner can edit it. */
  onEdit?: () => void;
}) {
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    fetchProgressReviewPreview(reviewId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPdfUrl(objectUrl);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to open the slides.');
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [reviewId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-[rgba(10,12,24,0.9)] p-4" role="dialog" aria-label={title}>
      <div className="flex items-center justify-between gap-3 pb-3 text-white">
        <p className="truncate text-sm font-semibold">{title}</p>
        <div className="flex items-center gap-2">
          {onEdit && (
            <button type="button" onClick={onEdit} className="inline-flex h-9 items-center gap-2 rounded-lg bg-white px-3 text-[12px] font-semibold text-foreground-900 hover:bg-background-100">
              <AppIcon className="ri-edit-2-line" /> Edit
            </button>
          )}
          <button type="button" onClick={onDownload} className="inline-flex h-9 items-center gap-2 rounded-lg bg-white/10 px-3 text-[12px] font-semibold hover:bg-white/20">
            <AppIcon className="ri-download-line" /> Download PPTX
          </button>
          <button type="button" onClick={onClose} aria-label="Close slides" title="Close" className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-white text-lg text-slate-900 shadow-md ring-1 ring-slate-300 hover:bg-slate-100">
            <AppIcon className="ri-close-line" />
          </button>
        </div>
      </div>
      <div className="relative flex-1 overflow-hidden rounded-xl bg-background-100">
        {pdfUrl ? (
          <iframe title={title} src={pdfUrl} className="h-full w-full border-0" />
        ) : error ? (
          <div className="m-6 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-foreground-600">
            <AppIcon className="ri-loader-4-line animate-spin" /> Preparing slides… the first view can take a few seconds.
          </div>
        )}
      </div>
    </div>
  );
}
