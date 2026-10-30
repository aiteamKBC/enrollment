import { useEffect } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { SAFEGUARDING_URL } from '@/api/wizardInclusion';
import { formatSystemTimestamp } from '@/lib/format';
import { btnPrimary, btnSecondary } from '../../components/ui';
import { useWizard } from "../WizardContext.helpers";
import { StepHeading } from './fields';
import { useText } from '../layout/textsContext';
import { useShowStepErrors } from "../stepErrors.helpers";

/**
 * The Inclusion step (apprenticeship learners only): the learner takes the
 * inclusion assessment on the Safeguarding site, in a new tab, and comes back.
 * It counts as done once the Safeguarding database holds a response for one of
 * their emails — WizardContext reads that into draft.inclusion.
 *
 * The status is checked again when the learner returns to this tab, and on
 * request. Staff see the status only: the site signs in whoever opens it, so
 * from the staff wizard it would sign in the member of staff, not the learner.
 */
export default function Inclusion() {
  const { draft, readOnly, inclusionCheck } = useWizard();
  const t = useText();
  const showErrors = useShowStepErrors();
  const { checking, error, refresh } = inclusionCheck;
  const status = draft.inclusion;
  const done = Boolean(status?.completed);

  // Back from the Safeguarding tab: look again, unless a look is under way.
  useEffect(() => {
    if (done) return;
    const onReturn = () => {
      if (document.visibilityState === 'visible' && !checking) refresh();
    };
    document.addEventListener('visibilitychange', onReturn);
    return () => document.removeEventListener('visibilitychange', onReturn);
  }, [done, checking, refresh]);

  const respondedOn = status?.respondedAt
    ? formatSystemTimestamp(status.respondedAt, { day: 'numeric', month: 'short', year: 'numeric' })
    : '';
  const tone = done
    ? 'border-emerald-200 bg-emerald-50'
    : showErrors
      ? 'border-red-300 bg-red-50 ring-1 ring-inset ring-red-500'
      : 'border-foreground-200 bg-background-50';

  return (
    <div className="max-w-3xl">
      <StepHeading title={t('block.inclusion.title')} />
      <p className="text-[13px] leading-relaxed text-foreground-700">{t('block.inclusion.body')}</p>

      <div className={`mt-4 rounded-xl border p-4 ${tone}`}>
        <div role="status" aria-live="polite" className="text-[13px]">
          {done ? (
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-medium text-emerald-800">
              <AppIcon className="ri-checkbox-circle-fill" />
              {readOnly ? 'The learner has completed the inclusion assessment.' : t('block.inclusion.done')}
              {respondedOn && <span className="font-normal text-emerald-700">Received {respondedOn}</span>}
            </p>
          ) : !status && !error ? (
            // Not looked up yet (the check waits for the saved answers), or under way.
            <p className="text-foreground-500"><AppIcon className="ri-loader-4-line mr-1.5 inline animate-spin" />Checking the inclusion assessment…</p>
          ) : error ? (
            <p className="text-red-700"><AppIcon className="ri-error-warning-line mr-1.5 inline" />{error}</p>
          ) : (
            <p className={showErrors ? 'text-red-700' : 'text-foreground-700'}>
              {readOnly ? 'The learner has not completed the inclusion assessment yet.' : 'You have not completed the inclusion assessment yet.'}
            </p>
          )}
        </div>

        {!done && (
          <div className="mt-3 flex flex-wrap gap-2">
            {!readOnly && (
              <a href={SAFEGUARDING_URL} target="_blank" rel="noopener noreferrer" className={btnPrimary}>
                <AppIcon className="ri-external-link-line" />{t('block.inclusion.start')}
              </a>
            )}
            <button type="button" className={btnSecondary} onClick={refresh} disabled={checking}>
              <AppIcon className={checking ? 'ri-loader-4-line animate-spin' : 'ri-refresh-line'} />
              {checking ? 'Checking…' : readOnly ? 'Check again' : 'I have finished — check again'}
            </button>
          </div>
        )}
        {readOnly && !done && (
          <p className="mt-2 text-[11.5px] text-foreground-500">The learner starts the assessment from their own enrolment.</p>
        )}
      </div>
    </div>
  );
}
