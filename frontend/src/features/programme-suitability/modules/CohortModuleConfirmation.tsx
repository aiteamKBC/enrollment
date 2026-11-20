import { useEffect, useRef, useState } from 'react';
import { btnPrimary } from '@/pages/users/components/ui';
import { request } from '../api';
import { selectionFingerprint, type ProgrammeModuleSelection } from './configuration';
import type { ScheduleContext } from './useModuleSchedules';

interface Confirmation {
  status: 'ready' | 'allocated'; selectionRevision: number;
  savedSelection?: ProgrammeModuleSelection;
  groupId?: string; groupName?: string; cohortId?: string; confirmedAt?: string;
  offerings?: { academicModuleId: string; offeringId: string; cohortId: string; groupId: string }[];
}

/** One explicit learner action; availability is never a reservation. */
export function CohortModuleConfirmation({ context, selection, saving, readOnly, onConfirmed, onBusy }: {
  context: ScheduleContext; selection: ProgrammeModuleSelection; saving: boolean; readOnly: boolean;
  onConfirmed: (value: boolean) => void; onBusy: (value: boolean) => void;
}) {
  const url = `/enrolment_api/programme-suitability/${context.kind}/${encodeURIComponent(context.id)}/cohort-module-confirmation/`;
  const fingerprint = selectionFingerprint(selection);
  const [state, setState] = useState<Confirmation>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const readSequence = useRef(0);
  useEffect(() => {
    let active = true;
    const load = async () => {
      if (saving || submitting.current) return;
      const sequence = ++readSequence.current;
      try {
        const value = await request<Confirmation>(url);
        if (active && sequence === readSequence.current && !submitting.current) {
          setState(value); setError(''); onConfirmed(value.status === 'allocated');
        }
      } catch (failure) {
        if (active && sequence === readSequence.current && !submitting.current) {
          setState(undefined); setError(failure instanceof Error ? failure.message : 'Could not check cohort module confirmation.');
        }
      }
    };
    void load();
    window.addEventListener('focus', load);
    return () => { active = false; window.removeEventListener('focus', load); };
  }, [url, fingerprint, saving, onConfirmed]);

  const confirm = async () => {
    if (submitting.current || readOnly || saving || state?.status !== 'ready'
      || selectionFingerprint(state.savedSelection) !== fingerprint) return;
    submitting.current = true; ++readSequence.current;
    setBusy(true); onBusy(true); setError('');
    try {
      const value = await request<Confirmation>(url, { expectedRevision: state.selectionRevision });
      setState(value); onConfirmed(value.status === 'allocated');
      window.dispatchEvent(new CustomEvent('pcp-cohort-modules-confirmed', { detail: context }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not confirm your cohort modules. Please retry.');
    } finally {
      submitting.current = false; setBusy(false); onBusy(false);
    }
  };

  return <section aria-label="Cohort module confirmation" className="mt-5 rounded-xl border border-primary-200 bg-background-50 p-4">
    {state?.status === 'allocated' ? <div role="status">
      <h4 className="font-semibold text-primary-800">Your cohort modules are confirmed.</h4>
      <p className="mt-2 text-sm">Permanently assigned group: <strong>{state.groupName}</strong></p>
      <p className="mt-1 text-sm text-foreground-600">{state.offerings?.length ?? 0} home-cohort modules confirmed. Your home cohort has not changed.</p>
    </div> : <>
      <button type="button" className={btnPrimary} onClick={() => void confirm()}
        disabled={readOnly || saving || busy || state?.status !== 'ready' || selectionFingerprint(state.savedSelection) !== fingerprint}>
        {busy ? 'Confirming cohort modules…' : 'Confirm My Cohort Modules'}
      </button>
      <p role="status" className="mt-2 text-sm text-foreground-600">{saving ? 'Save your academic module choices before confirming.'
        : !state && !error ? 'Checking your saved home-cohort modules…'
        : 'Your group is allocated automatically within your saved home cohort. Other-cohort offerings cannot be confirmed.'}</p>
    </>}
    {error && <p role="alert" className="mt-2 text-sm text-amber-800">{error}</p>}
  </section>;
}
