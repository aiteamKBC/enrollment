import { useEffect, useState, type ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { useWizard } from "../WizardContext.helpers";
import { useToast } from "@/hooks/useToast.helpers";
import type { LearnerKind } from '@/api/extendedIlr';
import {
  deletePlrEvidence,
  fetchPlrEvidence,
  getPlrEvidenceUrl,
  uploadPlrEvidence,
  type PlrEvidenceFile,
} from '@/api/plrEvidence';
import type { PlrRecord } from '../../types';
import { Modal } from '../../components/Modal';
import { Table, Pagination, inputClass, btnPrimary, btnSecondary, iconBtn } from '../../components/ui';
import { StepHeading } from './fields';
import { useText } from '../layout/textsContext';
import { FieldError, invalidClass } from '../stepErrors';
import { OTHER, QUALIFICATION_TYPE_OPTIONS, SUBJECT_OPTIONS, joinChoice, splitChoice } from './plrText';

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.doc,.docx';
const PAGE_SIZES = [5, 10, 20];

const newId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `plr-${Date.now()}`;

const fmtDate = (iso?: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '');
  return m ? `${m[3]}/${m[2]}/${m[1]}` : '—';
};

/** Every entry's certificate/evidence files, loaded once for the step. */
function usePlrEvidence(kind: LearnerKind, learnerId: string) {
  const { error } = useToast();
  const [files, setFiles] = useState<PlrEvidenceFile[]>([]);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchPlrEvidence(kind, learnerId)
      .then((res) => { if (!cancelled) setFiles(res); })
      .catch((e) => { if (!cancelled) error('Could not load certificates', e instanceof Error ? e.message : 'Unexpected error'); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, learnerId]);

  const upload = async (recordRef: string, picked: File[]): Promise<PlrEvidenceFile[]> => {
    const saved: PlrEvidenceFile[] = [];
    setUploading(true);
    try {
      for (const file of picked) {
        try {
          const f = await uploadPlrEvidence(kind, learnerId, recordRef, file);
          saved.push(f);
          setFiles((prev) => [...prev, f]);
        } catch (e) {
          error(`Could not upload ${file.name}`, e instanceof Error ? e.message : 'Unexpected error');
        }
      }
    } finally {
      setUploading(false);
    }
    return saved;
  };

  const remove = async (file: PlrEvidenceFile) => {
    try {
      await deletePlrEvidence(kind, learnerId, file.id);
      setFiles((prev) => prev.filter((f) => f.id !== file.id));
    } catch (e) {
      error(`Could not remove ${file.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    }
  };

  const open = async (file: PlrEvidenceFile) => {
    try {
      window.open(await getPlrEvidenceUrl(kind, learnerId, file.id), '_blank', 'noopener');
    } catch (e) {
      error(`Could not open ${file.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    }
  };

  return { files, uploading, upload, remove, open };
}

type Evidence = ReturnType<typeof usePlrEvidence>;

function FileLinks({ files, evidence }: { files: PlrEvidenceFile[]; evidence: Evidence }) {
  if (files.length === 0) return <span className="text-foreground-300">—</span>;
  return (
    <div className="flex flex-col gap-0.5">
      {files.map((f) => (
        <button key={f.id} type="button" onClick={() => void evidence.open(f)} className="text-left text-[12px] text-primary-600 hover:underline cursor-pointer truncate max-w-[180px]">
          {f.filename}
        </button>
      ))}
    </div>
  );
}

function Field({ label, required, error, children }: { label: string; required?: boolean; error?: string; children: ReactNode }) {
  return (
    <label className="block mb-3">
      <span className="block text-[12px] font-medium text-foreground-700 mb-1">
        {label}
        {required && <span aria-hidden="true" className="text-red-500 ml-0.5">*</span>}
      </span>
      {children}
      {error && <FieldError message={error} />}
    </label>
  );
}

interface FormState {
  qualificationType: string;
  qualificationTypeOther: string;
  placeOfStudy: string;
  subject: string;
  subjectOther: string;
  level: string;
  startDate: string;
  endDate: string;
  awardDate: string;
  credits: string;
  grade: string;
}

function toForm(r: PlrRecord): FormState {
  const q = splitChoice(r.qualificationType, QUALIFICATION_TYPE_OPTIONS);
  const s = splitChoice(r.subject, SUBJECT_OPTIONS);
  return {
    qualificationType: q.choice, qualificationTypeOther: q.other,
    placeOfStudy: r.placeOfStudy, subject: s.choice, subjectOther: s.other,
    level: r.level ?? '', startDate: r.startDate ?? '', endDate: r.endDate ?? '', awardDate: r.awardDate ?? '',
    credits: r.credits ? String(r.credits) : '', grade: r.grade,
  };
}

/** What still has to be filled in, keyed by form field. */
function formErrors(f: FormState): Partial<Record<keyof FormState, string>> {
  const out: Partial<Record<keyof FormState, string>> = {};
  const blank = (v: string) => v.trim() === '';
  if (blank(f.qualificationType)) out.qualificationType = 'Please choose a qualification type.';
  else if (f.qualificationType === OTHER && blank(f.qualificationTypeOther)) out.qualificationTypeOther = 'Please specify the qualification type.';
  if (blank(f.placeOfStudy)) out.placeOfStudy = 'Please enter the place of study.';
  if (blank(f.subject)) out.subject = 'Please choose a subject.';
  else if (f.subject === OTHER && blank(f.subjectOther)) out.subjectOther = 'Please specify the subject.';
  if (blank(f.level)) out.level = 'Please enter the level.';
  if (blank(f.awardDate)) out.awardDate = 'Please enter the award date.';
  if (blank(f.grade)) out.grade = 'Please enter the grade.';
  if (f.credits.trim() !== '' && !/^\d+$/.test(f.credits.trim())) out.credits = 'Credits must be a whole number.';
  return out;
}

function PlrForm({
  record,
  isNew,
  evidence,
  onSave,
  onClose,
}: {
  record: PlrRecord;
  isNew: boolean;
  evidence: Evidence;
  onSave: (r: PlrRecord) => void;
  onClose: () => void;
}) {
  const [form, setForm] = useState<FormState>(() => toForm(record));
  const [tried, setTried] = useState(false);
  // Files go to storage as soon as they are picked. Cancelling a new entry
  // takes back the ones picked for it, so nothing is left behind for an entry
  // that was never added.
  const [pickedHere, setPickedHere] = useState<PlrEvidenceFile[]>([]);
  const set = (patch: Partial<FormState>) => setForm((f) => ({ ...f, ...patch }));
  const errors = tried ? formErrors(form) : {};
  const files = evidence.files.filter((f) => f.recordRef === record.id);
  const control = (key: keyof FormState) => `${inputClass}${errors[key] ? invalidClass : ''}`;

  const cancel = () => {
    if (isNew) pickedHere.forEach((f) => void evidence.remove(f));
    onClose();
  };

  const save = () => {
    setTried(true);
    if (Object.keys(formErrors(form)).length > 0) return;
    onSave({
      ...record,
      qualificationType: joinChoice(form.qualificationType, form.qualificationTypeOther),
      placeOfStudy: form.placeOfStudy.trim(),
      subject: joinChoice(form.subject, form.subjectOther),
      level: form.level.trim(),
      startDate: form.startDate,
      endDate: form.endDate,
      awardDate: form.awardDate,
      credits: form.credits.trim() ? Number(form.credits.trim()) : 0,
      grade: form.grade.trim(),
    });
  };

  return (
    <Modal
      title={isNew ? 'Add PLR' : 'Edit PLR'}
      onClose={cancel}
      size="max-w-lg"
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={cancel}>Cancel</button>
          <button type="button" className={btnPrimary} onClick={save} disabled={evidence.uploading}>Save</button>
        </div>
      }
    >
      <Field label="Qualification type" required error={errors.qualificationType}>
        <select value={form.qualificationType} onChange={(e) => set({ qualificationType: e.target.value })} aria-invalid={Boolean(errors.qualificationType) || undefined} className={`${control('qualificationType')} cursor-pointer`}>
          <option value="">Select…</option>
          {QUALIFICATION_TYPE_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </Field>
      {form.qualificationType === OTHER && (
        <Field label="Please specify the qualification type" required error={errors.qualificationTypeOther}>
          <input value={form.qualificationTypeOther} onChange={(e) => set({ qualificationTypeOther: e.target.value })} className={control('qualificationTypeOther')} />
        </Field>
      )}
      <Field label="Place of Study" required error={errors.placeOfStudy}>
        <input value={form.placeOfStudy} onChange={(e) => set({ placeOfStudy: e.target.value })} className={control('placeOfStudy')} />
      </Field>
      <Field label="Subject" required error={errors.subject}>
        <select value={form.subject} onChange={(e) => set({ subject: e.target.value })} aria-invalid={Boolean(errors.subject) || undefined} className={`${control('subject')} cursor-pointer`}>
          <option value="">Select…</option>
          {SUBJECT_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </Field>
      {form.subject === OTHER && (
        <Field label="Please specify the subject" required error={errors.subjectOther}>
          <input value={form.subjectOther} onChange={(e) => set({ subjectOther: e.target.value })} className={control('subjectOther')} />
        </Field>
      )}
      <Field label="Level" required error={errors.level}>
        <input value={form.level} onChange={(e) => set({ level: e.target.value })} className={control('level')} />
      </Field>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-3">
        <Field label="Start date">
          <input type="date" value={form.startDate} onChange={(e) => set({ startDate: e.target.value })} className={inputClass} />
        </Field>
        <Field label="End date">
          <input type="date" value={form.endDate} onChange={(e) => set({ endDate: e.target.value })} className={inputClass} />
        </Field>
        <Field label="Award date" required error={errors.awardDate}>
          <input type="date" value={form.awardDate} onChange={(e) => set({ awardDate: e.target.value })} className={control('awardDate')} />
        </Field>
      </div>
      <Field label="Credits" error={errors.credits}>
        <input inputMode="numeric" value={form.credits} onChange={(e) => set({ credits: e.target.value })} className={control('credits')} />
      </Field>
      <Field label="Grade" required error={errors.grade}>
        <input value={form.grade} onChange={(e) => set({ grade: e.target.value })} className={control('grade')} />
      </Field>

      <div className="mb-1">
        <span className="block text-[12px] font-medium text-foreground-700 mb-1">Certificate/Evidence</span>
        {files.length > 0 && (
          <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg mb-2">
            {files.map((f) => (
              <div key={f.id} className="flex items-center justify-between gap-3 px-3 py-2">
                <button type="button" onClick={() => void evidence.open(f)} className="text-[12px] text-primary-600 hover:underline inline-flex items-center gap-1.5 min-w-0 cursor-pointer">
                  <AppIcon className="ri-file-text-line shrink-0" /><span className="truncate">{f.filename}</span>
                </button>
                <button type="button" onClick={() => void evidence.remove(f)} aria-label={`Delete ${f.filename}`} className="text-red-500 hover:text-red-600 transition-smooth cursor-pointer shrink-0">
                  <AppIcon className="ri-delete-bin-line text-sm" />
                </button>
              </div>
            ))}
          </div>
        )}
        <label className={`relative inline-flex items-center gap-2 px-3 py-1.5 text-[12px] bg-background-100 text-foreground-600 rounded-lg border border-background-200 hover:bg-background-200 transition-smooth ${evidence.uploading ? 'opacity-60 pointer-events-none' : 'cursor-pointer'}`}>
          <AppIcon className={evidence.uploading ? 'ri-loader-4-line animate-spin' : 'ri-upload-2-line'} />
          {evidence.uploading ? 'Uploading…' : 'Select file…'}
          <input
            type="file"
            accept={ACCEPT}
            aria-label="Certificate/Evidence"
            disabled={evidence.uploading}
            className="sr-only"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              // Cleared so picking the same file again still fires onChange.
              e.target.value = '';
              void evidence.upload(record.id, picked).then((saved) => setPickedHere((prev) => [...prev, ...saved]));
            }}
          />
        </label>
      </div>
    </Modal>
  );
}

export default function Plr() {
  const { draft, setSection, userId, isCommercial } = useWizard();
  const t = useText();
  const plr = draft.plr;
  const kind: LearnerKind = isCommercial ? 'commercial' : 'apprenticeship';
  const evidence = usePlrEvidence(kind, userId);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(PAGE_SIZES[0]);
  const [editing, setEditing] = useState<{ record: PlrRecord; isNew: boolean } | null>(null);

  const setRecords = (records: PlrRecord[]) => setSection('plr', { ...plr, records });
  const add = () =>
    setEditing({
      isNew: true,
      record: { id: newId(), placeOfStudy: '', qualificationType: '', subject: '', level: '', startDate: '', endDate: '', awardDate: '', credits: 0, grade: '', recordType: 'Manual' },
    });
  const save = (r: PlrRecord) => {
    setRecords(editing?.isNew ? [...plr.records, r] : plr.records.map((x) => (x.id === r.id ? r : x)));
    setEditing(null);
  };
  // The entry's certificate files are deleted with it when the step is saved.
  const remove = (id: string) => setRecords(plr.records.filter((r) => r.id !== id));

  const total = plr.records.length;
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  const current = Math.min(page, totalPages);
  const rows = plr.records.slice((current - 1) * perPage, current * perPage);
  const first = total === 0 ? 0 : (current - 1) * perPage + 1;
  const last = Math.min(current * perPage, total);

  return (
    <div>
      <StepHeading title={t('block.plr.title')} />
      <button type="button" className={`${btnSecondary} mb-3`} onClick={add}>
        <AppIcon className="ri-add-line" />Add
      </button>

      <div className="border border-foreground-100 rounded-xl overflow-hidden">
        <Table headers={['Place of Study', 'Qualification Type', 'Subject', 'Level', 'Award Date', 'Credits', 'Grade', 'Record Type', 'Certificate / Evidence', 'Actions']}>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={10} className="py-4 px-3 text-center text-[12px] text-foreground-400">{t('block.plr.empty')}</td>
            </tr>
          ) : (
            rows.map((r) => (
              <tr key={r.id} className="border-b border-foreground-100 last:border-0 align-top">
                <td className="py-2 px-3 text-foreground-700">{r.placeOfStudy || '—'}</td>
                <td className="py-2 px-3 text-foreground-600">{r.qualificationType || '—'}</td>
                <td className="py-2 px-3 text-foreground-700">{r.subject || '—'}</td>
                <td className="py-2 px-3 text-foreground-600">{r.level || '—'}</td>
                <td className="py-2 px-3 text-foreground-600 whitespace-nowrap">{fmtDate(r.awardDate)}</td>
                <td className="py-2 px-3 text-foreground-600">{r.credits}</td>
                <td className="py-2 px-3 text-foreground-600">{r.grade || '—'}</td>
                <td className="py-2 px-3 text-foreground-600">{r.recordType}</td>
                <td className="py-2 px-3"><FileLinks files={evidence.files.filter((f) => f.recordRef === r.id)} evidence={evidence} /></td>
                <td className="py-2 px-3 whitespace-nowrap">
                  <button type="button" className={iconBtn} aria-label={`Edit ${r.subject || 'record'}`} onClick={() => setEditing({ record: r, isNew: false })}>
                    <AppIcon className="ri-pencil-line text-sm" />
                  </button>
                  <button type="button" className={`${iconBtn} ml-1`} aria-label={`Delete ${r.subject || 'record'}`} onClick={() => remove(r.id)}>
                    <AppIcon className="ri-delete-bin-line text-sm" />
                  </button>
                </td>
              </tr>
            ))
          )}
        </Table>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-foreground-100 px-3">
          <div className="flex items-center gap-2">
            <Pagination page={current} totalPages={totalPages} onChange={setPage} />
            <select
              aria-label="Items per page"
              value={perPage}
              onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1); }}
              className={`${inputClass} !w-auto !py-1 cursor-pointer`}
            >
              {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span className="text-[12px] text-foreground-500">items per page</span>
          </div>
          <span className="text-[12px] text-foreground-500">{first} - {last} of {total} items</span>
        </div>
      </div>

      {editing && (
        <PlrForm
          key={editing.record.id}
          record={editing.record}
          isNew={editing.isNew}
          evidence={evidence}
          onSave={save}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
