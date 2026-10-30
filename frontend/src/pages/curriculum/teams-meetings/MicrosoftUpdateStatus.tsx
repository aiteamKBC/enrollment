import { useEffect, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { InlineError } from '../shared/entities/ui';
import {
  MICROSOFT_UPDATE_LABELS,
  MICROSOFT_UPDATE_TONES,
  MICROSOFT_VERIFICATION_NOTES,
  OPTION_GROUP_NAMES,
  retryMicrosoftMeetingOptions,
  type MicrosoftUpdateSummary,
} from './microsoftUpdateState';

/**
 * Saved / Pending Microsoft update / Failed for one meeting's settings, with
 * Retry. Retry re-sends only what Microsoft has not applied: it never moves a
 * date, never emails anyone and never cancels anything.
 */
export function MicrosoftUpdateStatus({ liveSessionId, status, disabled, onChange }: {
  liveSessionId: string;
  status: MicrosoftUpdateSummary | null;
  disabled?: boolean;
  onChange?: (status: MicrosoftUpdateSummary) => void;
}) {
  const [current, setCurrent] = useState(status);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  useEffect(() => { setCurrent(status); setError(''); setNote(''); }, [status, liveSessionId]);
  if (!current) return null;

  const retry = async () => {
    setRetrying(true); setError(''); setNote('');
    try {
      const result = await retryMicrosoftMeetingOptions(liveSessionId);
      setCurrent(result.microsoftUpdate);
      setNote(result.message);
      onChange?.(result.microsoftUpdate);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The retry could not finish. Invitations and dates are unchanged.');
    } finally {
      setRetrying(false);
    }
  };
  const lastError = current.lastError;

  return (
    <section aria-label="Microsoft update status" className="space-y-2 rounded-xl border border-background-200 p-3 text-[12px]">
      <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Meeting settings</span>
        <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold ${MICROSOFT_UPDATE_TONES[current.state]}`}>
          {MICROSOFT_UPDATE_LABELS[current.state]}
        </span>
      </div>
      {current.message && <p>{current.message}</p>}
      {current.verification && current.state === 'saved' && (
        <p className="text-[11px] text-foreground-600">{MICROSOFT_VERIFICATION_NOTES[current.verification]}</p>
      )}
      {current.pendingGroups.length > 0 && (
        <p className="text-foreground-600">Waiting: {current.pendingGroups.map(group => OPTION_GROUP_NAMES[group] || group).join('; ')}.</p>
      )}
      {lastError && (
        <p className="break-all text-[11px] text-foreground-500">
          Microsoft said: {[lastError.status ? `HTTP ${lastError.status}` : '', lastError.code, lastError.message].filter(Boolean).join(' ')}
          {lastError.requestId ? ` · request ID ${lastError.requestId}` : ''}
          {lastError.at ? ` · ${new Date(lastError.at).toLocaleString('en-GB')}` : ''}
        </p>
      )}
      {current.retryable && (
        <button type="button" disabled={disabled || retrying || !liveSessionId} onClick={() => void retry()}
          className="inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[12px] font-semibold disabled:opacity-50">
          <AppIcon className={retrying ? 'ri-loader-4-line animate-spin' : 'ri-refresh-line'} />
          {retrying ? 'Retrying…' : 'Retry Microsoft update'}
        </button>
      )}
      {current.retryable && (
        <p className="text-[11px] text-foreground-500">Retry sends only these settings to Microsoft. It does not change dates, email anyone or cancel anything.</p>
      )}
      {note && <p className="font-semibold">{note}</p>}
      {error && <InlineError message={error} />}
    </section>
  );
}
