import { useCallback, useEffect, useState } from 'react';
import { useAuth } from "@/hooks/useAuth.helpers";
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { LoaderCircle } from 'lucide-react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { PageContainer } from '@/components/ui/PageContainer';
import {
  fetchEmployerLearner,
  fetchEmployerLearnerPlan,
  type EmployerLearnerDetail,
  type EmployerOwnedItem,
} from '@/api/employerPortal';
import type { LearnerKind } from '@/api/extendedIlr';
import { btnSecondary } from '@/pages/users/components/ui';
import type { LearnerDetail } from '@/api/learnerDetail';
import { KsbProgressBody } from '@/components/feature/RealKsbView';
import { EmployerLearningOverview, EmployerTrainingPlan } from './EmployerLearning';
import { EmployerLearnerHeader } from './EmployerLearnerOverview';
import { SignModal } from './EmployerDocumentRows';
import { EmployerDocumentPreview } from './EmployerDocumentPreview';
import { useEmployerSigning } from './useEmployerSigning';
import { EmployerDocumentsTable } from './EmployerDocumentsTable';
import documentStyles from './EmployerDocumentsPage.module.css';
import { useEmployerPortalChrome } from './employerPortalNav';
import { EmployerAssignmentsTab } from './EmployerAssignmentsTab';
import { EmployerMonthlyLogsTab } from './EmployerMonthlyLogsTab';
import { EmployerProgressReviewsTab } from './EmployerProgressReviewsTab';

// ============================================================================
// One learner, as their employer sees them.
//
// Styled as the learner workspace. Overview keeps the dashboard header and progress;
// Learning plan uses the same module timeline and overview. The documents needing this
// employer's signature have their own tab, badged from every tab while any are
// outstanding. Unsigned documents are pinned above signed ones. The rows and
// sign-off dialog are shared with All documents (EmployerDocumentRows).
// ============================================================================

/**
 * The page is one learner seen six ways. Overview is the learner's own
 * dashboard header and progress; Learning plan includes the timeline and module overview;
 * Assignments is what they have handed
 * in; Documents is the employer's own business — the signing queue; the other
 * progress tabs are the learner's workspace
 * shown read-only, so employer and apprentice discuss the same plan, the same
 * hours and the same KSBs rather than two different pictures of them.
 */
type TabKey = 'overview' | 'plan' | 'otjh' | 'ksbs' | 'assignments' | 'documents' | 'reviews';

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'overview', label: 'Overview', icon: 'ri-dashboard-line' },
  { key: 'plan', label: 'Learning plan', icon: 'ri-calendar-check-line' },
  { key: 'otjh', label: 'Monthly logs', icon: 'ri-time-line' },
  { key: 'ksbs', label: 'KSB progress', icon: 'ri-award-line' },
  { key: 'assignments', label: 'Assignments', icon: 'ri-task-line' },
  { key: 'documents', label: 'Documents', icon: 'ri-folder-open-line' },
  { key: 'reviews', label: 'Progress reviews', icon: 'ri-file-list-3-line' },
];

/** The tabs that read the learner's workspace payload (fetched on first use). */
const PLAN_TABS: TabKey[] = ['ksbs'];

export default function EmployerLearnerPage() {
  const { employerId, kind, learnerId } = useParams();
  const { auth } = useAuth();
  return <EmployerLearnerContent key={`${auth.account?.id}:${employerId}:${kind}:${learnerId}`} />;
}

function EmployerLearnerContent() {
  const { employerId = '', kind: kindParam = 'apprenticeship', learnerId = '' } = useParams();
  // The URL segment is a plain string; the document APIs want the narrowed union.
  const kind: LearnerKind = kindParam === 'commercial' ? 'commercial' : 'apprenticeship';
  const navigate = useNavigate();
  const [data, setData] = useState<EmployerLearnerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const tab: TabKey = TABS.find(item => item.key === params.get('tab'))?.key || 'overview';
  const setTab = (nextTab: TabKey) => {
    const next = new URLSearchParams(params);
    if (nextTab === 'overview') next.delete('tab');
    else next.set('tab', nextTab);
    setParams(next);
  };
  // The KSB observer uses the read-only full-plan compatibility payload.
  // Overview and Learning plan use the current learner dashboard projections.
  const [plan, setPlan] = useState<LearnerDetail | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const load = () => {
    setLoading(true);
    setError(null);
    fetchEmployerLearner(employerId, kind, learnerId)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [employerId, kind, learnerId]);

  const loadPlan = useCallback(() => {
    setPlanLoading(true);
    setPlanError(null);
    fetchEmployerLearnerPlan(employerId, kind, learnerId)
      .then(setPlan)
      .catch((e: Error) => setPlanError(e.message))
      .finally(() => setPlanLoading(false));
  }, [employerId, kind, learnerId]);

  // Only when a progress tab is actually opened, and only once per learner.
  useEffect(() => {
    if (!PLAN_TABS.includes(tab) || plan || planLoading || planError) return;
    loadPlan();
  }, [tab, plan, planLoading, planError, loadPlan]);

  // A different learner invalidates whatever plan is held.
  useEffect(() => {
    setPlan(null);
    setPlanError(null);
  }, [employerId, kind, learnerId]);

  const signingActions = useEmployerSigning(employerId, load);

  const learner = data?.learner;
  const items: EmployerOwnedItem[] = data ? [...data.reviews, ...data.documents].map(item => ({
    ...item, learner: { id: learnerId, kind, name: data.learner.name, programme: data.learner.programme },
  })) : [];
  // The fetch is kicked off by an effect, so the first render after a tab switch
  // has neither data nor an in-flight flag yet. Treat that frame as loading too,
  // or the panels flash "nothing here" before the request has even started.
  const planPending = !plan && !planError;
  const outstanding = data?.outstandingCount ?? 0;
  const unsigned = items.filter(item => !item.signed).length;
  const unsignedNote = outstanding > 0 ? `${outstanding} awaiting your signature`
    : unsigned > 0 ? `${unsigned} not signed yet` : null;

  const { shell } = useEmployerPortalChrome(employerId, data?.employer.name);
  const backToLearners = () => navigate(`/employers/${employerId}/learners`);

  return (
    <WorkspaceShell {...shell} pageTitle="Learner" pageSubtitle={learner?.name ?? 'Learner'}>
      <PageContainer>
        {loading && (
          <div
            role="status"
            aria-label="Loading learner"
            aria-live="polite"
            className="flex min-h-[18rem] items-center justify-center py-16"
          >
            <div className="flex flex-col items-center gap-3 text-center">
              <span className="grid h-12 w-12 place-items-center rounded-full bg-primary-50 text-primary-700 shadow-sm">
                <LoaderCircle data-testid="learner-loading-spinner" aria-hidden="true" className="h-7 w-7 animate-spin" />
              </span>
              <div>
                <p className="text-[13px] font-semibold text-foreground-700">Loading learner...</p>
                <p className="mt-1 text-[11px] text-foreground-400">Preparing the learner workspace</p>
              </div>
            </div>
          </div>
        )}

        {!loading && error && (
          <div className="py-16 text-center">
            <p className="text-red-600 text-[13px] mb-3"><i className="ri-error-warning-line mr-1.5" />{error}</p>
            <button className={btnSecondary} onClick={load}><i className="ri-refresh-line" />Retry</button>
          </div>
        )}

        {!loading && !error && data && learner && (
          <>
            <EmployerLearnerHeader
              employerId={employerId}
              kind={kind}
              learner={learner}
              onBack={backToLearners}
            />

            <nav className="flex w-full flex-nowrap items-center gap-1 overflow-x-auto border-b border-foreground-100 pb-2" aria-label="Learner sections">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  aria-current={tab === t.key ? 'page' : undefined}
                  className={`inline-flex min-w-max flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-[12px] font-semibold transition-smooth cursor-pointer lg:px-3 lg:text-[13px] ${
                    tab === t.key
                      ? 'bg-primary-600 text-white'
                      : 'text-foreground-500 hover:bg-background-100 hover:text-foreground-700'
                  }`}
                >
                  <i className={`${t.icon} text-[14px]`} />{t.label}
                  {/* Paperwork not yet signed by this employer stays visible
                      from every tab, now that it has a tab of its own —
                      including items not ready to sign yet. */}
                  {t.key === 'documents' && unsignedNote && (
                    <span
                      data-testid="documents-unsigned-dot"
                      className="ml-0.5 h-2 w-2 shrink-0 rounded-full bg-red-500 ring-2 ring-white/70"
                      title={unsignedNote}
                    >
                      <span className="sr-only">{unsignedNote}</span>
                    </span>
                  )}
                </button>
              ))}
            </nav>

            {tab === 'overview' && <EmployerLearningOverview key={`${employerId}:${kind}:${learnerId}`}
              employerId={employerId} kind={kind} learnerId={learnerId} />}

            {tab === 'plan' && <EmployerTrainingPlan key={`${employerId}:${kind}:${learnerId}`}
              employerId={employerId} kind={kind} learnerId={learnerId} />}

            {tab === 'assignments' && (
              <EmployerAssignmentsTab employerId={employerId} kind={kind} learnerId={learnerId} learnerName={learner.name || 'this learner'} />
            )}

            {tab === 'documents' && (
              <div className="space-y-4">
                {outstanding > 0 && (
                  <div className="rounded-xl border border-amber-300/70 bg-amber-50/70 px-4 py-3 flex items-center gap-2.5">
                    <i className="ri-error-warning-line text-amber-600 shrink-0" />
                    <p className="text-[13px] text-amber-900">
                      <strong>{outstanding} document{outstanding === 1 ? '' : 's'}</strong> need your signature.
                    </p>
                  </div>
                )}
                <div className={documentStyles.page}>
                  <EmployerDocumentsTable items={items} employerId={employerId}
                    opening={signingActions.opening} onSign={signingActions.startSigning} onShow={signingActions.openDocument} />
                </div>
              </div>
            )}

            {tab === 'reviews' && (
              <EmployerProgressReviewsTab key={`${employerId}:${kind}:${learnerId}`} employerId={employerId} kind={kind}
                learnerId={learnerId} employerName={data.employer.name}
                onReviewChanged={() => fetchEmployerLearner(employerId, kind, learnerId).then(setData)} />
            )}

            {PLAN_TABS.includes(tab) && planError && (
              <div className="py-16 text-center">
                <p className="text-red-600 text-[13px] mb-3"><i className="ri-error-warning-line mr-1.5" />{planError}</p>
                <button className={btnSecondary} onClick={loadPlan}><i className="ri-refresh-line" />Retry</button>
              </div>
            )}

            {tab === 'otjh' && (
              <EmployerMonthlyLogsTab key={`${employerId}:${kind}:${learnerId}`} employerId={employerId} kind={kind} learnerId={learnerId} />
            )}

            {tab === 'ksbs' && !planError && (
              <KsbProgressBody real={plan} loading={planPending} showHero={false} audience="observer" />
            )}
          </>
        )}
      </PageContainer>

      {signingActions.preview && <EmployerDocumentPreview employerId={employerId}
        target={signingActions.preview} onClose={signingActions.closePreview} />}

      {signingActions.signing && data && (
        <SignModal
          item={signingActions.signing.item}
          employerName={data.employer.name}
          reviewDefinition={signingActions.reviewDefinition}
          onClose={signingActions.closeSigning}
          onSign={signingActions.sign}
          onSaveReviewAnswers={signingActions.saveReviewAnswers}
        />
      )}
    </WorkspaceShell>
  );
}
