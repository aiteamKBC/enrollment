import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';
import { useMyLearner } from '@/hooks/useMyLearner';
import { ONBOARDING_NAV_ITEMS, ONBOARDING_ROUTE } from '@/hooks/useOnboardingRedirect';
import { useEnrolmentSubmitted } from '@/hooks/useLearnerNavGate';
import {
  fetchOnboardingReviewSlots,
  fetchReviewForm,
  issuedLabel,
  type OnboardingReviewSlot,
  type OnboardingReviewSlotsResponse,
  type OnboardingReviewType,
  type ReviewFormResponse,
} from '@/api/reviewForm';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import SignReviewModal from './SignReviewModal';
import { downloadReviewPdf } from './reviewDocument';
import { REVIEW_QUESTION_LABELS } from './questions';
import { RowsSkeleton } from '@/components/feature/Skeletons';

const learnerNav = roleNavMap.learner;

// Labels come from the backend (ONBOARDING_REVIEW_LABELS); only the icon is
// presentational, so that is all this map carries.
const REVIEW_ICONS: Record<OnboardingReviewType, string> = {
  'eligibility-review': 'ri-shield-check-line',
  workspace: 'ri-file-list-3-line',
  'training-plan': 'ri-heart-pulse-line',
  'uln-privacy': 'ri-shield-user-line',
};

/**
 * One review. Until the enrolment team issues it there is nothing to do but
 * wait; once issued, the learner fills it in, signs it and can download it.
 */
function ReviewCard({ review, onChanged }: { review: OnboardingReviewSlot; onChanged: () => void }) {
  const { kind, id } = useMyLearner();
  const navigate = useNavigate();
  const icon = REVIEW_ICONS[review.type];
  const [err, setErr] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadErr, setDownloadErr] = useState('');
  // The list carries only whether each side has signed, so the full signature
  // (needed to show/replace it) is fetched when the dialog opens.
  const [signing, setSigning] = useState(false);
  const [signData, setSignData] = useState<ReviewFormResponse | null>(null);
  const eventKey = review.eventKey;

  useEffect(() => {
    if (!signing || signData || !eventKey) return;
    let cancelled = false;
    fetchReviewForm(kind, id, eventKey)
      .then((res) => !cancelled && setSignData(res))
      .catch((e: Error) => { if (!cancelled) { setErr(e.message); setSigning(false); } });
    return () => { cancelled = true; };
  }, [signing, signData, kind, id, eventKey]);

  // The same document the review's own Export PDF produces, built from the
  // saved review — so it is offered on the same terms: once the form is done.
  const download = async () => {
    if (!eventKey || downloading) return;
    setDownloading(true);
    setDownloadErr('');
    try {
      downloadReviewPdf(await fetchReviewForm(kind, id, eventKey), REVIEW_QUESTION_LABELS);
    } catch (e) {
      setDownloadErr(e instanceof Error ? e.message : 'Could not download the review.');
    } finally {
      setDownloading(false);
    }
  };

  const done = Boolean(review.completed);
  const learnerSigned = Boolean(review.signatures?.learner.signed);

  return (
    <div className={`rounded-2xl border p-4 md:p-5 transition-smooth ${
      done ? 'border-emerald-200 bg-emerald-50/40' : review.issued ? 'border-primary-200 bg-background-50' : 'border-foreground-200/70 bg-background-50'
    }`}>
      <div className="flex items-start gap-3">
        <span className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
          done ? 'bg-emerald-500 text-white' : review.issued ? 'bg-primary-50 text-primary-600' : 'bg-background-100 text-foreground-400'
        }`}>
          <i className={`${done ? 'ri-check-line' : review.issued ? icon : 'ri-lock-line'} text-[18px]`} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[14px] font-heading font-semibold text-foreground-900">{review.label}</h3>
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
              done ? 'bg-emerald-100 text-emerald-700'
                : review.started ? 'bg-amber-100 text-amber-800'
                  : review.issued ? 'bg-primary-100 text-primary-700' : 'bg-background-200 text-foreground-500'
            }`}>
              {done ? 'Completed' : review.started ? 'In progress' : review.issued ? 'Ready to start' : 'Not issued yet'}
            </span>
          </div>
          {review.issued && (review.issuedAt || (done && review.completedAt)) && (
            <p className="text-[11px] text-foreground-500 mt-1 flex flex-wrap gap-x-4 gap-y-0.5">
              {review.issuedAt && <span><i className="ri-time-line mr-1" aria-hidden="true" />Issued {issuedLabel(review.issuedAt)}</span>}
              {done && review.completedAt && (
                <span className="text-emerald-700"><i className="ri-check-double-line mr-1" aria-hidden="true" />Completed {issuedLabel(review.completedAt)}</span>
              )}
            </p>
          )}

          {!review.issued || !eventKey ? (
            <p className="text-[12px] text-foreground-500 mt-2">
              Your enrolment officer will issue this review to you. It will appear here, ready to start.
            </p>
          ) : (
            <div className="mt-2.5 flex flex-wrap items-center gap-3">
              <button onClick={() => navigate(`/learner/onboarding/reviews/${encodeURIComponent(eventKey)}`)} className={btnPrimary}>
                {done
                  ? <><i className="ri-eye-line" />View review</>
                  : review.started
                    ? <><i className="ri-play-circle-line" />Continue review</>
                    : <><i className="ri-play-circle-line" />Start</>}
              </button>
              {/* Signing opens only once the review is finished. */}
              {done && (
                <button onClick={() => setSigning(true)} className={btnSecondary}>
                  {learnerSigned
                    ? <><i className="ri-check-line text-emerald-600" />Signed</>
                    : <><i className="ri-pen-nib-line" />Sign review</>}
                </button>
              )}
              {done && (
                <button onClick={() => { void download(); }} disabled={downloading} className={btnSecondary}>
                  {downloading
                    ? <><i className="ri-loader-4-line animate-spin" />Preparing…</>
                    : <><i className="ri-download-2-line" />Download review</>}
                </button>
              )}
              {downloadErr && (
                <p role="alert" className="w-full text-[11px] text-red-600"><i className="ri-error-warning-line mr-1" />{downloadErr}</p>
              )}
            </div>
          )}
          {err && <p className="text-[11px] text-red-600 mt-2"><i className="ri-error-warning-line mr-1" />{err}</p>}
        </div>
      </div>

      {signing && signData && eventKey && (
        <SignReviewModal
          kind={kind}
          learnerId={id}
          eventKey={eventKey}
          party="learner"
          defaultName={signData.learnerInformation.name}
          signatures={signData.signatures}
          onClose={() => setSigning(false)}
          onSigned={(signatures) => {
            setSignData((prev) => (prev ? { ...prev, signatures } : prev));
            // Refresh the list so the button flips to "Signed".
            onChanged();
          }}
        />
      )}
    </div>
  );
}

/**
 * The learner's four enrolment reviews.
 *
 * Shown once the enrolment form has been submitted. The enrolment team issues
 * each review from the learner's board — there is nothing to book — and the
 * learner then completes and signs it here.
 */
export default function OnboardingReviewsPage() {
  const { kind, id } = useMyLearner();
  const isCommercial = kind === 'commercial';
  const navigate = useNavigate();
  // Locked until the enrolment is submitted; the sidebar item is locked too, so
  // this covers a bookmark or a typed address.
  const submitted = useEnrolmentSubmitted(isCommercial ? undefined : kind, id);
  const [data, setData] = useState<OnboardingReviewSlotsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    if (isCommercial) {
      setLoading(false);
      return;
    }
    if (submitted !== true) return;
    fetchOnboardingReviewSlots(kind, id)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [kind, id, isCommercial, submitted]);

  useEffect(load, [load]);

  const completedCount = data?.reviews.filter((r) => r.completed).length ?? 0;
  const total = data?.reviews.length || 4;

  if (isCommercial) {
    return (
      <WorkspaceShell
        role="learner"
        roleLabel={learnerNav.label}
        navItems={learnerNav.items}
        workspaceLabel={learnerNav.workspaceLabel}
        pageTitle="Reviews"
        pageSubtitle="Not required for commercial delivery"
        userName="Learner"
        userRole="Learner"
      >
        <main className=" page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
          <div className="mx-auto max-w-2xl rounded-2xl border border-primary-200 bg-primary-50/40 p-8 text-center">
            <i className="ri-information-line text-3xl text-primary-600" />
            <h2 className="mt-3 text-lg font-heading font-semibold text-foreground-900">No onboarding reviews are required</h2>
            <p className="mt-2 text-[13px] leading-relaxed text-foreground-600">Commercial learners go straight to programme delivery without apprenticeship onboarding reviews.</p>
          </div>
        </main>
      </WorkspaceShell>
    );
  }

  if (submitted !== true) {
    return (
      <WorkspaceShell
        role="learner"
        roleLabel={learnerNav.label}
        navItems={ONBOARDING_NAV_ITEMS}
        workspaceLabel={learnerNav.workspaceLabel}
        pageTitle="Reviews"
        pageSubtitle="Available once you submit your enrolment"
        userName="Learner"
        userRole="Learner"
      >
        <main className="page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
          {submitted === null ? (
            <div className="rounded-2xl border border-foreground-200/60 bg-background-50 p-5"><RowsSkeleton rows={3} /></div>
          ) : (
            <div className="mx-auto max-w-2xl rounded-2xl border border-foreground-200/60 bg-background-50 p-8 text-center">
              <i className="ri-lock-line text-3xl text-primary-600" aria-hidden="true" />
              <h2 className="mt-3 text-lg font-heading font-semibold text-foreground-900">Reviews open once your enrolment is submitted</h2>
              <p className="mt-2 text-[13px] leading-relaxed text-foreground-600">
                Please complete every step of your enrolment and press Submit enrolment. Your enrolment officer will
                then issue your four enrolment reviews here.
              </p>
              <button className={`${btnPrimary} mt-5`} onClick={() => navigate(ONBOARDING_ROUTE)}>
                <i className="ri-file-user-line" aria-hidden="true" />Go to My Enrolment
              </button>
            </div>
          )}
        </main>
      </WorkspaceShell>
    );
  }

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      navItems={ONBOARDING_NAV_ITEMS}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="Reviews"
      pageSubtitle="Complete your four enrolment reviews"
      userName="Learner"
      userRole="Learner"
    >
      <main className="page-container min-w-0 w-full space-y-3 p-3 md:space-y-4 md:p-6">
        <div className="rounded-2xl border border-foreground-200/60 bg-background-50 p-4 md:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-[15px] font-heading font-semibold text-foreground-900">Your enrolment reviews</h2>
              <p className="text-[12px] text-foreground-500 mt-1 max-w-2xl leading-relaxed">
                Thank you for submitting your enrolment. Your enrolment officer issues each of the four reviews below;
                once a review is issued, open it, complete it and sign it.
              </p>
            </div>
            <span className={`text-[12px] font-semibold px-3 py-1.5 rounded-full shrink-0 ${
              completedCount === total ? 'bg-emerald-100 text-emerald-700' : 'bg-primary-50 text-primary-700'
            }`}>
              {completedCount} of {total} completed
            </span>
          </div>
          <div className="mt-3 h-1.5 rounded-full bg-background-200 overflow-hidden">
            <div className="h-full rounded-full bg-primary-500 transition-all duration-500" style={{ width: `${(completedCount / total) * 100}%` }} />
          </div>
        </div>

        {loading && (
          <div className="rounded-2xl border border-foreground-200/60 bg-background-50 p-5">
            <RowsSkeleton rows={4} />
          </div>
        )}

        {!loading && error && (
          <div className="py-16 text-center text-[13px]">
            <p className="text-red-600 mb-3"><i className="ri-error-warning-line mr-1.5" />{error}</p>
            <button className={btnSecondary} onClick={load}><i className="ri-refresh-line" />Retry</button>
          </div>
        )}

        {!loading && !error && data && (
          <div className="space-y-3">
            {data.reviews.map((review) => (
              <ReviewCard key={review.type} review={review} onChanged={load} />
            ))}
          </div>
        )}

        {!loading && !error && data?.allCompleted && (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50/50 p-4 flex items-start gap-3">
            <span className="w-9 h-9 rounded-xl bg-emerald-500 text-white flex items-center justify-center shrink-0">
              <i className="ri-check-double-line" />
            </span>
            <div>
              <p className="text-[13px] font-semibold text-emerald-800">All four reviews are completed</p>
              <p className="text-[12px] text-emerald-700/90 mt-0.5">
                Once every review is signed, your enrolment officer will confirm your place.
              </p>
            </div>
          </div>
        )}
      </main>
    </WorkspaceShell>
  );
}
