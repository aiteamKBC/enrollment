import { useEffect, useMemo, useState } from 'react';
import { Flag } from 'lucide-react';
import { AppIcon } from '@/components/feature/AppIcon';
import { useWizard } from "../WizardContext.helpers";
import { COMPETENCE_LEVELS, competenceMeta, competenceScore } from '@/mocks/enrolment-console';
import { fetchKsbProfile, peekKsbProfile } from '@/api/curriculum';
import type { Ksb, KsbAssessment, RagLevel } from '../../types';
import { Modal } from '../../components/Modal';
import { FileList, inputClass, btnPrimary, btnSecondary, EmptyState } from '../../components/ui';
import { StepHeading } from './fields';
import { useText } from '../layout/textsContext';
import { useShowStepErrors } from "../stepErrors.helpers";
import { RowsSkeleton } from '@/components/feature/Skeletons';

/**
 * Skills Radar — a sequential self-assessment questionnaire.
 *
 * One KSB at a time ("Question N of M") on a 9-point scale, with an optional
 * note and evidence upload per question. The learner answers it; the enrolment
 * team sees the same list read-only (`readOnly` via the wizard's mode), because
 * this is the learner's own self-assessment and staff editing it would falsify
 * the record they are meant to review.
 */

/**
 * A rating above this is flagged. 5 (Consistently) and up says the learner is
 * already largely competent, and an apprenticeship must involve substantial new
 * learning — so the learner is warned, must say why in a note, and the
 * enrolment team sees the flag when they review the answers.
 */
const FLAG_ABOVE_SCORE = 4;

const HIGH_RATING_WARNING =
  'Rating yourself above 4 on many skills may affect your eligibility for this apprenticeship, which requires substantial new learning.';

function isHighRating(level?: string | null): boolean {
  return (competenceScore(level) ?? 0) > FLAG_ABOVE_SCORE;
}

function HighRatingFlag() {
  return (
    <span
      role="img"
      aria-label={`Flagged: rated above ${FLAG_ABOVE_SCORE}`}
      title={`Rated above ${FLAG_ABOVE_SCORE}`}
      className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 bg-amber-50 text-amber-600 ring-1 ring-amber-200"
    >
      <Flag aria-hidden="true" className="h-3.5 w-3.5" />
    </span>
  );
}

interface WorkingAnswer {
  level: RagLevel | null;
  note: string;
  evidenceFiles: string[];
}

function workingFrom(existing?: KsbAssessment): WorkingAnswer {
  return {
    level: existing?.level ?? null,
    note: existing?.note ?? '',
    evidenceFiles: existing?.evidenceFiles ?? [],
  };
}

export default function SkillsRadar() {
  const { draft, setSection, readOnly, board } = useWizard();
  const t = useText();
  const showErrors = useShowStepErrors();
  const sr = draft.skillsRadar;

  // The KSBs come from the profile authored against THIS learner's programme
  // (curriculum.ksb_profiles), not a fixed standard — a learner must only ever
  // self-assess against their own programme's competencies.
  const programme = board.programme.name || '';
  // Seeded from the cache the wizard bootstrap primed, so a step the learner has
  // already visited (or one opened after the bootstrap) renders its competencies
  // on the first frame. Starting empty-and-loading meant a spinner flashed on
  // every mount for a list that was already in memory — effects run after paint,
  // so even an instantly-resolving promise costs one visible frame.
  const cached = peekKsbProfile(programme);
  const [ksbs, setKsbs] = useState<Ksb[]>(() => (cached?.results as Ksb[]) ?? []);
  const [loading, setLoading] = useState(!cached && Boolean(programme));
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!programme) {
      setKsbs([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    // A cache hit is served synchronously above; re-reading it here would only
    // re-set identical state. A miss still shows the spinner it always did.
    const hit = peekKsbProfile(programme);
    if (hit) {
      setKsbs(hit.results as Ksb[]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    fetchKsbProfile(programme)
      .then((res) => {
        if (cancelled) return;
        setKsbs(res.results as Ksb[]);
      })
      .catch((e: Error) => { if (!cancelled) setLoadError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [programme]);

  // An unrated row per competency is seeded by WizardProvider rather than here:
  // completeness is judged for every step at once, so a learner who never opens
  // this one must still show it as outstanding.

  // Index of the question open in the modal; null when closed.
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [work, setWork] = useState<WorkingAnswer>(workingFrom());

  const answeredCount = useMemo(
    () => ksbs.filter((k) => sr.assessments[k.id]?.level).length,
    [ksbs, sr.assessments]
  );
  const flaggedCount = useMemo(
    () => ksbs.filter((k) => isHighRating(sr.assessments[k.id]?.level)).length,
    [ksbs, sr.assessments]
  );

  const open = (index: number) => {
    const ksb = ksbs[index];
    setWork(workingFrom(sr.assessments[ksb.id]));
    setOpenIndex(index);
  };

  /** Persist the open question, then advance. Returns the next index (or null). */
  const commit = (index: number): void => {
    const ksb = ksbs[index];
    const existing = sr.assessments[ksb.id];
    const next: KsbAssessment = {
      ksbId: ksb.id,
      level: work.level,
      note: work.note,
      evidenceFiles: work.evidenceFiles,
      // The questionnaire doesn't capture an action plan; keep any plan the
      // enrolment team previously attached rather than dropping it.
      actionPlan: existing?.actionPlan ?? null,
    };
    setSection('skillsRadar', { ...sr, assessments: { ...sr.assessments, [ksb.id]: next } });
  };

  const confirm = () => {
    if (openIndex == null) return;
    commit(openIndex);
    // Walk straight into the next unanswered question so the learner can work
    // through all of them without reopening the list each time.
    const nextIndex = openIndex + 1;
    if (nextIndex < ksbs.length) {
      open(nextIndex);
    } else {
      setOpenIndex(null);
    }
  };

  const openKsb = openIndex != null ? ksbs[openIndex] : null;
  const highRating = isHighRating(work.level);

  return (
    <div>
      <StepHeading
        title={t('block.skillsRadar.title')}
        subtitle={programme ? `${programme} [${ksbs.length}]` : 'No programme assigned'}
      />

      {loading && <RowsSkeleton rows={4} avatar={false} className="py-4" />}
      {!loading && loadError && (
        <p className="text-[13px] text-red-600 py-6">
          <i className="ri-error-warning-line mr-1.5" />{loadError}
        </p>
      )}
      {!loading && !loadError && ksbs.length === 0 && (
        <div className="py-6">
          <EmptyState text={
            programme
              ? `No competencies have been authored for ${programme} yet. Please contact your programme team.`
              : 'This learner has no programme assigned, so there are no competencies to rate.'
          } />
        </div>
      )}

      {ksbs.length > 0 && (
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p className="text-[12px] text-foreground-500">
          {readOnly
            ? 'The learner’s self-assessment. Read-only here — only the learner can change their own answers.'
            : t('block.skillsRadar.instructions')}
        </p>
        <span className="flex items-center gap-3 shrink-0">
          {flaggedCount > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700 ring-1 ring-amber-200">
              <Flag aria-hidden="true" className="h-3 w-3" />{flaggedCount} rated above {FLAG_ABOVE_SCORE}
            </span>
          )}
          <span className="text-[12px] font-semibold text-foreground-600">{answeredCount} of {ksbs.length} answered</span>
        </span>
      </div>
      )}

      {/* Progress */}
      {ksbs.length > 0 && (
      <div className="h-1.5 bg-background-200 rounded-full overflow-hidden mb-5">
        <div className="h-full bg-primary-500 rounded-full transition-all duration-300" style={{ width: `${(answeredCount / ksbs.length) * 100}%` }} />
      </div>
      )}

      {!readOnly && answeredCount < ksbs.length && (
        <button
          className={`${btnPrimary} mb-4`}
          onClick={() => open(ksbs.findIndex((k) => !sr.assessments[k.id]?.level))}
        >
          <AppIcon className="ri-play-line" />{answeredCount === 0 ? 'Start assessment' : 'Continue assessment'}
        </button>
      )}

      {/* Question list — numbered, showing each answer's score */}
      <div className="border border-foreground-200/70 rounded-xl divide-y divide-foreground-100 overflow-hidden">
        <div className="flex items-center gap-3 px-3 py-2 bg-background-100/60">
          <span className="text-[11px] font-semibold text-foreground-500 uppercase tracking-wider flex-1">Skill description</span>
          <span className="text-[11px] font-semibold text-foreground-500 uppercase tracking-wider">Answer</span>
        </div>
        {ksbs.map((ksb, i) => {
          const level = sr.assessments[ksb.id]?.level ?? null;
          const meta = competenceMeta(level);
          const score = competenceScore(level);
          // After a refused Next, every competency still to rate is marked.
          const unrated = showErrors && !readOnly && !level;
          return (
            <div key={ksb.id} className={`flex items-center gap-3 px-3 py-2.5${unrated ? ' bg-red-50 ring-1 ring-inset ring-red-500' : ''}`}>
              <span className="text-[12px] font-semibold text-foreground-400 w-7 shrink-0 text-right">{i + 1}.</span>
              <span className="flex-1 min-w-0">
                <span className="text-[13px] text-foreground-800">{ksb.codes.join(', ')} {ksb.title}</span>
                <span className="block text-[11px] text-foreground-400 mt-0.5">{ksb.theme} · {ksb.kind}</span>
              </span>
              {score != null && meta ? (
                <span className={`text-[11px] font-bold w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${meta.tintBg} ${meta.tintText}`} title={meta.label}>
                  {score}
                </span>
              ) : unrated ? (
                <span className="text-[11px] text-red-600 shrink-0">Please answer</span>
              ) : (
                <span className="text-[11px] text-foreground-300 shrink-0">—</span>
              )}
              {/* Fixed-width slot so the scores stay in one column. */}
              <span className="w-7 shrink-0">{isHighRating(level) && <HighRatingFlag />}</span>
              <button
                onClick={() => open(i)}
                aria-invalid={unrated || undefined}
                className={`${btnSecondary} !py-1 !px-3 !text-[11px] shrink-0`}
              >
                <AppIcon className={readOnly ? 'ri-eye-line' : 'ri-edit-line'} />{readOnly ? 'View' : level ? 'Edit' : 'Answer'}
              </button>
            </div>
          );
        })}
      </div>

      {/* Question modal */}
      {openIndex != null && openKsb && (
        <Modal
          title={`Question ${openIndex + 1} of ${ksbs.length}`}
          onClose={() => setOpenIndex(null)}
          size="max-w-3xl"
          /* Each question starts at the top rather than inheriting the last scroll. */
          scrollResetKey={openIndex}
          footer={
            readOnly ? (
              <button className={btnSecondary} onClick={() => setOpenIndex(null)}>Close</button>
            ) : (
              <>
                <button className={btnSecondary} onClick={() => setOpenIndex(null)}>Cancel</button>
                <button className={btnPrimary} onClick={confirm} disabled={!work.level || (highRating && !work.note.trim())}>
                  <AppIcon className="ri-check-line" />{openIndex + 1 < ksbs.length ? 'Confirm & next' : 'Confirm'}
                </button>
              </>
            )
          }
        >
          {/* Position within the questionnaire */}
          <div className="h-1.5 bg-background-200 rounded-full overflow-hidden mb-4">
            <div className="h-full bg-primary-500 rounded-full transition-all duration-300" style={{ width: `${((openIndex + 1) / ksbs.length) * 100}%` }} />
          </div>

          <p className="text-[14px] text-foreground-900 mb-4">
            <span className="font-semibold mr-1.5">{openIndex + 1}.</span>
            {openKsb.codes.join(', ')} {openKsb.title}
          </p>

          <div className="border border-foreground-200/70 rounded-lg overflow-hidden mb-4">
            <p className="text-[12px] font-medium text-foreground-700 px-3 py-2 bg-background-100/60 border-b border-foreground-100">
              {t('block.skillsRadar.choose')}
            </p>
            <div className="divide-y divide-foreground-100">
              {COMPETENCE_LEVELS.map((lvl) => {
                const selected = work.level === lvl.level;
                return (
                  <label
                    key={lvl.level}
                    className={`flex items-center gap-3 px-3 py-2.5 transition-smooth ${readOnly ? '' : 'cursor-pointer hover:bg-background-50'} ${selected ? lvl.tintBg : ''}`}
                  >
                    <input
                      type="radio"
                      name={`competence-${openKsb.id}`}
                      checked={selected}
                      disabled={readOnly}
                      onChange={() => setWork((w) => ({ ...w, level: lvl.level }))}
                      className="accent-primary-500 shrink-0"
                    />
                    <span className="flex-1 text-[12px] text-foreground-700">
                      <span className={`font-semibold ${selected ? lvl.tintText : 'text-foreground-800'}`}>{lvl.label}</span>
                      {' – '}{lvl.help}
                    </span>
                    <span className={`text-[11px] font-bold w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${lvl.tintBg} ${lvl.tintText}`}>
                      {lvl.score}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>

          {highRating && (
            <div role="status" className="mb-4 flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
              <Flag aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p className="text-[12px] leading-relaxed text-amber-800">
                {readOnly ? (
                  <>
                    <span className="font-semibold">Flagged: the learner rated this above {FLAG_ABOVE_SCORE}.</span>{' '}
                    High self-ratings may affect their eligibility for this apprenticeship, which requires substantial new learning.
                  </>
                ) : (
                  <>
                    <span className="font-semibold">{HIGH_RATING_WARNING}</span>{' '}
                    Please add a note explaining this rating.
                  </>
                )}
              </p>
            </div>
          )}

          <div className="mb-4">
            <label htmlFor={`competence-note-${openKsb.id}`} className="block text-[12px] font-medium text-foreground-700 mb-1.5">
              {t('block.skillsRadar.note')}{highRating && !readOnly && (
                <><span aria-hidden="true" className="ml-0.5 text-red-500">*</span><span className="sr-only"> (required)</span></>
              )}:
            </label>
            <textarea
              id={`competence-note-${openKsb.id}`}
              rows={3}
              value={work.note}
              readOnly={readOnly}
              required={highRating && !readOnly}
              aria-describedby={highRating && !readOnly ? `competence-note-hint-${openKsb.id}` : undefined}
              onChange={(e) => setWork((w) => ({ ...w, note: e.target.value }))}
              className={inputClass}
            />
            {highRating && !readOnly && (
              <p id={`competence-note-hint-${openKsb.id}`} className={`mt-1.5 text-[12px] ${work.note.trim() ? 'text-foreground-500' : 'text-red-600'}`}>
                A note is required for ratings above {FLAG_ABOVE_SCORE}.
              </p>
            )}
          </div>

          <div>
            <label className="block text-[12px] font-medium text-foreground-700 mb-1.5">{t('block.skillsRadar.upload')}</label>
            {!readOnly && (
              <label className="inline-flex items-center gap-2 px-3 py-2 text-[12px] bg-background-100 text-foreground-600 rounded-lg border border-background-200 hover:bg-background-200 transition-smooth cursor-pointer mb-2">
                <AppIcon className="ri-folder-open-line" />Select file…
                <input
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const names = Array.from(e.target.files ?? []).map((f) => f.name);
                    setWork((w) => ({ ...w, evidenceFiles: [...w.evidenceFiles, ...names] }));
                    e.target.value = '';
                  }}
                />
              </label>
            )}
            {readOnly && work.evidenceFiles.length === 0 ? (
              <EmptyState text="No evidence uploaded" />
            ) : (
              <FileList
                files={work.evidenceFiles.map((n, i) => ({ id: `${n}-${i}`, name: n }))}
                onDelete={readOnly ? undefined : (id) => setWork((w) => ({ ...w, evidenceFiles: w.evidenceFiles.filter((_, i) => `${w.evidenceFiles[i]}-${i}` !== id) }))}
                emptyText="No evidence uploaded"
              />
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
