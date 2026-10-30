import { useEffect, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { useToast } from "@/hooks/useToast.helpers";
import {
  deleteCustomUpload,
  fetchCustomUploads,
  getCustomUploadUrl,
  uploadCustomFile,
  type CustomUpload,
} from '@/api/wizardLayout';
import { EmptyState, FieldRow } from '../../components/ui';
import { LabeledInput, LabeledSelect } from '../steps/fields';
import { FieldError } from '../stepErrors';
import { missingMessage, useMissing } from "../stepErrors.helpers";
import { useWizard } from "../WizardContext.helpers";
import { isRequired, itemLabel } from './resolve';
import type { CustomUploadRef, LayoutItem } from './types';

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.doc,.docx';

/**
 * One field added in the wizard builder.
 *
 * Text, number and dropdown answers travel with the draft (draft.custom) and
 * are projected into the field's own column on save. Uploads go to Azure the
 * moment they are picked, like every other wizard upload, and are recorded in
 * the field's column by the upload endpoint; the draft keeps only their names
 * so the required check can see them.
 */
export function CustomField({ item }: { item: LayoutItem }) {
  const { draft, setCustom } = useWizard();
  const label = itemLabel(item);
  const required = isRequired(item);
  const raw = draft.custom?.[item.key];
  const value = typeof raw === 'string' ? raw : '';
  const helper = item.helpText || undefined;

  if (item.type === 'upload') return <CustomUploadField item={item} label={label} required={required} />;

  if (item.type === 'dropdown') {
    return (
      <>
        <LabeledSelect
          label={label}
          required={required}
          missingKey={label}
          value={value}
          options={item.options ?? []}
          onChange={(v) => setCustom(item.key, v)}
        />
        {helper && <p className="-mt-1.5 pb-2 text-[11px] text-foreground-400">{helper}</p>}
      </>
    );
  }

  return (
    <LabeledInput
      label={label}
      type={item.type === 'number' ? 'number' : 'text'}
      required={required}
      missingKey={label}
      value={value}
      helper={helper}
      onChange={(v) => setCustom(item.key, v)}
    />
  );
}

function CustomUploadField({ item, label, required }: { item: LayoutItem; label: string; required: boolean }) {
  const { userId, isCommercial, draft, setCustom } = useWizard();
  const { error } = useToast();
  const kind = isCommercial ? 'commercial' : 'apprenticeship';
  const [files, setFiles] = useState<CustomUpload[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const missing = useMissing(label);

  // What the draft holds for this field, so the required check sees the files.
  const syncDraft = (next: CustomUpload[]) => {
    const refs: CustomUploadRef[] = next.map((f) => ({ id: f.id, filename: f.filename }));
    const current = draft.custom?.[item.key];
    const same = Array.isArray(current) && current.length === refs.length && current.every((r, i) => r.id === refs[i].id);
    if (!same) setCustom(item.key, refs);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchCustomUploads(kind, userId, item.key)
      .then((res) => {
        if (cancelled) return;
        setFiles(res);
        syncDraft(res);
      })
      .catch((e) => { if (!cancelled) error(`Could not load ${label}`, e instanceof Error ? e.message : 'Unexpected error'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, userId, item.key]);

  const upload = async (picked: File[]) => {
    if (picked.length === 0) return;
    setUploading(true);
    let next = files;
    try {
      // One at a time, so a refused file is reported by name and the rest still go.
      for (const file of picked) {
        try {
          const saved = await uploadCustomFile(kind, userId, item.key, file);
          next = [...next, saved];
          setFiles(next);
        } catch (e) {
          error(`Could not upload ${file.name}`, e instanceof Error ? e.message : 'Unexpected error');
        }
      }
    } finally {
      setUploading(false);
      syncDraft(next);
    }
  };

  const remove = async (file: CustomUpload) => {
    setBusyId(file.id);
    try {
      await deleteCustomUpload(kind, userId, item.key, file.id);
      const next = files.filter((f) => f.id !== file.id);
      setFiles(next);
      syncDraft(next);
    } catch (e) {
      error(`Could not remove ${file.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setBusyId(null);
    }
  };

  const open = async (file: CustomUpload) => {
    setBusyId(file.id);
    try {
      window.open(await getCustomUploadUrl(kind, userId, item.key, file.id), '_blank', 'noopener');
    } catch (e) {
      error(`Could not open ${file.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setBusyId(null);
    }
  };

  const disabled = loading || uploading;
  return (
    <FieldRow label={label} required={required}>
      <div className="space-y-2">
        {loading ? (
          <EmptyState text="Loading…" />
        ) : files.length > 0 && (
          <div className="divide-y divide-foreground-100 rounded-lg border border-foreground-100">
            {files.map((f) => (
              <div key={f.id} className="flex items-center justify-between gap-3 px-3 py-2">
                <button
                  type="button"
                  onClick={() => void open(f)}
                  disabled={busyId === f.id}
                  className="inline-flex min-w-0 cursor-pointer items-center gap-1.5 text-[12px] text-primary-600 hover:underline disabled:opacity-60"
                >
                  <AppIcon className="ri-file-text-line shrink-0" />
                  <span className="truncate">{f.filename}</span>
                </button>
                <button
                  type="button"
                  onClick={() => void remove(f)}
                  disabled={busyId === f.id}
                  aria-label={`Delete ${f.filename}`}
                  className="shrink-0 cursor-pointer text-red-500 transition-smooth hover:text-red-600 disabled:opacity-60"
                >
                  <AppIcon className="ri-delete-bin-line text-sm" />
                </button>
              </div>
            ))}
          </div>
        )}
        <label
          className={`relative inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[12px] transition-smooth ${
            missing ? 'border-red-500 bg-red-50 text-red-600' : 'border-background-200 bg-background-100 text-foreground-600 hover:bg-background-200'
          } ${disabled ? 'pointer-events-none opacity-60' : 'cursor-pointer'}`}
        >
          <AppIcon className={uploading ? 'ri-loader-4-line animate-spin' : 'ri-upload-2-line'} />
          {uploading ? 'Uploading…' : 'Select file…'}
          <input
            type="file"
            accept={ACCEPT}
            disabled={disabled}
            aria-label={label}
            aria-invalid={missing ? true : undefined}
            className="sr-only"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              // Cleared so picking the same file again still fires onChange.
              e.target.value = '';
              void upload(picked);
            }}
          />
        </label>
        {missing && <div><FieldError message={missingMessage(missing, label, label, 'enter').replace('Please enter', 'Please upload')} /></div>}
        {item.helpText && !missing && <p className="text-[11px] text-foreground-400">{item.helpText}</p>}
      </div>
    </FieldRow>
  );
}
