import { useEffect, useMemo, useState } from 'react';
import { Flag, Info } from 'lucide-react';
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
import { bindImpactModules, weightedReadiness, type ImpactModule } from '@/features/programme-suitability/skills-radar/moduleImpact';

/**
 * Skills Radar: a sequential self-assessment questionnaire.
 *
 * One KSB at a time ("Question N of M") on a 9-point scale. Historical notes
 * and evidence remain in the saved answer and visible to staff in read-only mode.
 */

/**
 * Staff can see which self-ratings are above four during their review.
 */
const FLAG_ABOVE_SCORE = 4;

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
  const { draft, setSection, readOnly, board, suitability, modules, radarCatalogue, radarImpact, radarImpactError, retryRadarImpact } = useWizard();
  const managed = Boolean(radarCatalogue?.enabled && !(radarCatalogue.data && !radarCatalogue.data.catalogue));
  const pcp = isPcpRadar(suitability?.state);
  const mm = isMmRadar(suitability?.state);
  const me = isMeRadar(suitability?.state);
  const apm = isApmRadar(suitability?.state) || isApmStaffReview(readOnly, board.programme.name || '', suitability?.state);
  const moduleBased = managed || pcp || mm || me || apm;
  const impact = radarImpact ?? null;
  const impactError = radarImpactError ?? '';
  const t = useText();
  const showErrors = useShowStepErrors();
  const sr = draft.skillsRadar;

  // The KSBs come from the profile authored against THIS learner's programme
  // (curriculum.ksb_profiles), not a fixed standard; a learner must only ever
  // self-assess against their own programme's competencies.
  const programmeLookup = apm ? apmRadarProfileProgramme(suitability?.state) : me ? meRadarProfileProgramme(suitability?.state) : mm ? mmRadarProfileProgramme(suitability?.state) : radarProfileProgramme(board.programme.name || '', suitability?.state);
  const programme = managed ? modules?.data?.configuration.programmes.find((p) => p.id === radarCatalogue?.data?.programmeId)?.title || board.programme.name || '' : moduleBased ? modules?.data?.configuration.programmes.find((p) => p.id === (apm ? 'APM_L4' : me ? 'ME_L4' : mm ? 'MM_L6' : 'PCP_L6'))?.title || (apm ? 'Associate Project Manager Level 4' : me ? 'Marketing Executive Level 4' : mm ? 'Marketing Manager Level 6' : 'Project Controls Professional Level 6') : board.programme.name || '';
  const selectedProgrammeId = suitability?.state?.selectedProgramme || modules?.data?.selectedProgramme;
  const programmeLevel = suitability?.state?.configuration?.programmes.find((item) => item.id === selectedProgrammeId)?.level
    ?? selectedProgrammeId?.match(/_L(\d+)$/)?.[1];
  const programmeLabel = programme && programmeLevel && !/\blevel\s*\d+/i.test(programme)
    ? `${programme} · Level ${programmeLevel}` : programme;
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
  const answerIdByCode = useMemo(() => {
    const lookup = new Map<string, string>();
    const duplicates = new Set<string>();
    for (const question of grouping?.questions ?? profileKsbs) for (const code of question.codes) {
      if (lookup.has(code) && lookup.get(code) !== question.id) duplicates.add(code);
      else lookup.set(code, question.id);
    }
    for (const code of duplicates) lookup.delete(code);
    return lookup;
  }, [grouping, profileKsbs]);
  const mappedModules = useMemo(() => bindImpactModules(grouping?.modules ?? [], grouping?.questions ?? [],
    impact, modules?.data?.selectedProgramme, sr.standardId ?? ''),
  [grouping, impact, modules?.data?.selectedProgramme, sr.standardId]);
  const savedAssessments = useMemo(() => Object.fromEntries(Object.entries(impact?.savedLevels ?? {}).map(([id, level]) =>
    [id, { ksbId: id, level: level as KsbAssessment['level'], evidenceFiles: [], actionPlan: null }])), [impact?.savedLevels]);
  const readinessByModule = useMemo(() => new Map(mappedModules.flatMap((module) => {
    const record = module.impact as ImpactModule | undefined;
    return record?.status === 'ready'
      ? [[module.id, weightedReadiness(record, savedAssessments, answerIdByCode)] as const] : [];
  })), [mappedModules, savedAssessments, answerIdByCode]);
  // The assessment's published questions remain authoritative. A new local
  // revision can cover fewer unique KSBs than the original questionnaire.
  const ksbs = useMemo(() => {
    if (!grouping) return managed ? [] : profileKsbs;
    const seen = new Set<string>();
    return grouping.questions.filter((question) => {
      if (seen.has(question.id)) return false;
      seen.add(question.id);
      return true;
    });
  }, [grouping, managed, profileKsbs]);
  const coverageById = useMemo(() => {
    const byId = new Map<string, string[]>();
    for (const module of mappedModules) {
      if (impact && module.impact?.status !== 'ready') continue;
      for (const question of module.questions) {
        const modules = byId.get(question.id) ?? [];
        if (!modules.includes(module.title)) modules.push(module.title);
        byId.set(question.id, modules);
      }
    }
    return byId;
  }, [mappedModules, impact]);
  const coverageByCode = useMemo(() => new Map(mappedModules.filter((module) => !impact || module.impact?.status === 'ready')
    .flatMap((module) => module.questions.flatMap((question) => question.codes.map((code) => [code, module.title] as const)))), [mappedModules, impact]);
  const moduleCoverage = useMemo(() => mappedModules.map((module) => {
    const ids = new Set(module.questions.map((ksb) => ksb.id));
    const record = module.impact as ImpactModule | undefined;
    const readiness = readinessByModule.get(module.id) ?? null;
    return {
      id: module.id, title: module.title, record, readiness,
      count: ids.size,
      answered: [...ids].filter((id) => sr.assessments[id]?.level).length,
    };
  }), [mappedModules, sr.assessments, readinessByModule]);
  const orderedModuleCoverage = useMemo(() => [
    ...moduleCoverage.filter((module) => module.readiness?.score != null),
    ...moduleCoverage.filter((module) => module.readiness?.score == null),
  ], [moduleCoverage]);
  const questionIndex = useMemo(() => new Map(ksbs.map((ksb, index) => [ksb.id, index])), [ksbs]);

  // An unrated row per competency is seeded by WizardProvider rather than here:
  // completeness is judged for every step at once, so a learner who never opens
  // this one must still show it as outstanding.

  // Keep the open question tied to its profile ID if module grouping changes.
  const [openKsbId, setOpenKsbId] = useState<string | null>(null);
  const [showScoreHelp, setShowScoreHelp] = useState(false);
  const position = openKsbId == null ? -1 : ksbs.findIndex((ksb) => ksb.id === openKsbId);
  const openIndex = position < 0 ? null : position;
  const [work, setWork] = useState<WorkingAnswer>(workingFrom());

  const answeredCount = useMemo(
    () => ksbs.filter((k) => sr.assessments[k.id]?.level).length,
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
    const coverageLabel = linkedModules.length ? null : impact ? 'No verified module mapping' : grouping?.status === 'ready' ? 'Mapping unavailable' : 'Not available';
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
          <div className={styles.chips}>{linkedModules.map((name) =>
            <span key={name} className={styles.chip}>{name}</span>
          )}</div>
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
        {readOnly && isHighRating(level) && <HighRatingFlag />}
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
        subtitle={programmeLabel || 'No programme assigned'}
      />
      <p className={styles.pageDescription}>{readOnly
        ? "The Skills Radar records the learner's self-assessed knowledge, skills and behaviours (KSBs). Their responses help inform the initial assessment, identify potential prior learning and support an individualised training plan."
        : 'The Skills Radar helps us understand the knowledge, skills and behaviours (KSBs) you already have from previous education, training and work experience. Your answers help us identify prior learning and tailor your apprenticeship training plan.'}</p>

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

      {ksbs.length > 0 && <div className={styles.completion} role="status">
        <span className={styles.completionValue}>{answeredCount} of {ksbs.length}</span>
        <span>KSBs answered</span>
      </div>}

      {moduleCoverage.length > 0 && <section aria-labelledby="module-results-heading" className={styles.moduleCoverage}>
        <div className={styles.moduleTitleRow}>
          <h3 id="module-results-heading">Your prior knowledge by module</h3>
          <button type="button" className={styles.scoreHelpButton} aria-expanded={showScoreHelp} aria-controls="skills-radar-score-help" onClick={() => setShowScoreHelp((open) => !open)}>
            <Info aria-hidden="true" />About your scores
          </button>
        </div>
        <p className={styles.moduleIntro}>Module scores reflect self-assessed prior knowledge, weighted by the KSBs covered in each module.</p>
        <p id="skills-radar-score-help" className={styles.scoreHelpContent} hidden={!showScoreHelp}>Module scores are indicative and based on self-assessment. They do not represent the percentage of training that can automatically be removed. Any potential reduction in training must be supported by appropriate evidence and reviewed through the provider's existing Recognition of Prior Learning (RPL) process.</p>
        {impactError && <div role="alert" className={styles.impactError}>
          <span>We couldn’t load module results right now. Your answers are still saved.</span>
          <button type="button" onClick={retryRadarImpact}>Retry</button>
        </div>}
        <div className={styles.moduleGrid}>{orderedModuleCoverage.map((module) => {
          const score = module.readiness?.score;
          const unavailable = !impactError && impact?.status === 'ready' && module.record?.status === 'unavailable';
          const answered = module.readiness?.answered ?? module.answered;
          return <article key={module.id} className={`${styles.moduleCard} ${unavailable ? styles.moduleCardUnavailable : ''}`}>
            <div className={styles.moduleHeading}>
              <h4 className={styles.moduleName}>{module.title}</h4>
            </div>
            {score == null ? <div className={styles.unavailableScore}>Score unavailable</div> : <div className={styles.scoreRow}>
              <div className={styles.moduleScore} aria-label={`Prior knowledge ${score} percent`}>{score}<span>%</span></div>
              <span className={styles.scoreLabel}>Prior knowledge</span>
            </div>}
            {score != null && <div className={styles.scoreTrack} role="meter" aria-label={`${module.title} prior knowledge`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={score}>
              <span style={{ width: `${score}%` }} />
            </div>}
            <p className={styles.moduleCount}>{answered} of {module.count} KSB{module.count === 1 ? '' : 's'} assessed</p>
          </article>;
        })}</div>
      </section>}

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
        {ksbs.map((ksb) => renderQuestion(ksb, questionIndex.get(ksb.id)!))}
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
                <button className={btnPrimary} onClick={confirm} disabled={!work.level}>
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

          {!readOnly && highRating && (
            <div role="status" aria-live="polite" className={styles.priorLearningNotice}>
              <Info aria-hidden="true" />
              <div>
                <strong>Potential Prior Learning</strong>
                <p>Your rating suggests you may already have relevant knowledge or experience that could be considered during your initial assessment.</p>
                <p>If you already meet many of the apprenticeship requirements, this may affect how much new training you need and, in some cases, the suitability or funding eligibility of the apprenticeship.</p>
              </div>
            </div>
          )}

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

          {readOnly && highRating && (
            <div role="status" className="mb-4 flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
              <Flag aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p className="text-[12px] leading-relaxed text-amber-800">
                <span className="font-semibold">Flagged: the learner rated this above {FLAG_ABOVE_SCORE}.</span>{' '}
                This self-assessment does not determine eligibility or RPL. Review any existing evidence through the normal assessment process.
              </p>
            </div>
          )}

          {readOnly && <div className="mb-4">
            <label htmlFor={`competence-note-${openKsb.id}`} className="block text-[12px] font-medium text-foreground-700 mb-1.5">
              {t('block.skillsRadar.note')}:
            </label>
            <textarea
              id={`competence-note-${openKsb.id}`}
              rows={3}
              value={work.note}
              readOnly
              className={inputClass}
            />
          </div>}

          {readOnly && <div>
            <label className="block text-[12px] font-medium text-foreground-700 mb-1.5">{t('block.skillsRadar.upload')}</label>
            {work.evidenceFiles.length === 0 ? (
              <EmptyState text="No evidence uploaded" />
            ) : (
              <FileList
                files={work.evidenceFiles.map((n, i) => ({ id: `${n}-${i}`, name: n }))}
                emptyText="No evidence uploaded"
              />
            )}
          </div>}
        </Modal>
      )}
    </div>
  );
}
