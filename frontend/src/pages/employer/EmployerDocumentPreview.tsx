import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Download, ExternalLink, FileText, LoaderCircle, X } from 'lucide-react';
import { LearnerReviewInstanceForm } from '@/pages/learner/reviews/LearnerReviewInstanceForm';
import { AssignmentPdfPreview } from '@/pages/learner/monthly-submission/AssignmentPdfPreview';
import { loadEmployerDocumentPreview, type DocumentPreview } from './employerDocumentPreviewData';
import type { SigningTarget } from './useEmployerSigning';

export function EmployerDocumentPreview({ employerId, target, onClose }: {
  employerId: string; target: SigningTarget; onClose: () => void;
}) {
  const [content, setContent] = useState<DocumentPreview | null>(null);
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const modal = dialog.current!;
    modal.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      modal.close();
      document.body.style.overflow = overflow;
      previousFocus?.focus();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    setContent(null);
    setUrl('');
    setError('');
    loadEmployerDocumentPreview(employerId, target, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.kind === 'pdf') {
        objectUrl = URL.createObjectURL(value.blob);
        setUrl(objectUrl);
      }
      setContent(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load the document.');
    });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [employerId, target, attempt]);

  return createPortal(
    <dialog ref={dialog} aria-labelledby={titleId} onCancel={onClose}
      className="m-auto h-[92dvh] max-h-[92dvh] w-[96vw] max-w-6xl overflow-hidden rounded-2xl border border-foreground-200 bg-white p-0 text-foreground-900 shadow-2xl backdrop:bg-black/50 backdrop:backdrop-blur-sm"
      onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex flex-wrap items-center gap-3 border-b border-foreground-100 px-4 py-3">
          <FileText className="shrink-0 text-primary-600" aria-hidden />
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="break-words text-base font-semibold">{target.item.label}</h2>
            <p className="text-xs text-foreground-500">Document preview · Read only</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close document preview"
            className="rounded-lg p-2 hover:bg-primary-50 focus-visible:outline focus-visible:outline-primary-600"><X aria-hidden /></button>
          {content?.kind === 'pdf' && url && <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
            <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 rounded-lg border border-primary-200 px-3 py-2 text-sm text-primary-700">
              <ExternalLink size={16} aria-hidden />Open in new tab
            </a>
            <a href={url} download={content.filename} className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-3 py-2 text-sm text-white">
              <Download size={16} aria-hidden />Download
            </a>
          </div>}
        </header>
        <div className="min-h-0 flex-1 overflow-auto bg-background-50 p-3 sm:p-5">
          {error ? <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
            <p className="font-semibold">Could not open the document</p><p className="mt-1">{error}</p>
            <button type="button" onClick={() => setAttempt(value => value + 1)} className="mt-3 rounded-lg border border-red-300 px-3 py-2">Retry</button>
          </div> : !content ? <div role="status" className="flex items-center justify-center gap-2 p-12 text-primary-700">
            <LoaderCircle className="animate-spin" aria-hidden />Loading preview…
          </div> : content.kind === 'pdf'
            ? <AssignmentPdfPreview url={url} filename={content.filename} label={`${target.item.label} preview`} />
            : <>
              <p role="status" className="mb-4 rounded-xl border border-primary-100 bg-primary-50 p-3 text-sm text-primary-800">{content.unavailableReason}</p>
              <LearnerReviewInstanceForm definition={content.definition} viewerRole="employer" signatoryName="" />
            </>}
        </div>
      </div>
    </dialog>, document.body,
  );
}
