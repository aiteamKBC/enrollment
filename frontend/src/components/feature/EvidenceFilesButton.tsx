import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { LearnerKind } from '@/api/learnerDetail';
import { fetchEvidence, getEvidenceDownloadUrl, type EvidenceRecord } from '@/api/evidence';
import { AppIcon } from '@/components/feature/AppIcon';

export interface EvidencePreview {
  file: EvidenceRecord;
  url: string;
}

export function EvidencePreviewModal({ preview, onClose }: { preview: EvidencePreview; onClose: () => void }) {
  const fileName = preview.file.filename.toLowerCase();
  const isImage = /\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i.test(fileName);
  const isVideo = /\.(mp4|webm|ogg|mov|m4v)(\?|$)/i.test(fileName);

  return createPortal(
    <div className="fixed inset-0 z-[140] flex items-center justify-center bg-foreground-950/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-background-300 bg-white shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-background-200 px-4 py-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-primary-50 text-primary-700">
            <AppIcon className="ri-attachment-2" />
          </span>
          <div className="min-w-0 flex-1 text-left">
            <p className="truncate text-sm font-bold text-foreground-900">{preview.file.filename}</p>
            <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Evidence preview</p>
          </div>
          <a
            href={preview.url}
            target="_blank"
            rel="noreferrer"
            className="hidden rounded-xl border border-background-300 px-3 py-2 text-xs font-semibold text-foreground-600 hover:bg-background-50 sm:inline-flex"
          >
            Open
          </a>
          <button
            type="button"
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded-xl border border-background-300 text-foreground-500 transition-colors hover:bg-background-50 hover:text-foreground-900"
            aria-label="Close evidence preview"
          >
            <AppIcon className="ri-close-line text-lg" />
          </button>
        </div>
        <div className="min-h-0 flex-1 bg-background-950/95 p-4">
          {isImage ? (
            <img src={preview.url} alt={preview.file.filename} className="mx-auto max-h-[76vh] max-w-full rounded-lg object-contain" />
          ) : isVideo ? (
            <video src={preview.url} controls className="mx-auto max-h-[76vh] max-w-full rounded-lg bg-black" />
          ) : (
            <iframe title={preview.file.filename} src={preview.url} className="h-[76vh] w-full rounded-lg bg-white" />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ═══════════════════════════════════════════════════════
   EVIDENCE FILES BUTTON — compact "view uploaded file(s)"
   affordance for an assignment row in the training plan.
   Renders nothing until we know the learner actually has
   evidence for this component, so rows stay uncluttered.
   Approved files open via a short-lived Azure SAS URL.
   ═══════════════════════════════════════════════════════ */

export function EvidenceFilesButton({ kind, learnerId, componentId }: {
  kind: LearnerKind;
  learnerId: string;
  componentId: string;
}) {
  const [files, setFiles] = useState<EvidenceRecord[]>([]);
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<EvidencePreview | null>(null);
  // The menu is portalled to <body>: the training-plan week/module cards use
  // `overflow-hidden` for their rounded corners, which would otherwise clip an
  // absolutely-positioned dropdown.
  const [menuRect, setMenuRect] = useState({ left: 0, top: 0 });
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchEvidence(kind, learnerId, { sectionRef: componentId })
      .then((rows) => { if (!cancelled) setFiles(rows); })
      .catch(() => { /* a row is not the place to surface a load failure */ });
    return () => { cancelled = true; };
  }, [kind, learnerId, componentId]);

  const MENU_WIDTH = 256;

  useEffect(() => {
    if (!open) return undefined;
    const positionMenu = () => {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      // Right-align to the trigger (it sits at the row's right edge), clamped
      // so the menu can never run off the left of the viewport.
      setMenuRect({ left: Math.max(8, rect.right - MENU_WIDTH), top: rect.bottom + 6 });
    };
    positionMenu();
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('resize', positionMenu);
    window.addEventListener('scroll', positionMenu, true);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('resize', positionMenu);
      window.removeEventListener('scroll', positionMenu, true);
    };
  }, [open]);

  if (files.length === 0) return null;

  const openFile = async (file: EvidenceRecord) => {
    setError(null);
    setBusyId(file.id);
    try {
      const url = await getEvidenceDownloadUrl(kind, learnerId, file.id);
      setPreview({ file, url });
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open the file.');
    } finally {
      setBusyId(null);
    }
  };

  const approved = files.filter((f) => f.status === 'approved');

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        onClick={(e) => {
          e.stopPropagation();
          // One file that's ready to read → skip the menu and just open it.
          if (files.length === 1 && approved.length === 1) openFile(approved[0]);
          else setOpen((s) => !s);
        }}
        className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-3 py-1.5 rounded-lg border border-background-300 bg-white text-foreground-600 hover:bg-background-50 transition-colors cursor-pointer"
        title="View uploaded evidence"
      >
        <AppIcon className="ri-attachment-2 text-[10px]" />
        {files.length === 1 ? 'View file' : `${files.length} files`}
      </button>

      {open && createPortal(
        <div
          ref={menuRef}
          className="fixed z-[120] rounded-xl border border-background-300 bg-white shadow-xl overflow-hidden"
          style={{ left: menuRect.left, top: menuRect.top, width: MENU_WIDTH }}
        >
          <ul className="divide-y divide-background-200 max-h-64 overflow-y-auto">
            {files.map((f) => {
              const isApproved = f.status === 'approved';
              return (
                <li key={f.id}>
                  <button
                    disabled={!isApproved || busyId === f.id}
                    onClick={(e) => { e.stopPropagation(); openFile(f); }}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-left transition-colors ${
                      isApproved ? 'hover:bg-background-50 cursor-pointer' : 'cursor-default opacity-60'
                    }`}
                  >
                    <AppIcon className="ri-file-line text-foreground-400 text-sm shrink-0" />
                    <span className="flex-1 min-w-0">
                      <span className="block text-[12px] font-semibold text-foreground-800 truncate">{f.filename}</span>
                      {!isApproved && (
                        <span className="block text-[10px] text-foreground-400">
                          {f.status === 'pending' ? 'Scanning…' : 'Rejected'}
                        </span>
                      )}
                    </span>
                    {isApproved && <AppIcon className="ri-external-link-line text-foreground-400 text-xs shrink-0" />}
                  </button>
                </li>
              );
            })}
          </ul>
          {error && <p className="px-3 py-2 text-[11px] font-medium text-red-600 border-t border-background-200">{error}</p>}
        </div>,
        document.body,
      )}
      {preview && <EvidencePreviewModal preview={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
