import { useCallback, useEffect, useRef, useState } from 'react';
import type { LearnerKind } from '@/api/learnerDetail';
import { AppIcon } from '@/components/feature/AppIcon';
import {
  fetchEvidence, uploadEvidence, getEvidenceDownloadUrl, deleteEvidence,
  type EvidenceRecord, type EvidenceTrainingPlanDetails,
} from '@/api/evidence';

/* ═══════════════════════════════════════════════════════
   ASSIGNMENT EVIDENCE — file upload for assignment components.
   Uploads land in Azure quarantine, get scanned, then land as
   approved/rejected. Files are listed with their lifecycle
   status; approved files can be downloaded via a short-lived
   SAS URL.
   ═══════════════════════════════════════════════════════ */

// Extensions as well as types: a browser with no mapping for .docx offers the
// file only if the extension is listed, and the server accepts a generic type
// on the strength of the extension for the same reason. Keep in step with
// learner_api/evidence.py ALLOWED_TYPES / ALLOWED_EXTENSIONS.
const ACCEPT = [
  'application/pdf', '.pdf',
  'image/png', '.png',
  'image/jpeg', '.jpg', '.jpeg',
  'video/mp4', '.mp4',
  'application/msword', '.doc',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx',
  'application/vnd.ms-powerpoint', '.ppt',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx',
  'application/vnd.openxmlformats-officedocument.presentationml.slideshow', '.ppsx',
].join(',');
const MAX_BYTES = 50 * 1024 * 1024;

const STATUS_META: Record<string, { label: string; bg: string; color: string; icon: string }> = {
  pending: { label: 'Scanning…', bg: 'bg-amber-100', color: 'text-amber-700', icon: 'ri-loader-4-line' },
  // The backend lifecycle name remains `approved` because it means the blob
  // reached the downloadable Azure container. It is not a tutor decision, so
  // the learner-facing label must describe the actual event.
  approved: { label: 'Uploaded', bg: 'bg-emerald-100', color: 'text-emerald-700', icon: 'ri-checkbox-circle-line' },
  rejected: { label: 'Rejected', bg: 'bg-red-100', color: 'text-red-700', icon: 'ri-close-circle-line' },
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function AssignmentEvidence({
  kind, learnerId, componentId, trainingPlanDetails, onUploaded, onFileSelected, inputId, showPanel = true, readOnly = false,
}: {
  kind: LearnerKind;
  learnerId: string;
  componentId: string;
  trainingPlanDetails: EvidenceTrainingPlanDetails;
  /** Fired after a successful upload so callers can re-check completion criteria. */
  onUploaded?: (files: EvidenceRecord[]) => void;
  /** Fired immediately after the learner chooses a file, so external triggers can swap their label. */
  onFileSelected?: (fileName: string) => void;
  /** Lets a button elsewhere on the same page open this uploader directly. */
  inputId?: string;
  /** Keep only the file input mounted when the page supplies its own trigger. */
  showPanel?: boolean;
  /** Show existing files without allowing upload or removal. */
  readOnly?: boolean;
}) {
  const [files, setFiles] = useState<EvidenceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Callers pass an inline arrow, so keep it in a ref: depending on the prop
  // directly would give `load` a new identity every render and re-fire the
  // mount effect in a loop.
  const onUploadedRef = useRef(onUploaded);
  useEffect(() => { onUploadedRef.current = onUploaded; }, [onUploaded]);

  const load = useCallback(() => {
    setLoading(true);
    fetchEvidence(kind, learnerId, { sectionRef: componentId })
      .then((rows) => {
        setFiles((prev) => {
          // A file finishing its scan changes whether the completion criteria
          // are met, so tell the caller when the approved count moves.
          const was = prev.filter((f) => f.status === 'approved').length;
          const now = rows.filter((f) => f.status === 'approved').length;
          if (now !== was) onUploadedRef.current?.(rows);
          return rows;
        });
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load evidence.'))
      .finally(() => setLoading(false));
  }, [kind, learnerId, componentId]);

  useEffect(() => { load(); }, [load]);

  // Poll while any file is still being scanned, so status flips pending -> approved/rejected.
  useEffect(() => {
    if (!files.some((f) => f.status === 'pending')) return;
    const refreshPendingFiles = () => {
      if (document.visibilityState !== 'hidden') load();
    };
    const t = window.setInterval(refreshPendingFiles, 10000);
    return () => window.clearInterval(t);
  }, [files, load]);

  const handleFile = async (file: File) => {
    setError(null);
    onFileSelected?.(file.name);
    if (file.size > MAX_BYTES) {
      setError('File exceeds the 50 MB size limit.');
      return;
    }
    setUploading(true);
    try {
      await uploadEvidence(kind, learnerId, file, componentId, trainingPlanDetails);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed.');
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  // Removing a file is destructive and cannot be undone, so the row asks first
  // rather than deleting on a single stray click.
  const handleDelete = async (fileId: string) => {
    setError(null);
    setDeletingId(fileId);
    try {
      await deleteEvidence(kind, learnerId, fileId);
      setConfirmingId(null);
      // Re-read rather than splicing locally: `load` is what tells the page the
      // approved count moved, which is what re-locks the Finish button.
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the file.');
    } finally {
      setDeletingId(null);
    }
  };

  const handleDownload = async (fileId: string) => {
    try {
      const url = await getEvidenceDownloadUrl(kind, learnerId, fileId);
      window.open(url, '_blank', 'noreferrer');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not get download link.');
    }
  };

  if (!showPanel) {
    return (
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={ACCEPT}
        disabled={uploading || readOnly}
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
      />
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-2">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Upload evidence</p>
        {!readOnly && (
          <label className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-600 hover:text-emerald-700 cursor-pointer">
            <AppIcon className="ri-upload-2-line" /> {uploading ? 'Uploading…' : 'Choose file'}
            <input
              ref={inputRef}
              id={inputId}
              type="file"
              accept={ACCEPT}
              disabled={uploading}
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
            />
          </label>
        )}
      </div>
      <p className="text-[11px] text-foreground-400 mb-3">Word, PowerPoint, PDF, PNG, JPEG or MP4, up to 50 MB.</p>

      {error && <p className="text-xs font-medium text-red-600 mb-3">{error}</p>}

      {loading ? (
        <p className="text-xs text-foreground-400">Loading evidence…</p>
      ) : files.length === 0 ? (
        <p className="text-xs text-foreground-400">No evidence uploaded yet.</p>
      ) : (
        <ul className="space-y-2">
          {files.map((f) => {
            const meta = STATUS_META[f.status] || STATUS_META.pending;
            return (
              <li key={f.id} className="flex items-center gap-2.5 rounded-lg border border-background-200 bg-background-50 px-3 py-2">
                <span className="w-8 h-8 rounded-lg bg-white border border-background-200 flex items-center justify-center shrink-0">
                  <AppIcon className="ri-file-line text-foreground-500 text-sm" />
                </span>
                {f.status === 'approved' ? (
                  <button
                    onClick={() => handleDownload(f.id)}
                    className="flex-1 min-w-0 text-left group cursor-pointer"
                    title="Open this file"
                  >
                    <span className="block text-[13px] font-semibold text-foreground-800 truncate group-hover:text-emerald-700 group-hover:underline">{f.filename}</span>
                    <span className="block text-[11px] text-foreground-400">{formatSize(f.sizeBytes)}</span>
                  </button>
                ) : (
                  <span className="flex-1 min-w-0">
                    <span className="block text-[13px] font-semibold text-foreground-800 truncate">{f.filename}</span>
                    <span className="block text-[11px] text-foreground-400">{formatSize(f.sizeBytes)}</span>
                  </span>
                )}
                <span className={`shrink-0 inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full ${meta.bg} ${meta.color}`}>
                  <AppIcon className={meta.icon} /> {meta.label}
                </span>
                {f.status === 'approved' && (
                  <button
                    onClick={() => handleDownload(f.id)}
                    className="shrink-0 text-foreground-400 hover:text-emerald-600 transition-colors cursor-pointer"
                    title="Open in a new tab"
                  >
                    <AppIcon className="ri-external-link-line" />
                  </button>
                )}
                {/* Remove, so a learner can correct a wrong file: deleting the
                    one that is here leaves the uploader ready for the right
                    one, which is the whole reupload path. */}
                {!readOnly && (confirmingId === f.id ? (
                  <span className="shrink-0 inline-flex items-center gap-1.5">
                    <button
                      onClick={() => handleDelete(f.id)}
                      disabled={deletingId === f.id}
                      className="rounded-md bg-red-600 px-2 py-1 text-[11px] font-semibold text-white transition-colors enabled:cursor-pointer enabled:hover:bg-red-700 disabled:opacity-60"
                    >
                      {deletingId === f.id ? 'Removing…' : 'Remove'}
                    </button>
                    <button
                      onClick={() => setConfirmingId(null)}
                      disabled={deletingId === f.id}
                      className="rounded-md border border-background-300 px-2 py-1 text-[11px] font-semibold text-foreground-600 transition-colors enabled:cursor-pointer enabled:hover:bg-background-100 disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => { setError(null); setConfirmingId(f.id); }}
                    className="shrink-0 text-foreground-400 hover:text-red-600 transition-colors cursor-pointer"
                    title="Remove this file"
                  >
                    <AppIcon className="ri-delete-bin-line" />
                  </button>
                ))}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
