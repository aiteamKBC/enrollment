import { AppIcon } from '@/components/feature/AppIcon';

/* ═══════════════════════════════════════════════════════
   READ-ONLY LEARNER NOTICE — shown in place of an activity
   runner, or an upload form, when the viewer is not the
   learner whose workspace is open.

   The learner pages are reachable by staff two ways: through
   the training-plan rows (now inert — see useLearnerWorkspaceAccess)
   and by URL, either pasted or from the browser's history. This
   is what the second route lands on, so a deep link explains
   itself instead of opening a quiz nobody is allowed to submit.
   ═══════════════════════════════════════════════════════ */

export function ReadOnlyLearnerNotice({ what, onBack }: {
  /** The thing that cannot be done, e.g. "complete this component". */
  what: string;
  onBack?: () => void;
}) {
  return (
    <div className="rounded-2xl border border-primary-200/70 bg-background-50 p-6 md:p-8 text-center">
      <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-600">
        <AppIcon className="ri-eye-line text-xl" />
      </span>
      <h2 className="font-heading text-base font-bold text-foreground-900">
        You are viewing this learner read-only
      </h2>
      <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-foreground-500">
        The learner or an administrator can {what}.
        You can still review everything on their plan, and book a session with them.
      </p>
      {onBack && (
        <button
          onClick={onBack}
          className="mt-5 inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-primary-700"
        >
          <AppIcon className="ri-arrow-left-line text-[13px]" /> Back to the training plan
        </button>
      )}
    </div>
  );
}
