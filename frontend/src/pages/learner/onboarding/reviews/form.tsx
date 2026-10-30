import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { useMyLearner } from '@/hooks/useMyLearner';
import { ONBOARDING_NAV_ITEMS } from '@/hooks/useOnboardingRedirect';
import {
  fetchReviewForm,
  saveReviewForm,
  type FsAssessment,
  type PriorLearningItem,
  type ReviewFormAnswers,
  type ReviewFormResponse,
  type ReviewSection,
  type SkillsRadarItem,
  type SkillsRadarSummary,
} from '@/api/reviewForm';
import { MAX_COMPETENCE_SCORE, competenceMeta } from '@/mocks/enrolment-console';
import { btnPrimary, btnSecondary, inputClass } from '@/pages/users/components/ui';
import SignReviewModal from './SignReviewModal';
import { downloadReviewPdf } from './reviewDocument';
import {
  EXTENDED_ILR_QUESTIONS,
  FS_JOB_ROLE_QUESTIONS,
  HEALTH_SAFETY_QUESTIONS,
  ILR_QUESTIONS,
  LEARNER_ACKNOWLEDGEMENT,
  ULN_PRIVACY_FURTHER_INFO,
  ULN_PRIVACY_INTRO,
  ULN_PRIVACY_POINTS,
  ULN_PURPOSES,
  REVIEW_QUESTION_LABELS,
  RPL_QUESTIONS,
} from './questions';

const learnerNav = roleNavMap.learner;

const PLR_SUBJECTS = ['English', 'Maths', 'ICT'] as const;

const EMPTY_PRIOR_LEARNING: PriorLearningItem = {
  description: '', impact: '', durationReduced: '', costReduced: '', offTheJobTimeReduced: '',
};

const FS_SUBJECTS = ['English', 'Maths', 'ICT'] as const;
const EXEMPTION_OPTIONS = ['', 'Exempt', 'Not exempt', 'Pending evidence'] as const;
/** Result tiles, grouped by subject as in the design. */
const FS_RESULT_GROUPS: { subject: string; items: string[] }[] = [
  { subject: 'English', items: ['English Reading', 'English Writing', 'English SLC'] },
  { subject: 'Maths', items: ['Maths'] },
  { subject: 'ICT', items: ['ICT'] },
];

const EMPTY_ASSESSMENT: FsAssessment = { subject: '', level: '', date: '', outcome: '' };

function Panel({
  title,
  complete,
  children,
  onSave,
  saving,
  dirty,
  /**
   * Allow saving with nothing typed. For panels whose only input is optional
   * (Skills Radar's note, Comments), requiring an edit would mean the reviewer
   * had to write something just to mark the panel complete.
   */
  alwaysSavable = false,
}: {
  title: string;
  complete: boolean;
  children: ReactNode;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  alwaysSavable?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const canSave = !saving && (dirty || alwaysSavable);
  return (
    <section className="rounded-xl border border-foreground-200/70 bg-background-50 overflow-hidden">
      <header className="flex items-center justify-between gap-3 px-4 py-2.5 bg-background-100 border-b border-foreground-200/70">
        <button type="button" onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 min-w-0 text-left">
          <i className={`${open ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'} text-foreground-500`} />
          <h2 className="text-[13px] font-heading font-semibold text-foreground-800 truncate">{title}</h2>
        </button>
        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${
          complete ? 'bg-emerald-50 text-emerald-700 border border-emerald-200/60' : 'bg-background-200 text-foreground-500'
        }`}>
          {complete ? 'Completed' : 'Incomplete'}
        </span>
      </header>
      {open && (
        <>
          <div className="p-4">{children}</div>
          <div className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-foreground-100">
            <button type="button" onClick={onSave} disabled={!canSave} className={`${btnPrimary} ${!canSave ? 'opacity-60 cursor-not-allowed' : ''}`}>
              {saving ? <><i className="ri-loader-4-line animate-spin" />Saving…</> : <><i className="ri-save-line" />Save</>}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

/**
 * Yes/No row matching the review form's layout (question above, radios below).
 *
 * `name` must be the answer key, not the label: radios sharing a name form one
 * mutually exclusive group, and two of these questions open with the same 40
 * characters ("Please confirm that you will provide the ..."), so deriving the
 * name from the label made answering one clear the other.
 */
function YesNo({ name, label, value, onChange, required = true }: { name: string; label: string; value: string; onChange: (v: string) => void; required?: boolean }) {
  return (
    <div className="py-2.5 border-b border-foreground-100 last:border-0">
      <p className="text-[12px] text-foreground-700 leading-relaxed">
        {label} {required && <span className="text-red-500">*</span>}
      </p>
      <div className="flex items-center gap-5 mt-1.5">
        {['Yes', 'No'].map((option) => (
          <label key={option} className="flex items-center gap-1.5 text-[12px] text-foreground-700 cursor-pointer">
            <input type="radio" name={name} checked={value === option} onChange={() => onChange(option)} className="accent-primary-500" />
            {option}
          </label>
        ))}
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[200px_1fr] gap-1 sm:gap-4 py-1.5">
      <span className="text-[12px] text-foreground-500">{label}</span>
      <span className="text-[12px] text-primary-700">{value || '—'}</span>
    </div>
  );
}

/** Editable list of Functional Skills assessments. */
function AssessmentList({
  title,
  rows,
  onChange,
}: {
  title: string;
  rows: FsAssessment[];
  onChange: (rows: FsAssessment[]) => void;
}) {
  const update = (index: number, patch: Partial<FsAssessment>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return (
    <div className="mb-4">
      <p className="text-[12px] font-semibold text-foreground-700 mb-2">{title}</p>
      <div className="space-y-2">
        {rows.map((row, index) => (
          <div key={index} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 items-end rounded-lg border border-foreground-200/70 bg-background-100 p-2.5">
            <label className="block">
              <span className="text-[10px] font-semibold text-foreground-500 block mb-1">Subject</span>
              <select value={row.subject} onChange={(e) => update(index, { subject: e.target.value })} className={inputClass}>
                <option value="">Select…</option>
                {FS_SUBJECTS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="text-[10px] font-semibold text-foreground-500 block mb-1">Level</span>
              <input value={row.level} onChange={(e) => update(index, { level: e.target.value })} className={inputClass} placeholder="e.g. Level 2" />
            </label>
            <label className="block">
              <span className="text-[10px] font-semibold text-foreground-500 block mb-1">Assessment date</span>
              <input type="date" value={row.date} onChange={(e) => update(index, { date: e.target.value })} className={inputClass} />
            </label>
            <label className="block">
              <span className="text-[10px] font-semibold text-foreground-500 block mb-1">Outcome</span>
              <input value={row.outcome} onChange={(e) => update(index, { outcome: e.target.value })} className={inputClass} placeholder="e.g. Pass" />
            </label>
            <button type="button" onClick={() => onChange(rows.filter((_, i) => i !== index))}
              className="text-[11px] font-semibold text-red-600 hover:underline px-2 py-2">
              <i className="ri-delete-bin-line" />
            </button>
          </div>
        ))}
      </div>
      <button type="button" onClick={() => onChange([...rows, { ...EMPTY_ASSESSMENT }])}
        className="mt-2 w-full rounded-lg border border-dashed border-foreground-300 py-2.5 text-[12px] font-semibold text-foreground-600 hover:bg-background-100 flex items-center justify-center gap-1.5">
        <i className="ri-add-circle-line" />Add New
      </button>
    </div>
  );
}

const PRIOR_LEARNING_FIELDS: { key: keyof PriorLearningItem; label: string }[] = [
  { key: 'description', label: 'Description' },
  { key: 'impact', label: 'Impact' },
  { key: 'durationReduced', label: 'Duration reduced' },
  { key: 'costReduced', label: 'Cost reduced' },
  { key: 'offTheJobTimeReduced', label: 'Off the job time reduced' },
];

/** "Add Prior Learning" dialog behind the New Item button. */
function PriorLearningModal({
  initial,
  onCancel,
  onSave,
}: {
  initial: PriorLearningItem;
  onCancel: () => void;
  onSave: (item: PriorLearningItem) => void;
}) {
  const [item, setItem] = useState<PriorLearningItem>(initial);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-lg rounded-2xl bg-background-50 shadow-xl max-h-[90vh] overflow-y-auto">
        <header className="flex items-center justify-between gap-3 px-5 py-3 border-b border-foreground-100">
          <h3 className="text-[14px] font-heading font-semibold text-foreground-900">Add Prior Learning</h3>
          <button onClick={onCancel} className="text-foreground-400 hover:text-foreground-700" aria-label="Close">
            <i className="ri-close-circle-line text-[20px]" />
          </button>
        </header>
        <div className="p-5 space-y-3">
          {PRIOR_LEARNING_FIELDS.map((field) => (
            <label key={field.key} className="block">
              <span className="text-[12px] text-foreground-700 block mb-1">{field.label}</span>
              <textarea rows={2} value={item[field.key]}
                onChange={(e) => setItem((prev) => ({ ...prev, [field.key]: e.target.value }))}
                className={`${inputClass} resize-y`} />
            </label>
          ))}
        </div>
        <footer className="flex items-center justify-end gap-2 px-5 py-3 border-t border-foreground-100">
          <button onClick={onCancel} className={btnSecondary}>Cancel</button>
          <button onClick={() => onSave(item)} className={btnPrimary}><i className="ri-save-line" />Save</button>
        </footer>
      </div>
    </div>
  );
}

/**
 * The learner's Skills Radar self-assessment, read-only.
 *
 * Grouped by theme with a level bar per KSB, using the same 8-point scale and
 * colours as the wizard step (competenceMeta), so the reviewer sees exactly what
 * the learner saw. Read-only on purpose: staff editing a self-assessment would
 * falsify the record they are reviewing.
 */
function SkillsRadarReview({ radar }: { radar?: SkillsRadarSummary }) {
  const [expanded, setExpanded] = useState(false);

  if (!radar || radar.items.length === 0) {
    return (
      <p className="text-[12px] text-foreground-400 italic py-2">
        This learner has not completed the Skills Radar self-assessment yet.
      </p>
    );
  }

  // Group by theme, preserving the authored order within each.
  const themes: { theme: string; items: SkillsRadarItem[] }[] = [];
  for (const item of radar.items) {
    const theme = item.theme || 'Other';
    const last = themes.find((t) => t.theme === theme);
    if (last) last.items.push(item);
    else themes.push({ theme, items: [item] });
  }

  const scored = radar.items.filter((i) => typeof i.score === 'number');
  const average = scored.length
    ? (scored.reduce((sum, i) => sum + (i.score ?? 0), 0) / scored.length)
    : null;
  // Long assessments are collapsed by default so the panel stays readable.
  const shown = expanded ? themes : themes.slice(0, 1);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <p className="text-[12px] text-foreground-600">
          {radar.standardLabel && <span className="font-semibold text-foreground-800">{radar.standardLabel}</span>}
          {radar.standardLabel && ' — '}
          {radar.answered} of {radar.total} answered
          {average !== null && <span className="text-foreground-400"> · average {average.toFixed(1)}/8</span>}
        </p>
        {themes.length > 1 && (
          <button type="button" onClick={() => setExpanded((v) => !v)}
            className="text-[11px] font-semibold text-primary-600 hover:underline">
            {expanded ? 'Show less' : `Show all ${themes.length} themes`}
          </button>
        )}
      </div>

      <div className="space-y-3">
        {shown.map((group) => (
          <div key={group.theme}>
            <p className="text-[11px] font-semibold text-foreground-500 uppercase tracking-wider mb-1.5">{group.theme}</p>
            <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg">
              {group.items.map((item) => {
                const meta = competenceMeta(item.level);
                return (
                  <div key={item.ksbId} className="px-3 py-2">
                    <div className="flex items-start justify-between gap-3">
                      <span className="text-[12px] text-foreground-700 min-w-0">
                        {item.codes.length > 0 && (
                          <span className="text-[10px] font-semibold text-foreground-500 bg-background-200 rounded px-1.5 py-0.5 mr-1.5">
                            {item.codes.join(', ')}
                          </span>
                        )}
                        {item.title || item.ksbId}
                      </span>
                      {meta ? (
                        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full shrink-0 ${meta.tintBg} ${meta.tintText} border ${meta.tintBorder}`}>
                          {meta.label}
                        </span>
                      ) : (
                        <span className="text-[10px] text-foreground-400 italic shrink-0">Not answered</span>
                      )}
                    </div>
                    {/* Level as a proportion of the self-assessment scale (9 points). */}
                    {typeof item.score === 'number' && (
                      <div className="mt-1.5 h-1.5 rounded-full bg-background-200 overflow-hidden">
                        <div className={`h-full rounded-full ${meta?.cellFill ?? 'bg-primary-500'}`}
                          style={{ width: `${(item.score / MAX_COMPETENCE_SCORE) * 100}%` }} />
                      </div>
                    )}
                    {item.note && <p className="text-[11px] text-foreground-500 mt-1 italic">{item.note}</p>}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Empty-state aware table, matching the design's "No records available." */
function RecordTable({ headers, rows, empty }: { headers: string[]; rows: ReactNode[][]; empty: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-foreground-200/70">
      <table className="w-full text-[12px]">
        <thead className="bg-background-100">
          <tr>
            {headers.map((h) => (
              <th key={h} className="text-left font-semibold text-foreground-600 px-3 py-2 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={headers.length} className="px-3 py-4 text-center text-foreground-400">{empty}</td></tr>
          ) : (
            rows.map((cells, i) => (
              <tr key={i} className="border-t border-foreground-100">
                {cells.map((cell, j) => <td key={j} className="px-3 py-2 text-foreground-700 align-top">{cell}</td>)}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The enrolment review form. Which panels render is decided by the backend
 * (`sections`), so the four reviews share this page:
 *   - Eligibility Review & FS Discussion
 *   - RPL And Experience
 *   - Workplace Health & Safety Declaration
 *   - ULN Privacy Notice & Learner Acknowledgement
 *
 * Each panel saves on its own — the backend merges sections, so one panel's Save
 * never blanks another's answers.
 */
export default function ReviewFormPage() {
  const { kind, id } = useMyLearner();
  const { eventKey = '' } = useParams();
  const navigate = useNavigate();

  const [data, setData] = useState<ReviewFormResponse | null>(null);
  const [answers, setAnswers] = useState<ReviewFormAnswers>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingSection, setSavingSection] = useState<ReviewSection | null>(null);
  const [dirty, setDirty] = useState<Partial<Record<ReviewSection, boolean>>>({});
  const [finishing, setFinishing] = useState(false);
  // Prior Learning modal: the index being edited, or -1 when adding.
  const [priorEditing, setPriorEditing] = useState<number | null>(null);
  const [signing, setSigning] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchReviewForm(kind, id, eventKey)
      .then((res) => {
        setData(res);
        setAnswers(res.answers || {});
        setDirty({});
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [kind, id, eventKey]);

  useEffect(load, [load]);

  const setSection = <K extends keyof ReviewFormAnswers>(section: K, value: ReviewFormAnswers[K]) => {
    setAnswers((prev) => ({ ...prev, [section]: value }));
    setDirty((prev) => ({ ...prev, [section as ReviewSection]: true }));
  };

  /** Save one panel; `value` stands in for the panel's answers (read-and-confirm panels). */
  const save = async (section: ReviewSection, value?: ReviewFormAnswers[keyof ReviewFormAnswers]) => {
    if (savingSection) return;
    setSavingSection(section);
    setError(null);
    try {
      // Default to {} rather than undefined: an undefined value disappears in
      // JSON.stringify, leaving an empty payload the server treats as "nothing
      // posted", so a panel with only optional input would never be marked done.
      const res = await saveReviewForm(kind, id, eventKey, {
        answers: { [section]: value ?? answers[section] ?? {} } as Partial<ReviewFormAnswers>,
      });
      if (value !== undefined) setAnswers((prev) => ({ ...prev, [section]: value }));
      setData(res);
      setDirty((prev) => ({ ...prev, [section]: false }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save this section.');
    } finally {
      setSavingSection(null);
    }
  };

  const finish = async () => {
    if (finishing) return;
    setFinishing(true);
    setError(null);
    try {
      // Send anything still unsaved along with the finish flag, so clicking
      // Finish never silently discards edits sitting in an open panel.
      const pending = Object.fromEntries(
        (Object.keys(dirty) as ReviewSection[])
          .filter((s) => dirty[s])
          .map((s) => [s, answers[s]]),
      ) as Partial<ReviewFormAnswers>;
      const res = await saveReviewForm(kind, id, eventKey, { answers: pending, finish: true });
      setData(res);
      setDirty({});
      navigate('/learner/onboarding/reviews');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not finish this review.');
    } finally {
      setFinishing(false);
    }
  };

  const info = data?.learnerInformation;
  const status = data?.sectionStatus;
  const fs = answers.functionalSkills || {};
  const priorItems = answers.priorLearning?.items ?? [];
  // Which panels this review renders — decided server-side.
  const has = (section: ReviewSection) => !!data?.sections.includes(section);
  // Calculated attainment comes from the learner's PLR records; with none on
  // file there is nothing to calculate, which the design shows as a message
  // rather than a level.
  const plrCalculated = (data?.plrRecords?.length ?? 0) > 0 ? '' : 'No qualifications';

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      navItems={ONBOARDING_NAV_ITEMS}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="Review"
      pageSubtitle={data?.reviewLabel || 'Enrolment review'}
      userName="Learner"
      userRole="Learner"
    >
      <main className="page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <button onClick={() => navigate('/learner/onboarding/reviews')} className="text-foreground-500 hover:text-foreground-800">
              <i className="ri-arrow-left-line text-[18px]" />
            </button>
            <h1 className="text-[15px] font-heading font-semibold text-foreground-900 truncate">
              {data?.reviewLabel}
              {data?.scheduledDate && <span className="text-foreground-500 font-normal"> — {data.scheduledDate}</span>}
            </h1>
            {data?.completed && (
              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700">Completed</span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {data?.reviewedBy && (
              <p className="text-[11px] text-foreground-500">Reviewed by: <span className="text-foreground-700">{data.reviewedBy}</span></p>
            )}
            {/* Signing and export are only offered once the form is finished —
                a signature against changeable answers would attest to nothing. */}
            {data?.completed && (
              <>
                <button onClick={() => setSigning(true)} className={btnSecondary}>
                  <i className="ri-pen-nib-line" />
                  {data.signatures.learner.signed ? 'Signed' : 'Sign review'}
                </button>
                <button onClick={() => downloadReviewPdf(data, REVIEW_QUESTION_LABELS)} className={btnSecondary}>
                  <i className="ri-file-pdf-line" />Export PDF
                </button>
              </>
            )}
          </div>
        </div>

        {loading && (
          <p className="py-16 text-center text-[13px] text-foreground-400">
            <i className="ri-loader-4-line animate-spin mr-2" />Loading the review…
          </p>
        )}

        {!loading && error && !data && (
          <div className="py-16 text-center text-[13px]">
            <p className="text-red-600 mb-3"><i className="ri-error-warning-line mr-1.5" />{error}</p>
            <button className={btnSecondary} onClick={load}><i className="ri-refresh-line" />Retry</button>
          </div>
        )}

        {!loading && data && (
          <>
            {error && (
              <p className="text-[11px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-2.5 py-2">
                <i className="ri-error-warning-line mr-1" />{error}
              </p>
            )}

            {/* 1. Learner Information — derived from the learner record, read-only. */}
            <section className="rounded-xl border border-foreground-200/70 bg-background-50 overflow-hidden">
              <header className="px-4 py-2.5 bg-background-100 border-b border-foreground-200/70">
                <h2 className="text-[13px] font-heading font-semibold text-foreground-800">Learner Information</h2>
              </header>
              <div className="p-4 grid grid-cols-1 md:grid-cols-[220px_1fr] gap-4">
                <div className="flex flex-col items-center justify-center gap-2 rounded-lg bg-background-100 py-6">
                  <span className="w-16 h-16 rounded-full bg-foreground-200 flex items-center justify-center">
                    <i className="ri-user-line text-[28px] text-foreground-500" />
                  </span>
                  <p className="text-[13px] font-semibold text-foreground-800">{info?.name}</p>
                </div>
                <div>
                  <InfoRow label="Programme Name:" value={info?.programmeName ?? ''} />
                  <InfoRow label="Programme Start Date:" value={info?.programmeStartDate ?? ''} />
                  <InfoRow label="Planned End Date:" value={info?.plannedEndDate ?? ''} />
                  <InfoRow label="Programme Status:" value={info?.programmeStatus ?? ''} />
                  <InfoRow label="Employer:" value={info?.employer ?? ''} />
                  <InfoRow label="Manager:" value={info?.manager ?? ''} />
                  <InfoRow label="Mentor:" value={info?.mentor ?? ''} />
                </div>
              </div>
            </section>

            {/* 2. ILR */}
            {has('ilr') && (
            <Panel title="ILR" complete={!!status?.ilr} onSave={() => save('ilr')} saving={savingSection === 'ilr'} dirty={!!dirty.ilr}>
              {ILR_QUESTIONS.map((q) => (
                <YesNo key={q.key} name={`ilr-${q.key}`} label={q.label}
                  value={answers.ilr?.[q.key] ?? ''}
                  onChange={(v) => setSection('ilr', { ...answers.ilr, [q.key]: v })} />
              ))}
            </Panel>
            )}

            {/* 3. Extended ILR */}
            {has('extendedIlr') && (
            <Panel title="Extended ILR" complete={!!status?.extendedIlr} onSave={() => save('extendedIlr')} saving={savingSection === 'extendedIlr'} dirty={!!dirty.extendedIlr}>
              {EXTENDED_ILR_QUESTIONS.map((q) =>
                q.type === 'text' ? (
                  <label key={q.key} className="block py-2.5 border-b border-foreground-100 last:border-0">
                    <span className="text-[12px] text-foreground-700 block mb-1.5">{q.label}</span>
                    <textarea rows={3} value={answers.extendedIlr?.[q.key] ?? ''}
                      onChange={(e) => setSection('extendedIlr', { ...answers.extendedIlr, [q.key]: e.target.value })}
                      className={`${inputClass} resize-y`} />
                  </label>
                ) : (
                  <YesNo key={q.key} name={`extendedIlr-${q.key}`} label={q.label}
                    value={answers.extendedIlr?.[q.key] ?? ''}
                    onChange={(v) => setSection('extendedIlr', { ...answers.extendedIlr, [q.key]: v })} />
                ),
              )}
            </Panel>
            )}

            {/* 4. Functional Skills */}
            {has('functionalSkills') && (
            <Panel title="Functional Skills" complete={!!status?.functionalSkills} onSave={() => save('functionalSkills')} saving={savingSection === 'functionalSkills'} dirty={!!dirty.functionalSkills}>
              <p className="text-[13px] font-heading font-semibold text-foreground-800 mb-3">Initial Assessments</p>
              <AssessmentList title="Functional Skills Assessments" rows={fs.initialAssessments ?? []}
                onChange={(rows) => setSection('functionalSkills', { ...fs, initialAssessments: rows })} />
              <AssessmentList title="Diagnostic Assessments" rows={fs.diagnosticAssessments ?? []}
                onChange={(rows) => setSection('functionalSkills', { ...fs, diagnosticAssessments: rows })} />

              <p className="text-[13px] font-heading font-semibold text-foreground-800 mb-2 mt-5">Exemptions</p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {FS_SUBJECTS.map((subject) => (
                  <label key={subject} className="block rounded-lg border border-foreground-200/70 bg-background-100 p-2.5">
                    <span className="text-[12px] font-semibold text-foreground-700 block">{subject}</span>
                    <span className="text-[10px] text-foreground-500 block mb-1 mt-1">Status</span>
                    <select value={fs.exemptions?.[subject] ?? ''}
                      onChange={(e) => setSection('functionalSkills', { ...fs, exemptions: { ...fs.exemptions, [subject]: e.target.value } })}
                      className={inputClass}>
                      {EXEMPTION_OPTIONS.map((o) => <option key={o} value={o}>{o || 'Select…'}</option>)}
                    </select>
                  </label>
                ))}
              </div>

              <p className="text-[13px] font-heading font-semibold text-foreground-800 mb-2 mt-5">Results</p>
              {FS_RESULT_GROUPS.map((group) => (
                <div key={group.subject} className="mb-3">
                  <p className="text-[12px] font-semibold text-foreground-600 mb-2">{group.subject}</p>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    {group.items.map((item) => (
                      <div key={item} className="rounded-lg border border-foreground-200/70 bg-background-100 p-2.5">
                        <p className="text-[12px] font-semibold text-foreground-700 mb-1.5">{item}</p>
                        <label className="block mb-1.5">
                          <span className="text-[10px] text-foreground-500 block mb-1">Score</span>
                          <input value={fs.results?.[item]?.score ?? ''} placeholder="%"
                            onChange={(e) => setSection('functionalSkills', {
                              ...fs,
                              results: { ...fs.results, [item]: { ...(fs.results?.[item] ?? { score: '', assessmentDate: '' }), score: e.target.value } },
                            })}
                            className={inputClass} />
                        </label>
                        <label className="block">
                          <span className="text-[10px] text-foreground-500 block mb-1">Assessment Date</span>
                          <input type="date" value={fs.results?.[item]?.assessmentDate ?? ''}
                            onChange={(e) => setSection('functionalSkills', {
                              ...fs,
                              results: { ...fs.results, [item]: { ...(fs.results?.[item] ?? { score: '', assessmentDate: '' }), assessmentDate: e.target.value } },
                            })}
                            className={inputClass} />
                        </label>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </Panel>
            )}

            {/* 5. Functional Skills & Job Role Discussion */}
            {has('fsJobRoleDiscussion') && (
            <Panel title="Functional Skills & Job Role Discussion: Learner & Employer" complete={!!status?.fsJobRoleDiscussion}
              onSave={() => save('fsJobRoleDiscussion')} saving={savingSection === 'fsJobRoleDiscussion'} dirty={!!dirty.fsJobRoleDiscussion}>
              {FS_JOB_ROLE_QUESTIONS.map((q) => (
                <YesNo key={q.key} name={`fsJobRoleDiscussion-${q.key}`} label={q.label}
                  value={answers.fsJobRoleDiscussion?.[q.key] ?? ''}
                  onChange={(v) => setSection('fsJobRoleDiscussion', { ...answers.fsJobRoleDiscussion, [q.key]: v })} />
              ))}
            </Panel>
            )}

            {/* --- RPL And Experience --- */}

            {/* Prior Learning: rows added through the "New Item" modal. */}
            {has('priorLearning') && (
            <Panel title="Prior Learning" complete={!!status?.priorLearning}
              onSave={() => save('priorLearning')} saving={savingSection === 'priorLearning'} dirty={!!dirty.priorLearning}>
              <button type="button" onClick={() => setPriorEditing(-1)} className={`${btnSecondary} mb-3`}>
                <i className="ri-add-line" />New Item
              </button>
              <RecordTable
                headers={['Description', 'Duration Reduced', 'Cost Reduced', 'Impact', 'Off the Job Time Reduced', '']}
                empty="No records available."
                rows={priorItems.map((item, index) => [
                  item.description,
                  item.durationReduced,
                  item.costReduced,
                  item.impact,
                  item.offTheJobTimeReduced,
                  <span className="flex items-center gap-2 whitespace-nowrap">
                    <button type="button" onClick={() => setPriorEditing(index)} className="text-primary-600 hover:underline" aria-label="Edit">
                      <i className="ri-pencil-line" />
                    </button>
                    <button type="button" aria-label="Delete" className="text-red-600 hover:underline"
                      onClick={() => setSection('priorLearning', { items: priorItems.filter((_, i) => i !== index) })}>
                      <i className="ri-delete-bin-line" />
                    </button>
                  </span>,
                ])}
              />
              <p className="text-[11px] text-foreground-400 mt-2">{priorItems.length} item(s)</p>
            </Panel>
            )}

            {/* Recognition of Prior Learning and Experience */}
            {has('rplExperience') && (
            <Panel title="Recognition of Prior Learning and Experience" complete={!!status?.rplExperience}
              onSave={() => save('rplExperience')} saving={savingSection === 'rplExperience'} dirty={!!dirty.rplExperience}>
              {RPL_QUESTIONS.map((q) => (
                <YesNo key={q.key} name={`rplExperience-${q.key}`} label={q.label}
                  value={answers.rplExperience?.[q.key] ?? ''}
                  onChange={(v) => setSection('rplExperience', { ...answers.rplExperience, [q.key]: v })} />
              ))}
              <p className="text-[12px] text-foreground-600 mt-3 leading-relaxed">
                Please summarise your assessment of the relevant prior learning in the next section of this review
              </p>
              <p className="text-[12px] text-foreground-600 leading-relaxed">
                This will then form part of the signed compliance documents for this apprenticeship
              </p>
            </Panel>
            )}

            {/* Personal Learner Record (PLR) */}
            {has('plr') && (
            <Panel title="Personal Learner Record (PLR)" complete={!!status?.plr}
              onSave={() => save('plr')} saving={savingSection === 'plr'} dirty={!!dirty.plr}>
              <label className="block mb-3 max-w-md">
                <span className="text-[12px] text-foreground-700 block mb-1">
                  Unique Learner Number (ULN) <span className="text-red-500">*</span>
                </span>
                <input value={answers.plr?.uln ?? data.uln ?? ''} placeholder="9999999999"
                  onChange={(e) => setSection('plr', { ...answers.plr, uln: e.target.value })}
                  className={inputClass} />
              </label>

              <RecordTable
                headers={['Place of Study', 'Qualification Type', 'Subject', 'Level', 'Award Date', 'Credits', 'Grade', 'Record Type']}
                empty="There are no personal learning records."
                rows={(data.plrRecords ?? []).map((r) => [
                  r.placeOfStudy, r.qualificationType, r.subject, r.level,
                  r.awardDate ?? '', r.credits ?? '', r.grade, r.recordType,
                ])}
              />

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
                <div className="rounded-lg bg-background-100 p-3">
                  <p className="text-[12px] font-semibold text-foreground-700 mb-2">Prior Attainment Levels:</p>
                  <InfoRow label="Learner selected highest overall level of attainment:" value={answers.plr?.reportedAttainment ?? ''} />
                  <InfoRow label="Calculated highest overall level of attainment:" value={plrCalculated} />
                  {PLR_SUBJECTS.map((subject) => (
                    <InfoRow key={subject} label={`Calculated level of attainment for ${subject}:`} value={plrCalculated} />
                  ))}
                </div>
                <div>
                  <label className="block mb-3">
                    <span className="text-[12px] text-foreground-700 block mb-1">
                      Please select the highest-level qualification you wish to report in the ILR
                    </span>
                    <select value={answers.plr?.reportedAttainment ?? ''}
                      onChange={(e) => setSection('plr', { ...answers.plr, reportedAttainment: e.target.value })}
                      className={inputClass}>
                      <option value="">Select…</option>
                      {(data.priorAttainmentOptions ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </label>
                  {PLR_SUBJECTS.map((subject) => (
                    <div key={subject} className="mb-2">
                      <p className="text-[12px] font-semibold text-foreground-700">{subject}</p>
                      <div className="flex items-center gap-4 mt-1">
                        {(data.priorAttainmentSubjectLevels ?? []).map((level) => (
                          <label key={level} className="flex items-center gap-1.5 text-[12px] text-foreground-700 cursor-pointer">
                            <input type="radio" name={`plr-${subject}`} className="accent-primary-500"
                              checked={(answers.plr?.subjectLevels?.[subject] ?? 'None') === level}
                              onChange={() => setSection('plr', {
                                ...answers.plr,
                                subjectLevels: { ...answers.plr?.subjectLevels, [subject]: level },
                              })} />
                            {level}
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </Panel>
            )}

            {/* Skills Radar — the self-assessment the learner completed during
                onboarding, shown read-only. It is the learner's own record, so
                the reviewer adds a note rather than editing the answers. */}
            {has('skillsRadar') && (
            <Panel title="Skills Radar" complete={!!status?.skillsRadar} alwaysSavable
              onSave={() => save('skillsRadar')} saving={savingSection === 'skillsRadar'} dirty={!!dirty.skillsRadar}>
              <SkillsRadarReview radar={data.skillsRadar} />
              <label className="block mt-4">
                <span className="text-[12px] text-foreground-700 block mb-1.5">Notes on the learner’s skills self-assessment</span>
                <textarea rows={3} value={answers.skillsRadar?.notes ?? ''}
                  onChange={(e) => setSection('skillsRadar', { notes: e.target.value })}
                  className={`${inputClass} resize-y`} />
              </label>
            </Panel>
            )}

            {/* --- Workplace Health & Safety Declaration --- */}
            {has('healthSafetyVetting') && (
            <Panel title="Health & Safety Vetting" complete={!!status?.healthSafetyVetting}
              onSave={() => save('healthSafetyVetting')} saving={savingSection === 'healthSafetyVetting'} dirty={!!dirty.healthSafetyVetting}>
              {HEALTH_SAFETY_QUESTIONS.map((q) => (
                <YesNo key={q.key} name={`healthSafetyVetting-${q.key}`} label={q.label}
                  value={answers.healthSafetyVetting?.[q.key] ?? ''}
                  onChange={(v) => setSection('healthSafetyVetting', { ...answers.healthSafetyVetting, [q.key]: v })} />
              ))}
            </Panel>
            )}

            {/* --- ULN Privacy Notice & Learner Acknowledgement --- read-and-confirm:
                saving a panel records that the learner read / confirms it. */}
            {has('ulnPrivacyNotice') && (
            <Panel title="Unique Learner Number (ULN) Privacy Notice" complete={!!status?.ulnPrivacyNotice} alwaysSavable
              onSave={() => save('ulnPrivacyNotice', { noticeRead: 'Yes' })} saving={savingSection === 'ulnPrivacyNotice'} dirty={false}>
              <div className="max-w-3xl space-y-3 text-[13px] leading-relaxed text-foreground-700">
                <h3 className="font-heading text-[15px] font-semibold text-foreground-900">Unique Learner Number (ULN)</h3>
                <p className="text-foreground-500">{ULN_PRIVACY_INTRO}</p>
                <ol className="list-decimal space-y-2 pl-5">
                  {ULN_PRIVACY_POINTS.map((point, index) => (
                    <li key={point}>
                      {point}
                      {index === 1 && (
                        <ol className="mt-1.5 list-[lower-alpha] space-y-0.5 pl-5 text-foreground-600">
                          {ULN_PURPOSES.map((purpose) => <li key={purpose}>{purpose}</li>)}
                        </ol>
                      )}
                    </li>
                  ))}
                </ol>
                <p>{ULN_PRIVACY_FURTHER_INFO}</p>
              </div>
            </Panel>
            )}

            {has('learnerAcknowledgement') && (
            <Panel title="Learner Acknowledgement" complete={!!status?.learnerAcknowledgement} alwaysSavable
              onSave={() => save('learnerAcknowledgement', { acknowledged: 'Yes' })} saving={savingSection === 'learnerAcknowledgement'} dirty={false}>
              <p className="max-w-3xl text-[13px] leading-relaxed text-foreground-700">1. {LEARNER_ACKNOWLEDGEMENT}</p>
            </Panel>
            )}

            {/* 6. Comments */}
            {has('comments') && (
            <Panel title="Comments" complete={!!status?.comments} alwaysSavable
              onSave={() => save('comments')} saving={savingSection === 'comments'} dirty={!!dirty.comments}>
              <label className="block">
                <span className="text-[12px] text-foreground-700 block mb-1.5">Comments:</span>
                <textarea rows={4} value={answers.comments?.text ?? ''}
                  onChange={(e) => setSection('comments', { text: e.target.value })}
                  className={`${inputClass} resize-y`} />
              </label>
            </Panel>
            )}

            {/* 7. Programme Status */}
            {has('programmeStatus') && (
            <Panel title="Programme Status Selector" complete={!!status?.programmeStatus}
              onSave={() => save('programmeStatus')} saving={savingSection === 'programmeStatus'} dirty={!!dirty.programmeStatus}>
              <label className="block max-w-md">
                <span className="text-[12px] text-foreground-700 block mb-1.5">Programme Status</span>
                <select value={answers.programmeStatus?.status ?? info?.programmeStatus ?? ''}
                  onChange={(e) => setSection('programmeStatus', { status: e.target.value })}
                  className={inputClass}>
                  {data.programmeStatusOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
            </Panel>
            )}

            {/* Sign-off state, once the review is finished. */}
            {data.completed && (
              <section className="rounded-xl border border-foreground-200/70 bg-background-50 overflow-hidden">
                <header className="px-4 py-2.5 bg-background-100 border-b border-foreground-200/70">
                  <h2 className="text-[13px] font-heading font-semibold text-foreground-800">Declaration</h2>
                </header>
                <div className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {([
                    ['Learner signature', data.signatures.learner],
                    ['Provider signature', data.signatures.admin],
                  ] as const).map(([label, sig]) => (
                    <div key={label}>
                      <p className="text-[12px] text-foreground-500 mb-1.5">{label}</p>
                      {sig.signed ? (
                        <>
                          <img src={sig.signature} alt={label} className="h-14 max-w-[240px] object-contain px-3 py-1.5 border border-foreground-200 rounded-lg bg-white" />
                          <p className="text-[11px] text-foreground-600 mt-1">
                            {sig.name}
                            {sig.signedAt && <span className="text-foreground-400"> — {new Date(sig.signedAt).toLocaleDateString('en-GB')}</span>}
                          </p>
                        </>
                      ) : (
                        <p className="text-[12px] text-foreground-400 italic h-14 flex items-center">Not signed yet</p>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {signing && (
              <SignReviewModal
                kind={kind}
                learnerId={id}
                eventKey={eventKey}
                party="learner"
                defaultName={data.learnerInformation.name}
                signatures={data.signatures}
                onClose={() => setSigning(false)}
                onSigned={(signatures) => setData((prev) => (prev ? { ...prev, signatures } : prev))}
              />
            )}

            {priorEditing !== null && (
              <PriorLearningModal
                initial={priorEditing >= 0 ? priorItems[priorEditing] : EMPTY_PRIOR_LEARNING}
                onCancel={() => setPriorEditing(null)}
                onSave={(item) => {
                  const next = priorEditing >= 0
                    ? priorItems.map((row, i) => (i === priorEditing ? item : row))
                    : [...priorItems, item];
                  setSection('priorLearning', { items: next });
                  setPriorEditing(null);
                }}
              />
            )}

            <div className="flex items-center justify-end gap-2 pt-1 pb-4">
              <button onClick={() => navigate('/learner/onboarding/reviews')} className={btnSecondary}>Save and Close</button>
              <button onClick={finish} disabled={finishing} className={`${btnPrimary} ${finishing ? 'opacity-60 cursor-not-allowed' : ''}`}>
                {finishing ? <><i className="ri-loader-4-line animate-spin" />Finishing…</> : <><i className="ri-check-double-line" />Finish</>}
              </button>
            </div>
          </>
        )}
      </main>
    </WorkspaceShell>
  );
}
