import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useToast } from "@/hooks/useToast.helpers";
import { useResetWorkspaceScroll } from '@/hooks/useResetWorkspaceScroll';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { fetchEnrolmentBoard, updateEnrolmentUser } from '@/api/enrolmentUsers';
import { fetchCommercialBoard, updateCommercialBoard } from '@/api/commercialUsers';
import type { EnrolmentBoard } from '../types';
import { btnSecondary } from '../components/ui';
import { WizardProvider } from './WizardContext';
import { useWizard } from "./WizardContext.helpers";
import { WizardShell } from './WizardShell';
import { DEFAULT_LAYOUT, visibleSteps } from './layout/resolve';

const DEFAULT_STEPS = visibleSteps(DEFAULT_LAYOUT);

function WizardInner({ stepSlug }: { stepSlug: string }) {
  const { userId, isCommercial, board, draft, saveIlr, steps = DEFAULT_STEPS } = useWizard();
  // Resolved against the published layout's steps, so a step added in the
  // wizard builder has an address and a removed one falls back to the first.
  const idx = steps.findIndex((s) => s.slug === stepSlug);
  const currentIndex = idx === -1 ? 0 : idx;
  const navigate = useNavigate();
  const { success, error } = useToast();

  // Keep the source in the URL so step navigation and the profile link stay
  // pointed at the right table.
  const suffix = isCommercial ? '?source=commercial' : '';
  const profileHref = `/users/${userId}${suffix}`;

  const goTo = (i: number) => navigate(`/users/${userId}/wizard/${steps[i].slug}${suffix}`);

  const finish = async () => {
    const pd = draft.personalDetails;
    const name = `${pd.firstName} ${pd.lastName}`.trim();
    try {
      // Persist the Extended ILR first: it lives in its own table, so a learner
      // who filled it in but never pressed Save would otherwise lose it here.
      if (!isCommercial) await saveIlr();

      // Persist the fields this table can hold, and mark onboarding complete.
      // The commercial table has no dob/onboarding columns, so its board
      // endpoint takes only the fields it can actually store.
      if (isCommercial) {
        await updateCommercialBoard(userId, {
          username: name || board.user.name,
          email: pd.email,
          phone: pd.phone,
        });
      } else {
        await updateEnrolmentUser(userId, {
          username: name || board.user.name,
          email: pd.email,
          phone: pd.phone,
          dob: pd.dob,
          onboardingStatus: 'Completed',
          onboardingCompleted: new Date().toLocaleString('en-GB'),
        });
      }
      success('Enrolment submitted', 'Onboarding marked complete. Redirecting to the profile.');
      navigate(profileHref);
    } catch (err) {
      error('Could not submit enrolment', err instanceof Error ? err.message : 'Unexpected error');
    }
  };

  return (
    <WizardShell
      currentIndex={currentIndex}
      mode="staff"
      onNavigateStep={goTo}
      onFinish={finish}
      header={
        // Solid purple via a class, not an inline gradient: index.css replaces any
        // workspace surface whose inline style names primary-950/900 with the
        // shared hero gradient, which fades to near-white on the right and left
        // the white text and Close button unreadable.
        <div data-testid="staff-wizard-banner" className="relative rounded-2xl overflow-hidden mb-4 animate-fade-in-up bg-primary-800">
          <div className="absolute inset-x-0 top-0 h-px bg-white/10" />
          <div className="relative px-6 py-5 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <span className="w-11 h-11 rounded-xl bg-white/20 backdrop-blur-sm flex items-center justify-center shrink-0"><AppIcon className="ri-magic-line text-white text-xl" /></span>
              <div className="min-w-0">
                <p className="text-[11px] text-white/60 uppercase tracking-wide">Enrolment Wizard</p>
                <p className="text-[15px] font-heading font-semibold text-white truncate">You are viewing: {board.user.name}{board.user.reference ? <span className="text-white/60 font-normal"> ({board.user.reference})</span> : null}</p>
              </div>
            </div>
            <button onClick={() => navigate(profileHref)} className="inline-flex items-center gap-1.5 px-3 py-2 border border-white/70 bg-transparent text-white rounded-lg text-[12px] font-medium hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white transition-smooth cursor-pointer shrink-0"><AppIcon className="ri-close-line" />Close wizard</button>
          </div>
        </div>
      }
    />
  );
}

function WizardPageContent() {
  const { userId = '', stepSlug } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  // The directory lists two tables whose ids overlap, so the row's source is
  // carried in the URL — without it a commercial id would read the wrong record.
  const isCommercial = search.get('source') === 'commercial';
  const suffix = isCommercial ? '?source=commercial' : '';
  const [board, setBoard] = useState<EnrolmentBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Keyed on the learner, not the step — switching tabs must not refetch.
  const [reloadToken, setReloadToken] = useState(0);
  const reload = () => setReloadToken((n) => n + 1);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    (isCommercial ? fetchCommercialBoard(userId) : fetchEnrolmentBoard(userId))
      .then((b) => { if (!cancelled) setBoard(b); })
      .catch((e: Error) => { if (!cancelled) setLoadError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [userId, isCommercial, reloadToken]);

  if (!stepSlug) return <Navigate to={`/users/${userId}/wizard/introduction${suffix}`} replace />;
  // Commercial delivery has no funded ILR trail. If an old bookmark or a
  // generic next-step link reaches the ILR route, land on the next real step.
  // Neither ILR step applies to commercial delivery.
  const resolvedStepSlug = isCommercial && (stepSlug === 'ilr' || stepSlug === 'ilr-details') ? 'plr' : stepSlug;

  if (loading || loadError || !board) {
    return (
      <div className="min-h-full bg-background-200 flex items-center justify-center p-6">
        <div className="text-center">
          {loading && <p className="text-[13px] text-foreground-400"><AppIcon className="ri-loader-4-line animate-spin mr-2" />Loading enrolment…</p>}
          {!loading && loadError && (
            <>
              <p className="text-red-600 text-[13px] mb-3"><AppIcon className="ri-error-warning-line mr-1.5" />{loadError}</p>
              <div className="flex items-center justify-center gap-3">
                <button className={btnSecondary} onClick={reload}><AppIcon className="ri-refresh-line" />Retry</button>
                <button className={btnSecondary} onClick={() => navigate(`/users/${userId}${suffix}`)}><AppIcon className="ri-close-line" />Back to profile</button>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    /* readOnlyLearnerSteps: the Skills Radar is the learner's own self-assessment,
       so staff review it here rather than edit it. */
    <WizardProvider userId={userId} isCommercial={isCommercial} board={board} readOnlyLearnerSteps>
      <WizardInner stepSlug={resolvedStepSlug} />
    </WizardProvider>
  );
}

export default function WizardPage() {
  const config = roleNavMap.compliance;
  const { stepSlug } = useParams();
  // Each step opens at its top rather than at the previous step's position.
  // `contents`, so the anchor adds no box to the layout.
  const topRef = useResetWorkspaceScroll<HTMLDivElement>(stepSlug);
  return (
    <WorkspaceShell role="compliance" roleLabel={config.label} navItems={config.items}
      workspaceLabel={config.workspaceLabel} pageTitle="Enrolment Wizard">
      <div ref={topRef} className="contents">
        <WizardPageContent />
      </div>
    </WorkspaceShell>
  );
}
