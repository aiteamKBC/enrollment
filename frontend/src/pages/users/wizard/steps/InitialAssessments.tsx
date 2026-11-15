import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import {
  autoLoginBksbAccount,
  fetchInitialAssessments,
  type InitialAssessment,
  type InitialAssessmentStatus,
  type InitialAssessmentSubject,
} from '@/api/bksb';
import { formatSystemTimestamp } from '@/lib/format';
import { btnPrimary } from '../../components/ui';
import { useWizard } from '../WizardContext.helpers';
import { useText } from '../layout/textsContext';
import { StepHeading } from './fields';
import { useLearnerEnrolmentVisuals } from '@/features/learner-enrolment/enrolmentPresentation';
import { BookOpen, CalendarDays, TriangleAlert } from 'lucide-react';
import { FinalStepHeader } from './FinalStepHeader';
import styles from './finalSteps.module.css';

const SUBJECT_LABELS: Record<InitialAssessmentSubject, string> = {
  english: 'English',
  maths: 'Maths',
};

const STATUS_LABELS: Record<InitialAssessmentStatus, string> = {
  not_started: 'Not Started',
  in_progress: 'In Progress',
  completed: 'Completed',
  error: 'Error / Unavailable',
};

const CATEGORICAL_LEVELS = new Map([
  ['pre-entry', 'Pre-Entry'],
  ['pre entry', 'Pre-Entry'],
  ['entry 1', 'Entry 1'],
  ['entry 2', 'Entry 2'],
  ['entry 3', 'Entry 3'],
  ['level 1', 'Level 1'],
  ['level 2', 'Level 2'],
  ['level 3', 'Level 3'],
]);

function categoricalLevel(value?: string | null): string | null {
  const key = (value || '').trim().replace(/\s+/g, ' ').toLowerCase();
  return CATEGORICAL_LEVELS.get(key) || null;
}

function actionLabel(status: InitialAssessmentStatus, subject: InitialAssessmentSubject): string {
  const label = SUBJECT_LABELS[subject];
  if (status === 'completed') return `Review ${label} Assessment`;
  if (status === 'in_progress') return `Continue ${label} Assessment`;
  if (status === 'error') return `Retry ${label} Assessment`;
  return `Start ${label} Assessment`;
}

const emptyAssessment = (subject: InitialAssessmentSubject): InitialAssessment => ({ subject, status: 'not_started' });

function statusClass(status: InitialAssessmentStatus): string {
  switch (status) {
    case 'completed': return 'border-emerald-200 bg-emerald-50';
    case 'error': return 'border-red-200 bg-red-50';
    case 'in_progress': return 'border-amber-200 bg-amber-50';
    default: return 'border-foreground-200 bg-background-50';
  }
}

export default function InitialAssessments() {
  const learner = useLearnerEnrolmentVisuals();
  const { userId, isCommercial } = useWizard();
  const t = useText();
  const [assessments, setAssessments] = useState<InitialAssessment[]>([
    emptyAssessment('english'),
    emptyAssessment('maths'),
  ]);
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<InitialAssessmentSubject | null>(null);
  const [accountWorking, setAccountWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadedRef = useRef(false);

  const kind = isCommercial ? 'commercial' : 'apprenticeship';

  const refresh = useCallback(async (options?: { preserveError?: boolean; background?: boolean }) => {
    setLoading(true);
    try {
      const response = await fetchInitialAssessments(kind, userId);
      setEnabled(response.enabled);
      setAssessments(response.assessments.length ? response.assessments : [emptyAssessment('english'), emptyAssessment('maths')]);
      loadedRef.current = true;
      if (!options?.preserveError) setError(null);
    } catch (reason) {
      // A return-to-tab read is a best-effort sync. Keep the last provider
      // snapshot visible when that read races BKSB's eventual consistency.
      if (!loadedRef.current) {
        setAssessments([
          { subject: 'english', status: 'error', error: 'BKSB status could not be loaded.' },
          { subject: 'maths', status: 'error', error: 'BKSB status could not be loaded.' },
        ]);
      }
      if (!options?.background) {
        setError(reason instanceof Error ? reason.message : 'Could not load BKSB assessment status.');
      }
    } finally {
      setLoading(false);
    }
  }, [kind, userId]);

  useEffect(() => { void refresh(); }, [refresh]);

  // Returning from a BKSB tab is the normal sync trigger. Opening a link never
  // marks a card complete; the backend reads the provider session/result here.
  useEffect(() => {
    const onReturn = () => {
      if (document.visibilityState === 'visible' && !loading) void refresh({ preserveError: true, background: true });
    };
    document.addEventListener('visibilitychange', onReturn);
    window.addEventListener('pageshow', onReturn);
    return () => {
      document.removeEventListener('visibilitychange', onReturn);
      window.removeEventListener('pageshow', onReturn);
    };
  }, [loading, refresh]);

  const bySubject = useMemo(() => new Map(assessments.map((item) => [item.subject, item])), [assessments]);

  const reserveTab = () => {
    const tab = window.open('about:blank', '_blank');
    if (tab) {
      tab.opener = null;
      try {
        tab.document.title = 'Opening BKSB';
        tab.document.body.innerHTML = '<main style="font:16px system-ui,sans-serif;padding:2rem">Opening BKSB…</main>';
      } catch {
        // A browser may expose a restricted WindowProxy for the reserved tab.
      }
    }
    return tab;
  };

  const openBksbAccount = async (subject: InitialAssessmentSubject = 'english') => {
    // The temporary tab is created synchronously from the user's click. The
    // short-lived URL returned by Django is never stored in state or browser
    // storage; a failed request closes the reserved tab.
    const tab = reserveTab();
    setAccountWorking(true);
    try {
      const result = await autoLoginBksbAccount(kind, userId, subject);
      if (tab) tab.location.replace(result.url);
      else window.open(result.url, '_blank', 'noopener,noreferrer');
    } catch (reason) {
      if (tab && !tab.closed) tab.close();
      setError(reason instanceof Error ? reason.message : 'Could not open the BKSB account.');
    } finally {
      setAccountWorking(false);
    }
  };

  return (
    <div className={learner ? `${styles.finalStep} ${styles.initialStep}` : 'max-w-4xl space-y-6'}>
      {learner && <FinalStepHeader title={t('block.initialAssessments.title')} />}
      <div className={learner ? styles.informationPanel : undefined}>
        {!learner && <StepHeading title={t('block.initialAssessments.title')} />}
        <h3 className="mt-5 text-[15px] font-semibold text-foreground-900">{t('block.initialAssessments.what')}</h3>
        <p className="mt-1 text-[13px] leading-relaxed text-foreground-700">
          {t('block.initialAssessments.whatBody')}
        </p>
        <h3 className="mt-5 text-[15px] font-semibold text-foreground-900">{t('block.initialAssessments.why')}</h3>
        <p className="mt-1 text-[13px] leading-relaxed text-foreground-700">
          {t('block.initialAssessments.whyBody')}
        </p>
        <h3 className="mt-5 text-[15px] font-semibold text-foreground-900">{t('block.initialAssessments.completing')}</h3>
        <p className="mt-1 text-[13px] leading-relaxed text-foreground-700">
          {t('block.initialAssessments.completingBody')}
        </p>
      </div>

      {(error || !enabled) && (
        <div role="alert" className={learner ? styles.warning : 'rounded-xl border border-red-200 bg-red-50 p-3 text-[13px] text-red-800'}>
          {learner ? <span className={styles.warningIcon} aria-hidden="true"><TriangleAlert size={23} /></span> : <AppIcon className="ri-error-warning-line mr-1.5" />}
          <span>{error || 'BKSB is currently unavailable. You can continue onboarding and try again later.'}</span>
        </div>
      )}

      <div className={learner ? styles.assessmentGrid : 'grid gap-4 md:grid-cols-2'}>
        {(['english', 'maths'] as const).map((subject) => {
          const item = bySubject.get(subject) || emptyAssessment(subject);
          const label = SUBJECT_LABELS[subject];
          // Browser launches use the account dashboard for both subjects.
          // Keep this flag only for interpreting an old direct-launch error
          // in a previously stored status response.
          const directLaunchEnabled = item.directLaunchEnabled ?? false;
          const complete = item.status === 'completed';
          const bksbReady = item.bksbAccountReady === true || item.accessStatus === 'ready';
          // A legacy ENG_NEW deep-link 404 is not an assessment failure.  The
          // dashboard Auto-login path remains valid, so keep the card usable
          // and show only the separate BKSB access state.
          const directLaunchError = subject === 'english'
            && directLaunchEnabled === false
            && /404|direct\s+.*launch/i.test(item.error || '');
          const displayStatus: InitialAssessmentStatus = directLaunchError
            ? bksbReady ? 'in_progress' : 'not_started'
            : item.status;
          const action = actionLabel(displayStatus, subject);
          const displayLevel = categoricalLevel(item.level);
          const date = item.assessmentDate
            ? formatSystemTimestamp(item.assessmentDate, { day: 'numeric', month: 'short', year: 'numeric' })
            : '';
          return (
            <section key={subject} className={learner ? styles.assessmentCard : `rounded-xl border p-4 ${statusClass(displayStatus)}`} data-assessment-status={learner ? displayStatus : undefined} aria-labelledby={`bksb-${subject}`}>
              <div className={learner ? styles.assessmentHeader : 'flex items-start justify-between gap-3'}>
                {learner && <span className={styles.subjectIcon} aria-hidden="true">{subject === 'english' ? <BookOpen size={24} /> : <CalendarDays size={24} />}</span>}
                <div>
                  <h4 id={`bksb-${subject}`} className="text-[16px] font-semibold text-foreground-900">{label}</h4>
                  <p className="mt-1 text-[12px] font-medium text-foreground-600">{STATUS_LABELS[displayStatus]}</p>
                </div>
                {complete && <AppIcon className="ri-checkbox-circle-fill text-[22px] text-emerald-600" />}
              </div>
              {complete && (
                <dl className="mt-4 space-y-1 text-[12px] text-foreground-700">
                  <div className="text-center">
                    <dd className="text-lg font-bold text-foreground-900">{displayLevel || 'Level pending verification'}</dd>
                  </div>
                  {date && <div className="flex justify-between gap-2"><dt className="font-medium">Assessment date</dt><dd>{date}</dd></div>}
                </dl>
              )}
              {item.error && item.status === 'error' && !directLaunchError && <p className="mt-3 text-[12px] text-red-700">{item.error}</p>}
              <button
                type="button"
                className={learner ? `primary-action ${styles.assessmentButton}` : `${btnPrimary} mt-4`}
              onClick={() => void openBksbAccount(subject)}
                disabled={loading || working !== null || accountWorking}
              >
                <AppIcon className={working === subject || accountWorking ? 'ri-loader-4-line animate-spin' : complete ? 'ri-file-search-line' : 'ri-external-link-line'} />
                {working === subject ? 'Opening…' : action}
              </button>
              <p className="mt-2 text-[11px] text-foreground-500">
                {complete
                  ? `Review your completed assessment results and access your ${label} course in BKSB.`
                  : `Open BKSB to access your ${label} course and complete your Initial Assessment.`}
              </p>
              {!complete && <p className="mt-2 text-[11px] text-foreground-500">Opening BKSB does not mark this assessment complete.</p>}
            </section>
          );
        })}
      </div>
    </div>
  );
}
