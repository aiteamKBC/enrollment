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
import { isPcpRadar, radarProfileProgramme, resolvePcpRadar } from '@/features/programme-suitability/skills-radar/allocation';
import { isMmRadar, mmRadarProfileProgramme, resolveMmRadar } from '@/features/programme-suitability/skills-radar/marketingManager';
import { isMeRadar, meRadarProfileProgramme, resolveMeRadar } from '@/features/programme-suitability/skills-radar/marketingExecutive';
import { isApmRadar, isApmStaffReview, apmRadarProfileProgramme, resolveApmRadar } from '@/features/programme-suitability/skills-radar/associateProjectManager';
import styles from './SkillsRadar.module.css';
import { useLearnerStepPresentation } from '@/features/learner-enrolment/stepPresentation';

/**
 * Skills Radar: a sequential self-assessment questionnaire.
 *
 * One KSB at a time ("Question N of M") on a 9-point scale, with an optional
 * team sees the same list read-only (`readOnly` via the wizard's mode), because
 * the record they are meant to review.
 */

/**
 * already largely competent, and an apprenticeship must involve substantial new
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
  const learnerPresentation = useLearnerStepPresentation();
  const { draft, setSection, readOnly, board, suitability, modules, radarCatalogue } = useWizard();
  const managed = Boolean(radarCatalogue?.enabled && !(radarCatalogue.data && !radarCatalogue.data.catalogue));
  const pcp = isPcpRadar(suitability?.state);
  const mm = isMmRadar(suitability?.state);
  const me = isMeRadar(suitability?.state);
  const apm = isApmRadar(suitability?.state) || isApmStaffReview(readOnly, board.programme.name || '', suitability?.state);
  const moduleBased = managed || pcp || mm || me || apm;
  const t = useText();
  const showErrors = useShowStepErrors();
  const sr = draft.skillsRadar;

  // The KSBs come from the profile authored against THIS learner's programme
  // (curriculum.ksb_profiles), not a fixed standard; a learner must only ever
  // self-assess against their own programme's competencies.
  const programmeLookup = apm ? apmRadarProfileProgramme(suitability?.state) : me ? meRadarProfileProgramme(suitability?.state) : mm ? mmRadarProfileProgramme(suitability?.state) : radarProfileProgramme(board.programme.name || '', suitability?.state);
  const programme = managed ? modules?.data?.configuration.programmes.find((p) => p.id === radarCatalogue?.data?.programmeId)?.title || board.programme.name || '' : moduleBased ? modules?.data?.configuration.programmes.find((p) => p.id === (apm ? 'APM_L4' : me ? 'ME_L4' : mm ? 'MM_L6' : 'PCP_L6'))?.title || (apm ? 'Associate Project Manager Level 4' : me ? 'Marketing Executive Level 4' : mm ? 'Marketing Manager Level 6' : 'Project Controls Professional Level 6') : board.programme.name || '';
  // already visited (or one opened after the bootstrap) renders its competencies
  // on the first frame. Starting empty-and-loading meant a spinner flashed on
  // every mount for a list that was already in memory; effects run after paint,
  // so even an instantly-resolving promise costs one visible frame.
  const cached = peekKsbProfile(programmeLookup);
  const [profileKsbs, setKsbs] = useState<Ksb[]>(() => (cached?.results as Ksb[]) ?? []);
  const [loadedProgramme, setLoadedProgramme] = useState(cached ? programmeLookup : '');
  const [profileLoading, setLoading] = useState(!cached && Boolean(programmeLookup));
  const [profileError, setLoadError] = useState<string | null>(null);
  const loading = managed ? Boolean(radarCatalogue?.loading) : profileLoading;
  const loadError = managed ? radarCatalogue?.error : profileError;

  useEffect(() => {
    if (managed) return;
    if (!programmeLookup) {
      setKsbs([]);
      setLoadedProgramme('');
      setLoading(false);
      return;
    }
    let cancelled = false;
    // A cache hit is served synchronously above; re-reading it here would only
    // re-set identical state. A miss still shows the spinner it always did.
    const hit = peekKsbProfile(programmeLookup);
    if (hit) {
      setKsbs(hit.results as Ksb[]);
      setLoadedProgramme(programmeLookup);
      setLoadError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    fetchKsbProfile(programmeLookup)
      .then((res) => {
        if (cancelled) return;
        setKsbs(res.results as Ksb[]);
        setLoadedProgramme(programmeLookup);
      })
      .catch((e: Error) => { if (!cancelled) setLoadError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [programmeLookup, managed]);

  const grouping = useMemo(() => managed ? radarCatalogue?.grouping : pcp ? resolvePcpRadar(loading || loadedProgramme !== programmeLookup ? null : profileKsbs, modules?.data, loadError || modules?.error) : mm ? resolveMmRadar(loading || loadedProgramme !== programmeLookup ? null : profileKsbs, modules?.data, loadError || modules?.error) : me ? resolveMeRadar(loading || loadedProgramme !== programmeLookup ? null : profileKsbs, modules?.data, loadError || modules?.error) : apm ? resolveApmRadar(loading || loadedProgramme !== programmeLookup ? null : profileKsbs, modules?.data, loadError || modules?.error) : null,
    [managed, radarCatalogue?.grouping, pcp, mm, me, apm, loading, loadedProgramme, programmeLookup, profileKsbs, modules?.data, modules?.error, loadError]);
  const coverage = useMemo(() => {
    const source = grouping?.modules ?? [];
    const byId = new Map<string, { ksb: Ksb; modules: string[] }>();
    for (const module of source) for (const ksb of module.questions) {
      const current = byId.get(ksb.id);
      if (current) { if (!current.modules.includes(module.title)) current.modules.push(module.title); }
      else byId.set(ksb.id, { ksb, modules: [module.title] });
    }
    return [...byId.values()];
  }, [grouping]);
  const ksbs = useMemo(() => grouping ? coverage.map((item) => item.ksb) : managed ? [] : profileKsbs, [grouping, coverage, managed, profileKsbs]);
  const coverageById = useMemo(() => new Map(coverage.map((item) => [item.ksb.id, item.modules])), [coverage]);
  const coverageByCode = useMemo(() => new Map((grouping?.modules ?? []).flatMap((module) => module.questions.flatMap((question) => question.codes.map((code) => [code, module.title] as const)))), [grouping]);
  const moduleCoverage = useMemo(() => (grouping?.modules ?? []).map((module) => {
    const ids = new Set(module.questions.map((ksb) => ksb.id));
    return {
      title: module.title,
      count: ids.size,
      answered: [...ids].filter((id) => sr.assessments[id]?.level).length,
    };
  }), [grouping, sr.assessments]);
  const coverageAvailable = coverage.some((item) => item.modules.length > 0);
  const questionIndex = useMemo(() => new Map(ksbs.map((ksb, index) => [ksb.id, index])), [ksbs]);
  const [categoryFilter, setCategoryFilter] = useState<'all' | 'Knowledge' | 'Skill' | 'Behaviour'>('all');

  // An unrated row per competency is seeded by WizardProvider rather than here:
  // completeness is judged for every step at once, so a learner who never opens
  // this one must still show it as outstanding.

  // Keep the open question tied to its profile ID if module grouping changes.
  const [openKsbId, setOpenKsbId] = useState<string | null>(null);
  const position = openKsbId == null ? -1 : ksbs.findIndex((ksb) => ksb.id === openKsbId);
  const openIndex = position < 0 ? null : position;
  const [work, setWork] = useState<WorkingAnswer>(workingFrom());

  const visibleKsbs = useMemo(() => categoryFilter === 'all' ? ksbs : ksbs.filter((k) => k.kind === categoryFilter), [ksbs, categoryFilter]);
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
    if (!ksb) return;
    setWork(workingFrom(sr.assessments[ksb.id]));
    setOpenKsbId(ksb.id);
  };

  /** Apply the confirmed answer using the existing profile ID. */
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
    // through all of them without reopening the list each time.
    const nextIndex = openIndex + 1;
    if (nextIndex < ksbs.length) {
      open(nextIndex);
    } else {
      setOpenKsbId(null);
    }
  };

  const openKsb = openIndex != null ? ksbs[openIndex] : null;
  const highRating = isHighRating(work.level);

  const renderQuestion = (ksb: Ksb, i: number) => {
    const level = sr.assessments[ksb.id]?.level ?? null;
    const actionLabel = readOnly ? 'View' : level !== null ? 'Edit' : learnerPresentation ? 'Assess' : 'Answer';
    const meta = competenceMeta(level);
    const score = competenceScore(level);
    const linkedModules = coverageById.get(ksb.id) ?? ksb.codes.flatMap((code) => coverageByCode.has(code) ? [coverageByCode.get(code)!] : []);
    const coverageLabel = linkedModules.length ? null : grouping?.status === 'ready' ? 'No modules mapped' : 'Not available';
    // After a refused Next, every competency still to rate is marked.
    const unrated = showErrors && !readOnly && !level;
    return (
      <div key={ksb.id} role="row" className={`${styles.row}${unrated ? ` ${styles.unrated}` : ''}`}>
        <span role="cell" className={styles.description}>
          <span className={styles.number}>{i + 1}.</span>
          <span><span className={styles.question}>{ksb.codes.join(', ')} {ksb.title}</span>
            <span className={styles.meta}>{ksb.theme} &middot; {ksb.kind}</span>
          </span>
        </span>
        <div role="cell" className={styles.coverageCell}>
          <span className={styles.mobileLabel}>Covered by Modules</span>
          <div className={styles.chips}>{linkedModules.map((name) => <span key={name} className={styles.chip}>{name}</span>)}</div>
          {coverageLabel && <span className="text-foreground-400">{coverageLabel}</span>}
        </div>
        <div role="cell" className={styles.answerCell}>
          <span className={styles.mobileLabel}>Answer</span>
        {score != null && meta ? (
          <span className={`text-[11px] font-bold w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${meta.tintBg} ${meta.tintText}`} title={meta.label}>
            {score}
          </span>
        ) : unrated ? (
          <span className="text-[11px] text-red-600 shrink-0">Please answer</span>
        ) : (
          <span className="text-[11px] text-foreground-300 shrink-0">Not rated</span>
        )}
        {isHighRating(level) && <HighRatingFlag />}
        <button
          onClick={() => open(i)}
          aria-label={moduleBased ? `${actionLabel} ${ksb.codes[0]}` : undefined}
          aria-invalid={unrated || undefined}
          className={`${btnSecondary} ${styles.action}`}
        >
          <AppIcon className={readOnly ? 'ri-eye-line' : 'ri-edit-line'} />{actionLabel}
        </button>
        </div>
      </div>
    );
  };

  return (
    <div className={styles.radar}>
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
      {grouping && !loading && !loadError && grouping.status !== 'ready' && (
        <p role={grouping.status === 'loading' ? 'status' : 'alert'} className="my-4 text-sm text-foreground-700">{grouping.message}</p>
      )}
      {!moduleBased && !loading && !loadError && ksbs.length === 0 && (
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
            ? "The learner's self-assessment. Read-only here; only the learner can change their own answers."
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

      {ksbs.length > 0 && <>
        <div className={styles.categories}>
          {(['Knowledge', 'Skill', 'Behaviour'] as const).map((kind) => {
            const items = ksbs.filter((k) => k.kind === kind); const answered = items.filter((k) => sr.assessments[k.id]?.level).length;
            const pct = items.length ? Math.round(answered / items.length * 100) : 0; const tone = pct === 100 ? 'bg-emerald-500' : pct > 0 ? 'bg-amber-500' : 'bg-red-500';
            return <button type="button" key={kind} onClick={() => setCategoryFilter(categoryFilter === kind ? 'all' : kind)} aria-pressed={categoryFilter === kind} className={`${styles.category} ${categoryFilter === kind ? styles.selected : ''}`}>
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground-800"><span className={`h-2.5 w-2.5 rounded-full ${tone}`} />{kind === 'Skill' ? 'Skills' : kind}</div><div className="mt-1 flex items-center justify-between gap-2 text-xs text-foreground-600"><span>{answered} / {items.length} answered</span><span className="font-semibold">{pct}%</span></div><div className="mt-2 h-1.5 rounded-full bg-primary-100"><div className={styles.progressFill} style={{width: `${pct}%`}} /></div>
            </button>;
          })}
        </div>
        <div className={styles.filters}><button type="button" onClick={() => setCategoryFilter('all')} aria-pressed={categoryFilter === 'all'} className={`${styles.allFilter} ${categoryFilter === 'all' ? styles.activeFilter : ''}`}>All KSBs ({ksbs.length})</button></div>
        {moduleCoverage.length > 0 && <nav aria-label="Selected modules" className={styles.moduleCoverage}>
          <label className="sr-only" htmlFor="selected-module-skills">Selected module skills assessments</label><input id="selected-module-skills" className="sr-only" readOnly />
          <h3>Coverage by selected modules</h3>
          <div className={styles.moduleGrid}>{moduleCoverage.map((module) => <div key={module.title} className={styles.moduleCard}>
            <span className={styles.moduleName}>{module.title}</span>
            <span className={styles.moduleCount}>{module.answered} of {module.count} answered</span>
          </div>)}</div>
        </nav>}
      </>}

      {/* Progress */}
      {ksbs.length > 0 && (
      <div className={styles.overallProgress}>
        <div className={styles.progressFill} style={{ width: `${(answeredCount / ksbs.length) * 100}%` }} />
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

      {/* One assessment row per stable KSB ID, for both learner and staff views. */}
      {(grouping?.status === 'ready' || !moduleBased) && <div role="table" aria-label="Skills assessments" className={styles.table}>
        <div role="row" className={styles.tableHeader}>
          <span role="columnheader">SKILL DESCRIPTION</span>
          <span role="columnheader">COVERED BY MODULES</span>
          <span role="columnheader">ANSWER</span>
        </div>
        {visibleKsbs.map((ksb) => renderQuestion(ksb, questionIndex.get(ksb.id)!))}
      </div>}

      {/* Question modal */}
      {openIndex != null && openKsb && (
        <Modal
          title={`Question ${openIndex + 1} of ${ksbs.length}`}
          onClose={() => setOpenKsbId(null)}
          size="max-w-3xl"
          /* Each question starts at the top rather than inheriting the last scroll. */
          scrollResetKey={openIndex}
          footer={
            readOnly ? (
              <button className={btnSecondary} onClick={() => setOpenKsbId(null)}>Close</button>
            ) : (
              <>
                <button className={btnSecondary} onClick={() => setOpenKsbId(null)}>Cancel</button>
                <button className={btnPrimary} onClick={confirm} disabled={!work.level || (highRating && !work.note.trim())}>
                  <AppIcon className="ri-check-line" />{openIndex + 1 < ksbs.length ? 'Confirm & next' : 'Confirm'}
                </button>
              </>
            )
          }
        >
          {/* Position within the questionnaire */}
          <div className="h-1.5 bg-background-200 rounded-full overflow-hidden mb-4">
            <div className={styles.progressFill} style={{ width: `${((openIndex + 1) / ksbs.length) * 100}%` }} />
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
                      {' '}{lvl.help}
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
                <AppIcon className="ri-folder-open-line" />Select file
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
