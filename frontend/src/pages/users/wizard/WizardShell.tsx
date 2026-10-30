import { useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react';
import { useToast } from "@/hooks/useToast.helpers";
import { WIZARD_STEPS } from '../types';
import { btnDestructive, btnPrimary, btnSuccess, btnSecondary } from '../components/ui';
import { isStepComplete, missingForStep } from './validation';
import { useWizard } from "./WizardContext.helpers";
import { StepErrorsContext } from "./stepErrors.helpers";
import Introduction from './steps/Introduction';
import BeforeYouBegin from './steps/BeforeYouBegin';
import PersonalDetails from './steps/PersonalDetails';
import IlrLearnerDetails from './steps/IlrLearnerDetails';
import SkillsRadar from './steps/SkillsRadar';
import Inclusion from './steps/Inclusion';
import Ilr from './steps/Ilr';
import Plr from './steps/Plr';
import CvJob from './steps/CvJob';
import Policies from './steps/Policies';
import NextSteps from './steps/NextSteps';
import { StepHeading } from './steps/fields';
import { StepItems } from './layout/StepItems';

/**
 * The step bodies are mode-agnostic — every one reads and writes only through
 * useWizard(), so the same components serve the staff wizard and the learner's
 * own onboarding form. Only the chrome around them differs (see WizardShell).
 */
const BODIES_BY_SLUG: Record<string, ComponentType> = {
  introduction: Introduction,
  'before-you-begin': BeforeYouBegin,
  'personal-details': PersonalDetails,
  'ilr-details': IlrLearnerDetails,
  ilr: Ilr,
  'cv-job': CvJob,
  plr: Plr,
  'skills-radar': SkillsRadar,
  inclusion: Inclusion,
  policies: Policies,
  'next-steps': NextSteps,
};

/**
 * One body per WIZARD_STEPS entry, in its order. Matched on the slug rather
 * than written out positionally, so reordering the steps cannot put one step's
 * page under another step's name.
 */
export const STEP_BODIES: ComponentType[] = WIZARD_STEPS.map((step) => {
  const body = BODIES_BY_SLUG[step.slug];
  if (!body) throw new Error(`No wizard step body for '${step.slug}'`);
  return body;
});

/**
 * Steps whose built-in body is a single block (Welcome, PLR, Skills Radar,
 * Policies, …). Those blocks can be moved, so these steps render whatever the
 * layout now puts on them rather than their original component.
 */
const BLOCK_STEPS = new Set(['introduction', 'before-you-begin', 'plr', 'skills-radar', 'inclusion', 'policies', 'next-steps']);

/** A step added in the wizard builder, or a block step: its items, in layout order. */
function LayoutStepBody({ slug, label, custom }: { slug: string; label: string; custom: boolean }) {
  if (!custom) return <StepItems slug={slug} />;
  return (
    <div>
      <StepHeading title={label} />
      <div className="max-w-3xl">
        <StepItems slug={slug} />
      </div>
    </div>
  );
}

/**
 * Shared wizard chrome: step tabs, progress, body, prev/next and finish.
 *
 * `mode` only affects presentation and the finish action:
 *   'staff'   — "You are viewing: <learner>", Close-wizard, quick actions.
 *   'learner' — the learner filling in their own enrolment; no admin affordances.
 *
 * Both modes drive the same WizardContext, so both write the same Extended ILR
 * row — the learner's answers are what staff later review and countersign.
 */
export function WizardShell({
  currentIndex,
  mode,
  onNavigateStep,
  onFinish,
  finishLabel = 'Finish',
  header,
  sidebar,
}: {
  currentIndex: number;
  mode: 'staff' | 'learner';
  onNavigateStep: (index: number) => void;
  onFinish: () => Promise<void>;
  finishLabel?: string;
  header?: ReactNode;
  sidebar?: ReactNode;
}) {
  const { draft, hydrated, ready, saveIlr, ilrSaving, layout, steps } = useWizard();
  const { success, error } = useToast();
  // Learners save by moving on — Next writes the step as it advances, so a
  // separate Save progress button would only be a second name for the same
  // action. Staff keep it: they jump around the steps rather than walking them,
  // so they need a save that isn't tied to going forward.
  const showSaveButton = mode === 'staff';
  const tabScrollRef = useRef<HTMLDivElement>(null);
  const [submitting, setSubmitting] = useState(false);

  /**
   * Learners work through the steps in order: a step only opens once every step
   * before it is complete. Completeness is judged against the draft in hand, not
   * against what has landed on the server — moving on no longer waits for the
   * write (see navigateTo). Staff are not gated — they dip into whichever step
   * they need to check or correct.
   *
   * Gating waits for `ready`; before the saved answers arrive every step looks
   * empty, and a returning learner would be shut out of their own work. It waits
   * for the seeded competencies too — an unseeded Skills Radar has nothing
   * unrated in it, so for that window the gate would wave the learner straight
   * past their own self-assessment.
   */
  const gated = mode === 'learner' && ready;
  const stepComplete = useMemo(
    () => steps.map((_, i) => isStepComplete(i, draft, layout)),
    [draft, steps, layout]
  );
  /** First step before `target` that is still unfilled, or -1 if the path is clear. */
  const blockingStep = (target: number) => stepComplete.slice(0, target).findIndex((c) => !c);
  const locked = (i: number) => gated && i > currentIndex && blockingStep(i) !== -1;

  // What the rail and the progress count call complete. Next Steps has nothing
  // to answer, so validation always passes it — which ticked it off before the
  // learner had done anything. Here it only counts once every earlier step is
  // finished and it has actually been opened; until then it reads like any
  // other step (locked, then "Not started"). Gating still uses stepComplete.
  const lastIndex = steps.length - 1;
  const earlierStepsComplete = stepComplete.slice(0, lastIndex).every(Boolean);
  const [reachedLastStep, setReachedLastStep] = useState(false);
  useEffect(() => {
    if (currentIndex === lastIndex && earlierStepsComplete) setReachedLastStep(true);
  }, [currentIndex, lastIndex, earlierStepsComplete]);
  const shownComplete = stepComplete.map((complete, i) =>
    i === lastIndex ? complete && earlierStepsComplete && reachedLastStep : complete
  );

  // What the current step is still missing, recomputed live so the list shrinks
  // as the learner fills it in. Only shown once they've tried to move on.
  const [showErrors, setShowErrors] = useState(false);
  const currentMissing = mode === 'learner' ? missingForStep(currentIndex, draft, layout) : [];
  useEffect(() => setShowErrors(false), [currentIndex]);
  // Each refused Next brings the first red box into view — it may be far above
  // the footer the learner just clicked.
  const [blockedAttempt, setBlockedAttempt] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!blockedAttempt) return;
    const first = bodyRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]');
    first?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    first?.focus({ preventScroll: true });
  }, [blockedAttempt]);

  const save = async () => {
    try {
      await saveIlr();
      success('Progress saved', 'Every step of this wizard is stored against the learner.');
    } catch (e) {
      error('Could not save', e instanceof Error ? e.message : 'Unexpected error');
    }
  };

  /**
   * Step navigation.
   *
   * Moving between steps always saves, in both directions and both modes: it is
   * the only save the learner has, and on the staff side it means a step is
   * never left behind unwritten. Staff keep an unwritten Back, since they have
   * an explicit Save progress button.
   *
   * The save never blocks the move. Navigation is what the button promises, so
   * it happens immediately and the write goes out behind it — a step like the
   * Introduction holds nothing but static text, and making the learner watch a
   * "Saving…" spinner before it would let them read the next page was the whole
   * complaint. A failed write raises a toast rather than pulling them back.
   *
   * Because nothing is awaited, a learner tabbing quickly can have two writes in
   * flight; each sends the whole draft, so the loser of that race only costs a
   * redundant request. The last thing they do always writes the current draft.
   */
  const navigateTo = (target: number) => {
    if (target < 0 || target >= steps.length) return;
    /** Write this step behind the move; a failure is reported, never blocking. */
    const saveInBackground = () =>
      saveIlr().catch((e: unknown) =>
        error('Could not save your answers', e instanceof Error ? e.message : 'Unexpected error')
      );

    if (target <= currentIndex) {
      if (mode === 'learner' && hydrated) saveInBackground();
      onNavigateStep(target);
      return;
    }

    if (gated) {
      const blocking = blockingStep(target);
      if (blocking === currentIndex) {
        setShowErrors(true);
        setBlockedAttempt((n) => n + 1);
        error('This step isn’t finished', 'Please answer everything highlighted below before moving on.');
        return;
      }
      if (blocking !== -1) {
        // A step further back is unfinished — name it and stay put rather than
        // moving the learner somewhere they didn't ask to go.
        error('Earlier step incomplete', `Please finish “${steps[blocking].label}” first.`);
        return;
      }
    }

    saveInBackground();
    onNavigateStep(target);
  };

  const currentStep = steps[Math.min(currentIndex, steps.length - 1)];
  const BuiltinBody = currentStep && currentStep.builtin && !BLOCK_STEPS.has(currentStep.slug) ? BODIES_BY_SLUG[currentStep.slug] : undefined;
  const body = !currentStep ? null : BuiltinBody ? <BuiltinBody /> : (
    <LayoutStepBody slug={currentStep.slug} label={currentStep.label} custom={!currentStep.builtin} />
  );
  const isFirst = currentIndex === 0;
  const isLast = currentIndex === steps.length - 1;

  // How far through the form the learner is. Counted from finished steps rather
  // than the step they happen to be looking at, so flicking backwards to re-read
  // the Introduction doesn't appear to undo their progress.
  //
  // Derived live from the draft in both modes. The staff view once read a
  // `completed[]` array whose only mutator was never called, so an admin opening
  // a learner's wizard always saw 0% regardless of what the learner had filled
  // in; deriving from the loaded answers shows the learner's real progress — the
  // same measure the learner sees.
  //
  // Held behind `ready`, not merely `hydrated`, for both modes: the draft starts
  // as a blank seeded form, and the programme's competencies arrive in a second
  // request after the saved answers. Measuring in between reported finished steps
  // as "Not started", and counted the Skills Radar as complete (an empty
  // assessments map has nothing unrated in it) until seeding landed — so the
  // total ticked up twice. Unknown is shown as unknown instead.
  const statusKnown = ready;
  const doneCount = shownComplete.filter(Boolean).length;
  const pct = steps.length ? Math.round((doneCount / steps.length) * 100) : 0;

  const scrollTabs = (dir: number) => tabScrollRef.current?.scrollBy({ left: dir * 220, behavior: 'smooth' });

  const finish = async () => {
    setSubmitting(true);
    try {
      await onFinish();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={mode === 'staff' ? 'min-h-full bg-background-200' : ''}>
      <div className={mode === 'staff' ? 'max-w-6xl mx-auto px-6 py-6' : ''}>
        {header}

        {/* Rail | body | optional staff sidebar. The rail is a real column rather
            than a scrolling tab strip: eight steps never fitted across the top,
            so the labels were clipped to "Personal Learning …" and two arrow
            buttons were needed to reach the rest. Down the side every step reads
            in full and the wide dead space beside the form is what pays for it. */}
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
          {/* Step rail — lg and up. Below that the same list would push the form
              off the fold, so small screens get the compact header inside the
              card instead (both drive the same navigateTo). */}
          <nav className="hidden shrink-0 lg:block lg:w-64 xl:w-72">
            <div className="overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm">
              <div className="border-b border-foreground-100 px-4 py-3.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Your progress</span>
                  <span className="text-[12px] font-semibold text-primary-700">{statusKnown ? `${pct}%` : '—'}</span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-background-200">
                  {/* No bar at all until the answers are in: a 0%-then-jump bar
                      read as lost progress to a returning learner. */}
                  {statusKnown && (
                    <div className="h-full rounded-full bg-primary-500 transition-all duration-500" style={{ width: `${pct}%` }} />
                  )}
                </div>
                <p className="mt-2 text-[11px] text-foreground-400">
                  {statusKnown
                    ? `${doneCount} of ${steps.length} steps complete`
                    : 'Loading your answers…'}
                </p>
              </div>
              <div role="tablist" aria-label="Enrolment steps" aria-orientation="vertical" className="p-2">
                {steps.map((step, i) => {
                  const active = i === currentIndex;
                  const isLocked = locked(i);
                  // The tick tracks the answers themselves in both modes, and is
                  // not trusted before the draft has hydrated — see statusKnown.
                  const done = statusKnown && shownComplete[i];
                  return (
                    <button
                      key={step.slug}
                      role="tab"
                      aria-selected={active}
                      aria-disabled={isLocked || undefined}
                      // The step label alone is the name. The number token and
                      // the status line below it are decoration — without this
                      // the tab announced as "3 Skills Radar Not started",
                      // burying the label the learner is looking for.
                      aria-label={step.label}
                      title={isLocked ? `${step.label} — complete the earlier steps first` : step.label}
                      onClick={() => navigateTo(i)}
                      className={`group relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-smooth ${
                        active
                          ? 'cursor-pointer bg-primary-50 text-primary-700'
                          : isLocked
                            ? 'cursor-not-allowed text-foreground-300'
                            : 'cursor-pointer text-foreground-600 hover:bg-background-100'
                      }`}
                    >
                      {/* Active marker, drawn rather than shifting the row */}
                      <span className={`absolute left-0 top-1/2 h-6 w-[3px] -translate-y-1/2 rounded-r-full bg-primary-500 transition-opacity ${active ? 'opacity-100' : 'opacity-0'}`} />
                      {/* Numbered token: a tick once the step is answered, a
                          padlock while it is still out of reach, its number
                          otherwise — so the rail says both where you are and
                          what remains. */}
                      <span
                        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold ${
                          done
                            ? 'border-emerald-200 bg-emerald-50 text-emerald-600'
                            : active
                              ? 'border-primary-300 bg-primary-100 text-primary-700'
                              : isLocked
                                ? 'border-foreground-200/70 bg-background-100 text-foreground-300'
                                : 'border-foreground-200 bg-background-50 text-foreground-500'
                        }`}
                      >
                        {done ? <AppIcon className="ri-check-line text-[13px]" /> : isLocked ? <AppIcon className="ri-lock-line text-[12px]" /> : i + 1}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={`block truncate text-[12.5px] ${active ? 'font-semibold' : 'font-medium'}`}>{step.label}</span>
                        <span className="block text-[10.5px] text-foreground-400">
                          {/* "Not started" is a claim about the learner's saved
                              work, so it is withheld until that work has loaded;
                              the step they are on is knowable either way. */}
                          {done ? 'Complete' : active ? 'In progress' : !statusKnown ? '' : isLocked ? 'Locked' : 'Not started'}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </nav>

          {/* Main panel */}
          <div className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm">
            {/* Compact step header — below lg, where the rail is hidden. Keeps a
                horizontal step list so narrow screens can still jump between
                steps; the rail above owns the tablist role. */}
            <div className="border-b border-foreground-100 px-3 py-3 sm:px-4 lg:hidden">
              <div className="mb-2 flex items-center justify-between gap-3">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Step {currentIndex + 1} of {steps.length}</span>
                <span className="truncate text-xs font-semibold text-primary-700">{currentStep?.label}</span>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={() => scrollTabs(-1)} aria-label="Scroll tabs left" className="hidden h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-foreground-200 text-foreground-500 hover:bg-background-100 sm:flex">
                  <AppIcon className="ri-arrow-left-s-line" />
                </button>
                <div ref={tabScrollRef} className="min-w-0 flex-1 snap-x snap-mandatory overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:scrollbar-thin">
                  <div className="flex min-w-max items-center gap-1.5">
                    {steps.map((step, i) => {
                      const active = i === currentIndex;
                      const isLocked = locked(i);
                      const done = statusKnown && shownComplete[i];
                      return (
                        <button
                          key={step.slug}
                          // Plain buttons, not a second role="tab" list: the rail
                          // owns the tablist, and exposing the same eight steps
                          // twice would make every step ambiguous. They must stay
                          // in the accessibility tree all the same — the rail is
                          // display:none below lg, so hiding these too would
                          // leave narrow screens with no step navigation at all.
                          aria-label={step.label}
                          aria-disabled={isLocked || undefined}
                          title={isLocked ? `${step.label} — complete the earlier steps first` : step.label}
                          onClick={() => navigateTo(i)}
                          className={`flex max-w-[160px] snap-start items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-[12px] font-medium transition-smooth ${
                            active
                              ? 'cursor-pointer border border-primary-300/60 bg-primary-50 text-primary-700'
                              : isLocked
                                ? 'cursor-not-allowed border border-transparent text-foreground-300'
                                : 'cursor-pointer border border-transparent text-foreground-500 hover:bg-background-100'
                          }`}
                        >
                          <AppIcon className={`text-[13px] ${active ? 'ri-checkbox-blank-circle-line text-primary-500' : done ? 'ri-checkbox-circle-fill text-emerald-500' : isLocked ? 'ri-lock-line text-foreground-300' : 'ri-checkbox-blank-circle-fill text-foreground-300'}`} />
                          <span className="truncate">{step.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
                <button onClick={() => scrollTabs(1)} aria-label="Scroll tabs right" className="hidden h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-foreground-200 text-foreground-500 hover:bg-background-100 sm:flex">
                  <AppIcon className="ri-arrow-right-s-line" />
                </button>
              </div>
              <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-background-200">
                <div className="h-full rounded-full bg-primary-500 transition-all duration-300" style={{ width: `${steps.length ? ((currentIndex + 1) / steps.length) * 100 : 0}%` }} />
              </div>
            </div>

            {/* Step title bar — names the step beside the form on wide screens,
                where the card no longer carries a tab strip of its own. */}
            <div className="hidden items-center justify-between gap-4 border-b border-foreground-100 px-5 py-3.5 md:px-6 lg:flex">
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">
                  Step {currentIndex + 1} of {steps.length}
                </p>
                <p className="truncate text-[15px] font-heading font-semibold text-foreground-900">{currentStep?.label}</p>
              </div>
              {/* Same gate as the footer Next — this arrow is a second way
                  forward, so it cannot be allowed to skip the check. */}
              <button onClick={() => !isLast && navigateTo(currentIndex + 1)} disabled={isLast} aria-label="Next step" className="primary-action flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full bg-primary-500 text-white transition-smooth hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-40">
                <AppIcon className="ri-arrow-right-line" />
              </button>
            </div>

            {/* Step body.
                Keyed on the step so per-step local state (open modals, a
                half-typed row) doesn't leak into the next one. That makes every
                switch a fresh mount, so the entrance has to be cheap — a 0.7s
                slide-from-invisible replayed here read as a page reload. */}
            <div key={currentIndex} ref={bodyRef} className="animate-step-in p-4 sm:p-5 md:p-6 lg:px-8 lg:py-7">
              <StepErrorsContext.Provider value={{ show: showErrors, missing: currentMissing }}>
                {body}
              </StepErrorsContext.Provider>
            </div>

            {/* Outstanding answers on this step, once the learner has tried to
                move on. Listed by field so they know exactly what to go back to. */}
            {showErrors && currentMissing.length > 0 && (
              <div className="mx-4 mb-4 rounded-xl border border-red-200 bg-red-50 p-3.5 sm:mx-5 lg:mx-8">
                <p className="text-[13px] font-semibold text-red-700">
                  <AppIcon className="ri-error-warning-line mr-1.5" />
                  Please complete this step before continuing
                </p>
                <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12px] text-red-700">
                  {currentMissing.map((label) => <li key={label}>{label}</li>)}
                </ul>
              </div>
            )}

            {/* Footer */}
            <div className="flex flex-wrap items-center justify-end gap-2.5 border-t border-foreground-100 bg-background-100/40 px-4 py-4 sm:gap-3 sm:px-5 lg:px-8">
              {/* Routed through navigateTo, not onNavigateStep, so Back saves on
                  the learner side exactly as a backward tab-click does — a
                  learner who typed on this step and pressed Back was otherwise
                  one closed tab from losing it. */}
              {!isFirst && (
                <button onClick={() => navigateTo(currentIndex - 1)} className={btnDestructive}>
                  <AppIcon className="ri-arrow-left-line" />Back
                </button>
              )}
              {/* Staff only — save on every step, not just the ILR, so someone
                  correcting one field on (say) Policies can keep it without
                  walking the rest of the wizard. */}
              {showSaveButton && (
                <button onClick={save} disabled={ilrSaving} className={`${btnSecondary} ${isFirst ? 'ml-auto' : 'sm:ml-auto'} justify-center`}>
                  {ilrSaving ? <><AppIcon className="ri-loader-4-line animate-spin" />Saving…</> : <><AppIcon className="ri-save-line" />Save progress</>}
                </button>
              )}
              {/* Next always reads Next and always moves: the save runs behind
                  the navigation (see navigateTo), so the button never turns into
                  a "Saving…" spinner the learner has to wait out. */}
              {isLast ? (
                <button onClick={finish} disabled={submitting} className={btnSuccess}>
                  {submitting ? <><AppIcon className="ri-loader-4-line animate-spin" />Submitting…</> : <><AppIcon className="ri-check-double-line" />{finishLabel}</>}
                </button>
              ) : (
                <button onClick={() => navigateTo(currentIndex + 1)} className={btnPrimary}>
                  Next<AppIcon className="ri-arrow-right-line" />
                </button>
              )}
            </div>
          </div>

          {/* Right sidebar */}
          {sidebar && <aside className="lg:w-56 shrink-0">{sidebar}</aside>}
        </div>
      </div>
    </div>
  );
}
