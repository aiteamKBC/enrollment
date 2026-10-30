import { useEffect, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { useWizard } from "../WizardContext.helpers";
import { useToast } from "@/hooks/useToast.helpers";
import {
  deleteIlrEvidence,
  fetchIlrEvidence,
  getIlrEvidenceUrl,
  uploadIlrEvidence,
  type IlrEvidenceFile,
  type LearnerKind,
} from '@/api/extendedIlr';
import { EmptyState } from '../../components/ui';

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.doc,.docx';

/**
 * Proof of identification and residency for the ILR's Eligibility section.
 *
 * Each file is uploaded to Azure as soon as it is picked (quarantine -> scan ->
 * approved) and recorded, with its blob path, on the learner's Extended_ILR
 * row — so it is stored independently of the answers save. Once the ILR is
 * signed by both parties the server locks the files and this list goes read-only.
 */
export default function IlrEligibilityEvidence() {
  const { userId, isCommercial } = useWizard();
  const { error } = useToast();
  const kind: LearnerKind = isCommercial ? 'commercial' : 'apprenticeship';

  const [files, setFiles] = useState<IlrEvidenceFile[]>([]);
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchIlrEvidence(kind, userId)
      .then((res) => {
        if (cancelled) return;
        setFiles(res.results);
        setLocked(res.locked);
      })
      .catch((e) => {
        if (!cancelled) error('Could not load eligibility evidence', e instanceof Error ? e.message : 'Unexpected error');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, userId]);

  const upload = async (picked: File[]) => {
    if (picked.length === 0) return;
    setUploading(true);
    try {
      // One at a time, so a file the server refuses is reported by name and the
      // rest still go through.
      for (const file of picked) {
        try {
          const saved = await uploadIlrEvidence(kind, userId, file);
          setFiles((prev) => [...prev, saved]);
        } catch (e) {
          error(`Could not upload ${file.name}`, e instanceof Error ? e.message : 'Unexpected error');
        }
      }
    } finally {
      setUploading(false);
    }
  };

  const remove = async (file: IlrEvidenceFile) => {
    setBusyId(file.id);
    try {
      await deleteIlrEvidence(kind, userId, file.id);
      setFiles((prev) => prev.filter((f) => f.id !== file.id));
    } catch (e) {
      error(`Could not remove ${file.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setBusyId(null);
    }
  };

  const open = async (file: IlrEvidenceFile) => {
    setBusyId(file.id);
    try {
      window.open(await getIlrEvidenceUrl(kind, userId, file.id), '_blank', 'noopener');
    } catch (e) {
      error(`Could not open ${file.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-2">
      {loading ? (
        <EmptyState text="Loading evidence…" />
      ) : files.length === 0 ? (
        <EmptyState text="No evidence uploaded" />
      ) : (
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg">
          {files.map((f) => (
            <div key={f.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <button
                type="button"
                onClick={() => void open(f)}
                disabled={busyId === f.id}
                className="text-[12px] text-primary-600 hover:underline inline-flex items-center gap-1.5 min-w-0 cursor-pointer disabled:opacity-60"
              >
                <AppIcon className="ri-file-text-line shrink-0" />
                <span className="truncate">{f.filename}</span>
              </button>
              {!locked && (
                <button
                  type="button"
                  onClick={() => void remove(f)}
                  disabled={busyId === f.id}
                  aria-label={`Delete ${f.filename}`}
                  className="text-red-500 hover:text-red-600 transition-smooth cursor-pointer shrink-0 disabled:opacity-60"
                >
                  <AppIcon className="ri-delete-bin-line text-sm" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {locked ? (
        <p className="text-[12px] text-foreground-500">The Extended ILR has been signed, so this evidence can no longer be changed.</p>
      ) : (
        <label
          className={`relative inline-flex items-center gap-2 mt-2 px-3 py-1.5 text-[12px] bg-background-100 text-foreground-600 rounded-lg border border-background-200 hover:bg-background-200 transition-smooth ${uploading || loading ? 'opacity-60 pointer-events-none' : 'cursor-pointer'}`}
        >
          <AppIcon className={uploading ? 'ri-loader-4-line animate-spin' : 'ri-upload-2-line'} />
          {uploading ? 'Uploading…' : 'Upload file'}
          <input
            type="file"
            multiple
            accept={ACCEPT}
            disabled={uploading || loading}
            className="sr-only"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              // Cleared so picking the same file again still fires onChange.
              e.target.value = '';
              void upload(picked);
            }}
          />
        </label>
      )}
    </div>
  );
}
