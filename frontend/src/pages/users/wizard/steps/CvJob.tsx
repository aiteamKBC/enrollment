import { useEffect, useState, type ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { useWizard } from "../WizardContext.helpers";
import { useToast } from "@/hooks/useToast.helpers";
import type { LearnerKind } from '@/api/extendedIlr';
import {
  deleteCvDocument,
  fetchCvDocuments,
  getCvDocumentUrl,
  uploadCvDocument,
  type CvDocKind,
  type CvDocument,
} from '@/api/cvJobDocuments';
import { EmptyState, FieldRow, YesNoRadio, inputClass } from '../../components/ui';
import { LabeledInput, LabeledSelect, StepHeading } from './fields';
import { FieldError, invalidClass } from '../stepErrors';
import { requiredMessage, useMissing, useMissingCheck } from "../stepErrors.helpers";
import { CvJobText, fieldQualificationLabels, programmeField } from './cvJobText';
import { StepItems, type ItemRenderer } from '../layout/StepItems';
import { useStepTitle } from '../layout/useLayout';
import { useText } from '../layout/textsContext';
import { TEXT_SLOTS } from '../layout/texts';

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.doc,.docx';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-6">
      <h3 className="text-[14px] font-heading font-semibold text-foreground-800 border-b border-foreground-100 pb-1.5 mb-2">{title}</h3>
      {children}
    </section>
  );
}

/**
 * The step's uploads, loaded once for all four fields.
 *
 * Each file is sent to Azure as soon as it is picked (quarantine -> scan ->
 * approved) and recorded, with its blob path, on the learner's Wizard_Cv_Job
 * row — independently of the step's answers, which travel with the draft.
 */
function useCvDocuments(kind: LearnerKind, learnerId: string) {
  const { error } = useToast();
  const [docs, setDocs] = useState<CvDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState<CvDocKind | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchCvDocuments(kind, learnerId)
      .then((res) => { if (!cancelled) setDocs(res); })
      .catch((e) => { if (!cancelled) error('Could not load your documents', e instanceof Error ? e.message : 'Unexpected error'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, learnerId]);

  const upload = async (docKind: CvDocKind, files: File[]) => {
    if (files.length === 0) return;
    setUploading(docKind);
    try {
      // One at a time, so a refused file is reported by name and the rest still go.
      for (const file of files) {
        try {
          const saved = await uploadCvDocument(kind, learnerId, docKind, file);
          setDocs((prev) => [...prev, saved]);
        } catch (e) {
          error(`Could not upload ${file.name}`, e instanceof Error ? e.message : 'Unexpected error');
        }
      }
    } finally {
      setUploading(null);
    }
  };

  const remove = async (doc: CvDocument) => {
    setBusyId(doc.id);
    try {
      await deleteCvDocument(kind, learnerId, doc.id);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (e) {
      error(`Could not remove ${doc.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setBusyId(null);
    }
  };

  const open = async (doc: CvDocument) => {
    setBusyId(doc.id);
    try {
      window.open(await getCvDocumentUrl(kind, learnerId, doc.id), '_blank', 'noopener');
    } catch (e) {
      error(`Could not open ${doc.filename}`, e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setBusyId(null);
    }
  };

  return { docs, loading, uploading, busyId, upload, remove, open };
}

type CvDocs = ReturnType<typeof useCvDocuments>;

function DocumentField({ label, docKind, store }: { label: string; docKind: CvDocKind; store: CvDocs }) {
  const files = store.docs.filter((d) => d.docKind === docKind);
  const uploading = store.uploading === docKind;
  const disabled = store.loading || store.uploading !== null;
  return (
    <FieldRow label={label}>
      <div className="space-y-2">
        {store.loading ? (
          <EmptyState text="Loading…" />
        ) : files.length > 0 && (
          <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg">
            {files.map((f) => (
              <div key={f.id} className="flex items-center justify-between gap-3 px-3 py-2">
                <button
                  type="button"
                  onClick={() => void store.open(f)}
                  disabled={store.busyId === f.id}
                  className="text-[12px] text-primary-600 hover:underline inline-flex items-center gap-1.5 min-w-0 cursor-pointer disabled:opacity-60"
                >
                  <AppIcon className="ri-file-text-line shrink-0" />
                  <span className="truncate">{f.filename}</span>
                </button>
                <button
                  type="button"
                  onClick={() => void store.remove(f)}
                  disabled={store.busyId === f.id}
                  aria-label={`Delete ${f.filename}`}
                  className="text-red-500 hover:text-red-600 transition-smooth cursor-pointer shrink-0 disabled:opacity-60"
                >
                  <AppIcon className="ri-delete-bin-line text-sm" />
                </button>
              </div>
            ))}
          </div>
        )}
        <label className={`relative inline-flex items-center gap-2 px-3 py-1.5 text-[12px] bg-background-100 text-foreground-600 rounded-lg border border-background-200 hover:bg-background-200 transition-smooth ${disabled ? 'opacity-60 pointer-events-none' : 'cursor-pointer'}`}>
          <AppIcon className={uploading ? 'ri-loader-4-line animate-spin' : 'ri-upload-2-line'} />
          {uploading ? 'Uploading…' : 'Select file…'}
          <input
            type="file"
            accept={ACCEPT}
            disabled={disabled}
            aria-label={label}
            className="sr-only"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? []);
              // Cleared so picking the same file again still fires onChange.
              e.target.value = '';
              void store.upload(docKind, picked);
            }}
          />
        </label>
      </div>
    </FieldRow>
  );
}

export default function CvJob() {
  const { draft, setSection, board, userId, isCommercial } = useWizard();
  const cv = draft.cvJob;
  const set = (patch: Partial<typeof cv>) => setSection('cvJob', { ...cv, ...patch });
  const kind: LearnerKind = isCommercial ? 'commercial' : 'apprenticeship';
  const store = useCvDocuments(kind, userId);
  const has = (docKind: CvDocKind) => store.docs.some((d) => d.docKind === docKind);
  const t = useText();
  // The field questions name the learner's programme field. Edited wording
  // marks the spot with {field}; the standard wording keeps its own phrasing,
  // including the neutral one for programmes without a field.
  const standardField = fieldQualificationLabels(board.programme?.name);
  const field = programmeField(board.programme?.name) ?? 'your programme';
  const fieldText = (key: string, standard: string) => {
    const text = t(key);
    return text === TEXT_SLOTS[key]?.default ? standard : text.split('{field}').join(field);
  };
  const fieldLabels = {
    hasFieldQualification: fieldText('cv.fieldQualification.label', standardField.hasFieldQualification),
    highestFieldQualification: fieldText('cv.fieldQualification.named', standardField.highestFieldQualification),
  };

  // Red rows once Next is pressed with them unanswered.
  const missing = useMissingCheck();
  const yn = (key: string) => (missing(key) ? requiredMessage('', 'choose') : undefined);
  const experienceMissing = Boolean(useMissing('Previous experience (or N/A)'));
  const title = useStepTitle('cv-job', 'CV/Job Description');

  // One renderer per item in layout/registry.ts; the layout decides order,
  // which are shown and which are required.
  const renderers: Record<string, ItemRenderer> = {
    'cv.h.experience': ({ children }) => (
      <Section title={t('cv.h.experience.title')}>
        <p className="text-[12px] text-foreground-500 mb-3 leading-relaxed">{t('cv.h.experience.intro')}</p>
        {children}
      </Section>
    ),
    'cv.cvUpload': () => <DocumentField label={t('cv.cvUpload.label')} docKind="cv" store={store} />,
    'cv.experience': ({ required }) => (
      <FieldRow label={t('cv.experience.label')} required={required}>
        <textarea
          rows={4}
          value={cv.experienceText ?? ''}
          onChange={(e) => set({ experienceText: e.target.value })}
          aria-invalid={experienceMissing || undefined}
          className={`${inputClass}${experienceMissing ? invalidClass : ''}`}
        />
        {experienceMissing && <FieldError message="Please describe your experience, or write N/A if you uploaded your CV." />}
      </FieldRow>
    ),
    'cv.highestQualification': ({ required }) => <LabeledInput label={t('cv.highestQualification.label')} required={required} missingKey="Highest-level qualification" value={cv.highestQualification ?? ''} onChange={(v) => set({ highestQualification: v })} />,
    'cv.highestQualificationField': ({ required }) => <LabeledInput label={t('cv.highestQualificationField.label')} required={required} missingKey="Field of highest qualification" value={cv.highestQualificationField ?? ''} onChange={(v) => set({ highestQualificationField: v })} />,
    'cv.fieldQualification': ({ required }) => (
      <>
        {/* Answering No clears the named qualification, so a hidden answer is never saved. */}
        <YesNoRadio
          legend={fieldLabels.hasFieldQualification}
          name="cv-field-qual"
          error={yn('Qualifications in your programme field')}
          value={cv.hasFieldQualification ?? null}
          onChange={(v) => set({ hasFieldQualification: v, ...(v ? {} : { highestFieldQualification: '' }) })}
        />
        {cv.hasFieldQualification === true && (
          <LabeledInput label={fieldLabels.highestFieldQualification} required={required} missingKey="Highest qualification in your programme field" value={cv.highestFieldQualification ?? ''} onChange={(v) => set({ highestFieldQualification: v })} />
        )}
        {/* Shown while it holds a file too, so an upload is never hidden by a changed answer. */}
        {(cv.hasFieldQualification === true || has('transcript')) && (
          <DocumentField label={t('cv.fieldQualification.transcript')} docKind="transcript" store={store} />
        )}
      </>
    ),

    'cv.h.gcse': ({ children }) => (
      <Section title={t('cv.h.gcse.title')}>
        <p className="text-[12px] text-foreground-500 mb-3 leading-relaxed">{t('cv.h.gcse.note')}</p>
        {children}
      </Section>
    ),
    'cv.gcseEnglish': () => (
      <>
        <YesNoRadio legend={t('cv.gcseEnglish.label')} name="cv-gcse-english" error={yn('GCSE in English')} value={cv.gcseEnglish ?? null} onChange={(v) => set({ gcseEnglish: v })} />
        {(cv.gcseEnglish === true || has('gcse-english')) && (
          <DocumentField label={t('cv.gcseEnglish.upload')} docKind="gcse-english" store={store} />
        )}
      </>
    ),
    'cv.gcseMaths': () => (
      <>
        <YesNoRadio legend={t('cv.gcseMaths.label')} name="cv-gcse-maths" error={yn('GCSE in Maths')} value={cv.gcseMaths ?? null} onChange={(v) => set({ gcseMaths: v })} />
        {(cv.gcseMaths === true || has('gcse-maths')) && (
          <DocumentField label={t('cv.gcseMaths.upload')} docKind="gcse-maths" store={store} />
        )}
      </>
    ),

    'cv.h.functionalSkills': ({ children }) => (
      <Section title={t('cv.h.functionalSkills.title')}>
        <p className="text-[12px] text-foreground-500 mb-3 leading-relaxed">{t('cv.h.functionalSkills.note')}</p>
        {children}
      </Section>
    ),
    'cv.functionalSkills': ({ required }) => (
      <LabeledSelect
        label={t('cv.functionalSkills.label')}
        required={required}
        missingKey="Functional Skills enrolment"
        value={cv.functionalSkillsEnrol ?? ''}
        options={[...CvJobText.functionalSkillsOptions]}
        onChange={(v) => set({ functionalSkillsEnrol: v })}
      />
    ),
  };

  return (
    <div>
      <StepHeading title={title} />
      <div className="max-w-3xl">
        <StepItems slug="cv-job" renderers={renderers} />
      </div>
    </div>
  );
}
