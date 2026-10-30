import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { useToast } from "@/hooks/useToast.helpers";
import { useMyLearner } from '@/hooks/useMyLearner';
import { useResetWorkspaceScroll } from '@/hooks/useResetWorkspaceScroll';
import { updateEnrolmentUser } from '@/api/enrolmentUsers';
import { updateCommercialBoard } from '@/api/commercialUsers';
import { fetchWizardBootstrap } from '@/api/extendedIlr';
import type { EnrolmentBoard } from '@/pages/users/types';
import { btnSecondary } from '@/pages/users/components/ui';
import { WizardProvider } from '@/pages/users/wizard/WizardContext';
import { useWizard } from "@/pages/users/wizard/WizardContext.helpers";
import { WizardShell } from '@/pages/users/wizard/WizardShell';
import { maxReachableStep, missingAcrossWizard } from '@/pages/users/wizard/validation';
import { ONBOARDING_REVIEWS_ROUTE } from '@/hooks/useOnboardingRedirect';
import { markEnrolmentSubmitted } from '@/hooks/useLearnerNavGate';
import { RowsSkeleton } from '@/components/feature/Skeletons';

const learnerNav = roleNavMap.learner;

/**
 * The learner's own enrolment form.
 *
 * Deliberately the SAME wizard the enrolment team uses (same step components,
 * same WizardContext, same enrolment."Extended_ILR" row) rather than a parallel
 * form — a second implementation would drift, and staff need to review exactly
 * what the learner submitted. Only the chrome differs: no "you are viewing"
 * banner, no admin quick actions, and Finish marks the enrolment submitted
 * instead of complete (staff still verify evidence and countersign).
 */
function LearnerWizard({ stepSlug, onDone }: { stepSlug: string | undefined; onDone: () => void }) {
  const { userId, isCommercial, board, draft, ready, saveIlr, layout, steps } = useWizard();
  const navigate = useNavigate();
  const { success, error } = useToast();

  // Resolved against the published layout's steps (see the wizard builder).
  const idx = steps.findIndex((s) => s.slug === stepSlug);
  const currentIndex = idx === -1 ? 0 : idx;
  const goTo = (i: number) => navigate(`/learner/onboarding/${steps[i].slug}`);

  // The step tabs are gated, but the URL is not — typing a later step's slug
  // would otherwise walk straight past the steps in between. Held until the
  // draft is worth measuring (`ready`, not just `hydrated` — the competencies
  // land in a second request), or a returning learner is bounced to step one on
  // answers that hadn't loaded yet.
  const reachable = maxReachableStep(draft, steps.length, layout);
  const reachableSlug = steps[reachable]?.slug;
  useEffect(() => {
    if (ready && currentIndex > reachable && reachableSlug) {
      navigate(`/learner/onboarding/${reachableSlug}`, { replace: true });
    }
  }, [ready, currentIndex, reachable, reachableSlug, navigate]);

  const finish = async () => {
    // Backstop to the step gating: completeness is judged live, so the whole
    // form is re-checked before anything is marked submitted.
    const gaps = missingAcrossWizard(draft, steps.length, layout);
    if (gaps.length > 0) {
      const first = gaps[0];
      error(
        'Your enrolment isn’t complete',
        `${gaps.length} ${gaps.length === 1 ? 'answer is' : 'answers are'} still outstanding, starting with “${first.label}” on ${steps[first.stepIndex].label}.`
      );
      goTo(first.stepIndex);
      return;
    }

    const pd = draft.personalDetails;
    const name = `${pd.firstName} ${pd.lastName}`.trim();
    try {
      // The ILR lives in its own table — save it before touching the learner row
      // so a learner who never pressed Save on that step doesn't lose it.
      if (!isCommercial) await saveIlr();

      if (isCommercial) {
        await updateCommercialBoard(userId, {
          username: name || board.user.name,
          email: pd.email,
          phone: pd.phone,
        });
      } else {
        // 'Submitted' — not 'Completed'. Completion is the provider's call once
        // identity/eligibility evidence has been verified and countersigned.
        await updateEnrolmentUser(userId, {
          username: name || board.user.name,
          email: pd.email,
          phone: pd.phone,
          dob: pd.dob,
          onboardingStatus: 'Submitted',
        });
        // Opens the Reviews tab now, rather than on the learner's next session.
        markEnrolmentSubmitted('apprenticeship', userId);
      }
      success('Enrolment submitted', 'Thank you — your enrolment has been sent to the team for review.');
      onDone();
    } catch (err) {
      error('Could not submit your enrolment', err instanceof Error ? err.message : 'Unexpected error');
    }
  };

  return (
    <WizardShell
      currentIndex={currentIndex}
      mode="learner"
      onNavigateStep={goTo}
      onFinish={finish}
      finishLabel="Submit enrolment"
      header={
        <div className="mb-4 overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm">
          {/* Tinted band so the instruction reads as a welcome rather than as an
              alert — the flat grey panel it replaces was the least legible thing
              on the page. */}
          <div className="flex items-start gap-4 bg-primary-50/60 p-5 sm:items-center sm:p-6">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-primary-200/70 bg-background-50">
              <AppIcon className="ri-file-list-3-line text-xl text-primary-600" />
            </span>
            <div className="min-w-0">
              <h2 className="font-heading text-base font-bold text-foreground-950 sm:text-lg">Your enrolment</h2>
              <p className="mt-1 text-[13px] leading-relaxed text-foreground-600">
                Please complete every step and sign where asked. Your answers save as you go, and the enrolment team
                will review them once you submit.
              </p>
            </div>
          </div>
        </div>
      }
    />
  );
}

export default function LearnerOnboardingPage() {
  const { stepSlug } = useParams();
  const navigate = useNavigate();
  const myLearner = useMyLearner();
  const isCommercial = myLearner.kind === 'commercial';

  const [board, setBoard] = useState<EnrolmentBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Keyed on the learner, not the step — switching tabs must not refetch. The
  // board is only reloaded when the learner actually changes (or on Retry).
  const learnerId = myLearner.id;
  const [reloadToken, setReloadToken] = useState(0);
  const reload = () => setReloadToken((n) => n + 1);

  // Commercial learners do not have an apprenticeship onboarding flow. This
  // also clears an old bookmarked/sidebar URL once the account type is known.
  useEffect(() => {
    if (isCommercial) navigate('/workspace/learner', { replace: true });
  }, [isCommercial, navigate]);

  useEffect(() => {
    if (isCommercial) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    // One request for the board *and* the saved ILR, cached per learner: the
    // wizard used to fetch them separately (and StrictMode doubled each), so a
    // single open cost four round-trips. Retry forces a fresh read.
    fetchWizardBootstrap(isCommercial ? 'commercial' : 'apprenticeship', learnerId, {
      force: reloadToken > 0,
    })
      .then((data) => { if (!cancelled) setBoard(data.board); })
      .catch((e: Error) => { if (!cancelled) setLoadError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [learnerId, isCommercial, reloadToken]);

  // Neither ILR step applies to commercial delivery.
  const resolvedStepSlug = isCommercial && (stepSlug === 'ilr' || stepSlug === 'ilr-details') ? 'plr' : stepSlug;

  // Each step opens at its top rather than at the previous step's position.
  const topRef = useResetWorkspaceScroll<HTMLElement>(resolvedStepSlug);

  if (isCommercial) return null;

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      // WorkspaceShell trims this to match the learner's programme status
      // (onboarding and delivery each get a reduced menu), so the full nav is
      // handed over here and gated in one place for every learner page.
      navItems={learnerNav.items}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="My Enrolment"
      pageSubtitle="Complete your onboarding"
      userName={board?.user.name || 'Learner'}
      userRole="Learner"
      showBackButton={false}
    >
      {/* w-full, matching the other learner pages — the shell already offsets for
          the collapsed sidebar rail, so an extra centred max-width fought it. */}
      <main ref={topRef} className="page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
        {loading && (
          <div className="rounded-2xl border border-foreground-200/60 bg-background-50 p-5">
            <RowsSkeleton rows={5} />
          </div>
        )}
        {!loading && loadError && (
          <div className="py-20 text-center text-[13px]">
            <p className="text-red-600 mb-3"><AppIcon className="ri-error-warning-line mr-1.5" />{loadError}</p>
            <button className={btnSecondary} onClick={reload}><AppIcon className="ri-refresh-line" />Retry</button>
          </div>
        )}
        {!loading && !loadError && board && (
          <WizardProvider userId={myLearner.id} isCommercial={isCommercial} board={board}>
            {/* Straight to the reviews they now need to book, not the profile —
                booking all four is what completes their enrolment. */}
            <LearnerWizard stepSlug={resolvedStepSlug} onDone={() => navigate(ONBOARDING_REVIEWS_ROUTE)} />
          </WizardProvider>
        )}
      </main>
    </WorkspaceShell>
  );
}
