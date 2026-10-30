import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import {
  deckImageUrl,
  fetchDeckEditableFields,
  saveDeckEdits,
  uploadDeckImage,
  uploadEditedDeck,
  type DeckEditableFields,
  type DeckEvidenceItem,
} from '@/api/progressReviews';

/** Limits match the slide capacity enforced by backend edits.py. */
const MAX_EVIDENCE = 30;
const MAX_KSBS = 4;
const MAX_ACTIONS = 4;
const MAX_QUESTIONS = 8;
const RISK_STATUSES = ['On track', 'Need attention', 'At risk'];

const inputClass = 'w-full rounded-lg border border-foreground-200 bg-white px-3 py-2 text-[13px] text-foreground-900 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100';
const smallButton = 'inline-flex h-8 items-center gap-1.5 rounded-lg border border-foreground-200 bg-white px-2.5 text-[12px] font-semibold text-foreground-700 hover:bg-background-100 disabled:opacity-50';

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-foreground-200 bg-white p-4">
      <h3 className="text-sm font-bold text-foreground-900">{title}</h3>
      {hint && <p className="mt-0.5 text-[12px] text-foreground-500">{hint}</p>}
      <div className="mt-3 grid gap-3">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-[11px] font-bold uppercase tracking-wide text-foreground-500">
      {label}
      {children}
    </label>
  );
}

function TextInput({ value, onChange, multiline = false }: { value: string | null; onChange: (value: string | null) => void; multiline?: boolean }) {
  const props = { value: value ?? '', onChange: (e: { target: { value: string } }) => onChange(e.target.value || null), className: inputClass };
  return multiline ? <textarea rows={3} {...props} /> : <input type="text" {...props} />;
}

function NumberInput({ value, onChange, max }: { value: number | null; onChange: (value: number | null) => void; max: number }) {
  return (
    <input
      type="number" min={0} max={max} step="0.1" value={value ?? ''} className={inputClass}
      onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
    />
  );
}

function EvidenceCard({
  item, index, reviewId, pictured, onChange, onRemove, onMove, isFirst, isLast,
}: {
  item: DeckEvidenceItem; index: number; reviewId: string;
  /** Whether this item gets a photo frame on the slides. */
  pictured: boolean;
  onChange: (item: DeckEvidenceItem) => void; onRemove: () => void;
  onMove: (step: -1 | 1) => void; isFirst: boolean; isLast: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [ksbText, setKsbText] = useState(item.ksb_mappings.join(', '));

  async function upload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      onChange({ ...item, image_ref: await uploadDeckImage(reviewId, file) });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The photo could not be uploaded.');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  return (
    <div className={`grid gap-3 rounded-lg border p-3 sm:grid-cols-[180px_1fr] ${pictured ? 'border-foreground-200 bg-background-50' : 'border-dashed border-foreground-300 bg-white opacity-80'}`}>
      <div className="grid content-start gap-2">
        <div className="flex aspect-[16/10] items-center justify-center overflow-hidden rounded-lg border border-foreground-200 bg-white">
          {item.image_ref
            ? <img src={deckImageUrl(reviewId, item.image_ref)} alt={item.evidence_title || `Evidence ${index + 1}`} className="h-full w-full object-cover" />
            : <span className="text-[11px] text-foreground-400">No photo</span>}
        </div>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={(e) => { void upload(e.target.files?.[0]); }} />
        <div className="flex flex-wrap gap-1.5">
          <button type="button" className={smallButton} disabled={uploading} onClick={() => fileRef.current?.click()}>
            <AppIcon className={uploading ? 'ri-loader-4-line animate-spin' : 'ri-image-add-line'} />
            {item.image_ref ? 'Replace photo' : 'Add photo'}
          </button>
          {item.image_ref && (
            <button type="button" className={smallButton} onClick={() => onChange({ ...item, image_ref: null })}>Remove photo</button>
          )}
        </div>
        {error && <p role="alert" className="text-[11px] text-red-700">{error}</p>}
      </div>
      <div className="grid gap-2">
        <div className="flex items-center justify-between">
          <p className="text-[12px] font-bold text-foreground-700">
            Evidence {index + 1}{item.ref ? '' : ' (added)'}
            {!pictured && <span className="ml-2 font-semibold text-amber-700">Not pictured on the slides — move it up to show it</span>}
          </p>
          <div className="flex gap-1.5">
            <button type="button" aria-label="Move up" className={smallButton} disabled={isFirst} onClick={() => onMove(-1)}><AppIcon className="ri-arrow-up-line" /></button>
            <button type="button" aria-label="Move down" className={smallButton} disabled={isLast} onClick={() => onMove(1)}><AppIcon className="ri-arrow-down-line" /></button>
            <button type="button" className={smallButton} onClick={onRemove}><AppIcon className="ri-delete-bin-line" />Remove item</button>
          </div>
        </div>
        <Field label="Title"><TextInput value={item.evidence_title} onChange={(v) => onChange({ ...item, evidence_title: v })} /></Field>
        <Field label="What it shows"><TextInput multiline value={item.evidence_summary} onChange={(v) => onChange({ ...item, evidence_summary: v })} /></Field>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Date">
            <input type="date" value={item.evidence_date ?? ''} className={inputClass} onChange={(e) => onChange({ ...item, evidence_date: e.target.value || null })} />
          </Field>
          <Field label="KSBs (comma separated)">
            <input
              type="text" className={inputClass} placeholder="K1, S3, B2"
              value={ksbText}
              onChange={(e) => {
                setKsbText(e.target.value);
                onChange({ ...item, ksb_mappings: e.target.value.split(',').map((c) => c.trim()).filter(Boolean) });
              }}
            />
          </Field>
        </div>
      </div>
    </div>
  );
}

/** Correct a generated deck: its figures, evidence (with photos), KSB cards
 * and targets — or upload a version edited in PowerPoint. Either way the
 * server saves a new version and keeps the one it revises. */
export default function DeckEditor({
  reviewId, title, onClose, onSaved,
}: {
  reviewId: string;
  title: string;
  onClose: () => void;
  onSaved: (newReviewId: string) => void;
}) {
  const [fields, setFields] = useState<DeckEditableFields | null>(null);
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);
  const pptxRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDeckEditableFields(reviewId)
      .then((data) => { if (!cancelled) setFields(data); })
      .catch((err) => { if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Unable to load the slides for editing.'); });
    return () => { cancelled = true; };
  }, [reviewId]);

  function update<K extends keyof DeckEditableFields>(key: K, value: DeckEditableFields[K]) {
    setFields((current) => (current ? { ...current, [key]: value } : current));
  }

  async function save(work: () => Promise<{ reviewId: string }>) {
    setSaving(true);
    setSaveError('');
    try {
      onSaved((await work()).reviewId);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'The changes could not be saved.');
    } finally {
      setSaving(false);
    }
  }

  function saveFields() {
    if (!fields) return;
    const { kind: _kind, revisionSource: _source, evidence_photo_slots: _slots, ...edits } = fields;
    void save(() => saveDeckEdits(reviewId, edits));
  }

  function uploadPptx(file: File | undefined) {
    if (!file) return;
    if (!window.confirm(`Replace the slides with "${file.name}"? The current version is kept.`)) return;
    void save(() => uploadEditedDeck(reviewId, file));
  }

  const isMcm = fields?.kind === 'mcm';

  return (
    <div className="fixed inset-0 z-[70] flex flex-col bg-[rgba(10,12,24,0.9)] p-4" role="dialog" aria-label={title}>
      <div className="flex items-center justify-between gap-3 pb-3 text-white">
        <p className="truncate text-sm font-semibold">{title}</p>
        <button type="button" onClick={onClose} aria-label="Close editor" title="Close" className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-white text-lg text-slate-900 shadow-md ring-1 ring-slate-300 hover:bg-slate-100">
          <AppIcon className="ri-close-line" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto rounded-xl bg-background-100">
        {loadError ? (
          <div className="m-6 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{loadError}</div>
        ) : !fields ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-foreground-600">
            <AppIcon className="ri-loader-4-line animate-spin" /> Loading slides…
          </div>
        ) : (
          <div className="mx-auto grid max-w-4xl gap-4 p-4">
            {fields.revisionSource === 'uploaded' && (
              <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-900">
                These slides were uploaded from PowerPoint. Saving this form rebuilds them from the template, so changes made in PowerPoint will not carry over — upload a new PPTX instead to keep them.
              </p>
            )}
            <p className="rounded-lg border border-primary-100 bg-primary-50 px-3 py-2 text-[12px] text-primary-800">
              Correct anything the generated slides got wrong. Saving creates a new version from the same KBC template; the current version is kept. Leave a field empty to show “Not available”.
            </p>

            <Section title="Learner details">
              <div className="grid gap-3 sm:grid-cols-2">
                {([
                  ['full_name', 'Learner name'], ['programme', 'Programme'], ['employer', 'Employer'],
                  ['manager_name', 'Line manager'], ['coach', 'Coach'],
                ] as const).map(([key, label]) => (
                  <Field key={key} label={label}>
                    <TextInput value={fields.learner[key]} onChange={(v) => update('learner', { ...fields.learner, [key]: v })} />
                  </Field>
                ))}
              </div>
            </Section>

            <Section title="Attendance, progress and OTJ">
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Attendance %"><NumberInput max={100} value={fields.attendance.attendance_percentage} onChange={(v) => update('attendance', { ...fields.attendance, attendance_percentage: v })} /></Field>
                <Field label="Programme progress %"><NumberInput max={100} value={fields.progress.current_programme_progress_percentage} onChange={(v) => update('progress', { ...fields.progress, current_programme_progress_percentage: v })} /></Field>
                <Field label="Progress target %"><NumberInput max={100} value={fields.progress.target_progress_percentage} onChange={(v) => update('progress', { ...fields.progress, target_progress_percentage: v })} /></Field>
                <Field label="OTJ hours completed"><NumberInput max={10000} value={fields.otj.completed_otj_hours} onChange={(v) => update('otj', { ...fields.otj, completed_otj_hours: v })} /></Field>
                <Field label="OTJ hours required to date"><NumberInput max={10000} value={fields.otj.required_otj_hours_to_date} onChange={(v) => update('otj', { ...fields.otj, required_otj_hours_to_date: v })} /></Field>
                <Field label="OTJ status">
                  <select className={inputClass} value={fields.otj.risk_status ?? ''} onChange={(e) => update('otj', { ...fields.otj, risk_status: e.target.value || null })}>
                    <option value="">Not available</option>
                    {RISK_STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
                  </select>
                </Field>
                {fields.epa && (
                  <Field label="EPA readiness %"><NumberInput max={100} value={fields.epa.current_readiness} onChange={(v) => update('epa', { current_readiness: v })} /></Field>
                )}
              </div>
              <Field label="Next module"><TextInput value={fields.progress.next_module} onChange={(v) => update('progress', { ...fields.progress, next_module: v })} /></Field>
              <Field label="Engagement notes"><TextInput multiline value={fields.attendance.engagement_notes} onChange={(v) => update('attendance', { ...fields.attendance, engagement_notes: v })} /></Field>
            </Section>

            <Section title={isMcm ? 'My work this month' : 'Evidence'} hint={`The first ${fields.evidence_photo_slots} items are pictured on the slides, in this order. Use the arrows to choose which.`}>
              {fields.evidence.map((item, index) => (
                <EvidenceCard
                  key={item.ref || item.client_key || index} item={item} index={index} reviewId={reviewId}
                  pictured={index < fields.evidence_photo_slots}
                  isFirst={index === 0} isLast={index === fields.evidence.length - 1}
                  onChange={(next) => update('evidence', fields.evidence.map((e, i) => (i === index ? next : e)))}
                  onRemove={() => update('evidence', fields.evidence.filter((_, i) => i !== index))}
                  onMove={(step) => {
                    const next = [...fields.evidence];
                    [next[index], next[index + step]] = [next[index + step], next[index]];
                    update('evidence', next);
                  }}
                />
              ))}
              {fields.evidence.length < MAX_EVIDENCE && (
                <button
                  type="button" className={smallButton}
                  onClick={() => update('evidence', [...fields.evidence, { ref: null, client_key: crypto.randomUUID(), evidence_title: null, evidence_summary: null, evidence_date: null, ksb_mappings: [], image_ref: null }])}
                >
                  <AppIcon className="ri-add-line" /> Add evidence item
                </button>
              )}
            </Section>

            <Section title="KSBs to strengthen next" hint={`The cards on the KSB slide. Up to ${MAX_KSBS}.`}>
              {fields.priority_ksbs.map((ksb, index) => (
                <div key={index} className="grid gap-2 rounded-lg border border-foreground-200 bg-background-50 p-3 sm:grid-cols-[100px_1fr_auto]">
                  <Field label="Code"><TextInput value={ksb.code} onChange={(v) => update('priority_ksbs', fields.priority_ksbs.map((k, i) => (i === index ? { ...k, code: v } : k)))} /></Field>
                  <div className="grid gap-2">
                    <Field label="Description"><TextInput value={ksb.description} onChange={(v) => update('priority_ksbs', fields.priority_ksbs.map((k, i) => (i === index ? { ...k, description: v } : k)))} /></Field>
                    <Field label="Evidence idea"><TextInput value={ksb.how_to_evidence} onChange={(v) => update('priority_ksbs', fields.priority_ksbs.map((k, i) => (i === index ? { ...k, how_to_evidence: v } : k)))} /></Field>
                  </div>
                  <button type="button" aria-label="Remove KSB card" className={`${smallButton} self-start`} onClick={() => update('priority_ksbs', fields.priority_ksbs.filter((_, i) => i !== index))}><AppIcon className="ri-delete-bin-line" /></button>
                </div>
              ))}
              {fields.priority_ksbs.length < MAX_KSBS && (
                <button type="button" className={smallButton} onClick={() => update('priority_ksbs', [...fields.priority_ksbs, { code: null, description: null, how_to_evidence: null }])}>
                  <AppIcon className="ri-add-line" /> Add KSB card
                </button>
              )}
            </Section>

            <Section title="SMART targets" hint={`The action plan slide. Up to ${MAX_ACTIONS}.`}>
              {fields.actions.map((action, index) => {
                const set = (patch: Partial<typeof action>) => update('actions', fields.actions.map((a, i) => (i === index ? { ...a, ...patch } : a)));
                return (
                  <div key={index} className="grid gap-2 rounded-lg border border-foreground-200 bg-background-50 p-3">
                    <div className="grid gap-2 sm:grid-cols-[1fr_1fr_160px_auto]">
                      <Field label="Target"><TextInput value={action.title} onChange={(v) => set({ title: v })} /></Field>
                      <Field label="Owner"><TextInput value={action.owner} onChange={(v) => set({ owner: v })} /></Field>
                      <Field label="Due by"><input type="date" className={inputClass} value={action.due_by ?? ''} onChange={(e) => set({ due_by: e.target.value || null })} /></Field>
                      <button type="button" aria-label="Remove target" className={`${smallButton} self-end`} onClick={() => update('actions', fields.actions.filter((_, i) => i !== index))}><AppIcon className="ri-delete-bin-line" /></button>
                    </div>
                    <Field label="Detail"><TextInput value={action.detail} onChange={(v) => set({ detail: v })} /></Field>
                  </div>
                );
              })}
              {fields.actions.length < MAX_ACTIONS && (
                <button type="button" className={smallButton} onClick={() => update('actions', [...fields.actions, { title: null, detail: null, owner: null, due_by: null }])}>
                  <AppIcon className="ri-add-line" /> Add target
                </button>
              )}
            </Section>

            {fields.manager_questions && (
              <Section title="Questions for the line manager" hint={`Up to ${MAX_QUESTIONS}.`}>
                {fields.manager_questions.map((question, index) => (
                  <div key={index} className="flex gap-2">
                    <input type="text" className={inputClass} value={question} onChange={(e) => update('manager_questions', fields.manager_questions!.map((q, i) => (i === index ? e.target.value : q)))} />
                    <button type="button" aria-label="Remove question" className={smallButton} onClick={() => update('manager_questions', fields.manager_questions!.filter((_, i) => i !== index))}><AppIcon className="ri-delete-bin-line" /></button>
                  </div>
                ))}
                {fields.manager_questions.length < MAX_QUESTIONS && (
                  <button type="button" className={smallButton} onClick={() => update('manager_questions', [...fields.manager_questions!, ''])}>
                    <AppIcon className="ri-add-line" /> Add question
                  </button>
                )}
              </Section>
            )}

            <Section title="Edited it in PowerPoint instead?" hint="Upload your edited .pptx and it becomes the newest version of these slides.">
              <input ref={pptxRef} type="file" accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation" className="hidden" onChange={(e) => { uploadPptx(e.target.files?.[0]); e.target.value = ''; }} />
              <button type="button" className={`${smallButton} justify-self-start`} disabled={saving} onClick={() => pptxRef.current?.click()}>
                <AppIcon className="ri-upload-2-line" /> Upload edited PPTX
              </button>
            </Section>
          </div>
        )}
      </div>

      {fields && (
        <div className="flex items-center justify-end gap-3 pt-3">
          {saveError && <p role="alert" className="mr-auto rounded-lg bg-red-50 px-3 py-2 text-[12px] font-semibold text-red-700">{saveError}</p>}
          <button type="button" onClick={onClose} className="inline-flex h-10 items-center rounded-lg bg-white/10 px-4 text-[12px] font-semibold text-white hover:bg-white/20">Cancel</button>
          <button type="button" onClick={saveFields} disabled={saving} className="inline-flex h-10 items-center gap-2 rounded-lg bg-white px-4 text-[12px] font-semibold text-foreground-950 hover:bg-background-100 disabled:opacity-60">
            <AppIcon className={saving ? 'ri-loader-4-line animate-spin' : 'ri-save-3-line'} />
            {saving ? 'Saving…' : 'Save and regenerate'}
          </button>
        </div>
      )}
    </div>
  );
}
