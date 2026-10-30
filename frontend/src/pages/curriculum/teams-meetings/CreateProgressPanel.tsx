import { AppIcon } from '@/components/feature/AppIcon';
import { progressSteps, type CreateProgress, type CreateRecovery, type ProgressStepState } from './createRecovery';

const STEP_ICON: Record<ProgressStepState, string> = {
  done: 'ri-checkbox-circle-fill text-emerald-600',
  active: 'ri-loader-4-line animate-spin text-primary-600',
  pending: 'ri-checkbox-blank-circle-line text-foreground-300',
};

/**
 * A Create under way, in place of the form it was sent from.
 *
 * The form has done its job once the author confirms the review, and leaving it
 * on screen put the only sign of progress below a long scroll -- where it read
 * as a stuck dialog. This shows the three things a Create does, each ticked as
 * the server reports it done, and it keeps asking until the server answers, so
 * the author is never left holding a "check again" button for work that has
 * already finished.
 */
export function CreateProgressPanel({
  moduleName,
  sessionCount,
  progress,
  recovery,
  busy,
  onCheckAgain,
  onConfirmUncertain,
  steps: suppliedSteps,
  title,
  description,
}: {
  moduleName: string;
  sessionCount: number;
  progress: CreateProgress | null;
  recovery: CreateRecovery | null;
  busy: boolean;
  onCheckAgain: () => void;
  onConfirmUncertain: () => void;
  steps?: ReturnType<typeof progressSteps>;
  title?: string;
  description?: string;
}) {
  const steps = suppliedSteps || progressSteps(progress?.status || null, sessionCount);
  const stalled = recovery?.phase === 'stalled';
  const uncertain = recovery?.phase === 'uncertain';
  const working = !stalled && !uncertain;
  return (
    <section aria-labelledby="teams-create-progress-title" className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-primary-100 bg-primary-50/60 px-4 py-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-primary-600 text-white">
          <AppIcon className="ri-microsoft-teams-line text-base"></AppIcon>
        </span>
        <div className="min-w-0">
          <h3 id="teams-create-progress-title" className="text-[13px] font-bold text-foreground-800">
            {title || (working ? `Creating the Teams calendar for ${moduleName}` : `The Teams calendar for ${moduleName}`)}
          </h3>
          <p className="mt-0.5 text-[12px] text-foreground-600">
            {description || (working
              ? 'This usually takes under a minute. Closing this window does not stop it: the calendar and its emails are finished on the server either way, and nothing is created twice.'
              : 'Here is what the server reported last.')}
          </p>
        </div>
      </div>

      <ol role="status" aria-live="polite" className="space-y-2">
        {steps.map(step => (
          <li key={step.key}
            className={`flex items-start gap-3 rounded-xl border px-4 py-3 ${step.state === 'active' ? 'border-primary-200 bg-white' : 'border-background-200 bg-background-50'}`}>
            <AppIcon className={`${STEP_ICON[step.state]} mt-0.5 text-lg`}></AppIcon>
            <div className="min-w-0">
              <p className={`text-[13px] font-bold ${step.state === 'pending' ? 'text-foreground-400' : 'text-foreground-800'}`}>
                {step.label}
                <span className="sr-only">{step.state === 'done' ? ' (done)' : step.state === 'active' ? ' (in progress)' : ' (waiting)'}</span>
              </p>
              <p className="text-[12px] text-foreground-500">{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>

      {working && progress?.slow && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] font-semibold text-amber-900">
          This is taking longer than usual. Microsoft is still working on it, so please do not create it again. The result appears here as soon as it is ready.
        </p>
      )}
      {working && (progress?.failures || 0) >= 3 && (
        <p className="rounded-lg border border-background-200 bg-background-100/70 p-3 text-[12px] font-semibold text-foreground-600">
          The progress check is not answering right now, so the steps above may be behind. Still checking; the calendar itself is unaffected.
        </p>
      )}

      {(stalled || uncertain) && (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-semibold">{recovery?.message}</p>
          {stalled && (
            <button type="button" disabled={busy} onClick={onCheckAgain}
              className="mt-2 inline-flex h-8 items-center rounded-lg border border-amber-300 bg-white px-3 text-[12px] font-bold text-amber-900 disabled:opacity-50">
              Check again
            </button>
          )}
          {uncertain && (
            <button type="button" disabled={busy} onClick={onConfirmUncertain}
              className="mt-2 inline-flex h-8 items-center rounded-lg border border-amber-300 bg-white px-3 text-[12px] font-bold text-amber-900 disabled:opacity-50">
              I checked Outlook: the meeting is not there, create it
            </button>
          )}
        </div>
      )}
    </section>
  );
}
