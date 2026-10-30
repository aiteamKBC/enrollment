import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AppIcon } from './AppIcon';
import type { WorkingHoursHoliday } from '@/lib/componentAccessWindow';
import {
  declaredCompletedAt,
  reasonHeadline,
  ukDateKey,
  workingRuleFailure,
  type WorkingRuleReason,
} from '@/lib/completionValidation';

// ============================================================================
// The one moment the working rules are put to the learner.
//
// Nothing about studying is restricted: this opens only after a Finish click
// the server refused, and the refusal wrote nothing — no completion, no
// percentage, no history entry. The learner picks the date and time the work
// was actually done at, and Final Submit sends that instant to be written.
//
// Clock choices are limited to 07:00 through 19:00 inclusive. Weekend and
// holiday warnings remain advisory; the server also rejects future instants.
// ============================================================================

interface CompletionTimeDialogProps {
  /** Why the learner's real Finish click was refused. */
  reason: WorkingRuleReason | '';
  holidayName: string;
  /** Bank holidays for THIS learner's cohort, one entry per date, named where the server named them. */
  holidays: WorkingHoursHoliday[];
  submitting: boolean;
  /** A server refusal of the declared instant, shown in place of the local one. */
  serverError: string;
  onSubmit: (declaredAt: string) => void;
  onClose: () => void;
}

/** Today in UK terms — the learner cannot declare a future completion. */
function todayKey(): string {
  return ukDateKey(new Date());
}

export function CompletionTimeDialog({
  reason,
  holidayName,
  holidays,
  submitting,
  serverError,
  onSubmit,
  onClose,
}: CompletionTimeDialogProps) {
  const titleId = useId();
  const dateId = useId();
  const timeId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  const [day, setDay] = useState('');
  const [hour, setHour] = useState('');
  const [minute, setMinute] = useState('');
  const time = hour && minute ? `${hour}:${minute}` : '';
  const [touched, setTouched] = useState(false);

  const failure = useMemo(
    () => (day && time ? workingRuleFailure(day, time, holidays) : null),
    [day, time, holidays],
  );
  const complete = Boolean(day && /^\d{2}:\d{2}$/.test(time) && time >= '07:00' && time <= '19:00');
  const canSubmit = complete && !submitting;

  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    const dialog = dialogRef.current!;
    document.body.style.overflow = 'hidden';
    dialog.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!dialog.contains(document.activeElement)) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
      )).filter(element => !element.closest('[hidden], [inert], [aria-hidden="true"]'));
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first) {
        event.preventDefault();
        dialog.focus();
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
        event.preventDefault();
        first.focus();
      }
    };
    dialog.addEventListener('keydown', handleKeyDown);
    return () => {
      dialog.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);

  const submit = () => {
    setTouched(true);
    if (!canSubmit) return;
    onSubmit(declaredCompletedAt(day, time));
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-foreground-950/40 p-3 backdrop-blur-sm sm:p-6"
      onClick={event => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="flex max-h-[calc(100dvh-1.5rem)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-foreground-200 bg-white text-foreground-900 shadow-2xl outline-none sm:max-h-[calc(100dvh-3rem)]"
      >
        <div className="shrink-0 border-b border-foreground-200 bg-background-50 px-5 py-4 sm:px-6">
          <div className="mb-3 flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 text-xs font-semibold text-foreground-500">
              <AppIcon className="ri-time-line h-4 w-4" />Completion date and time
            </span>
            <button
              type="button"
              aria-label="Close without completing"
              onClick={onClose}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-foreground-500 transition hover:bg-background-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
            >
              <AppIcon className="ri-close-line h-5 w-5" />
            </button>
          </div>
          <h2 id={titleId} className="min-w-0 break-words text-xl font-heading font-bold leading-snug text-foreground-950">
            Please review your completion date and time
          </h2>
        </div>

        <div className="min-h-0 space-y-4 overflow-y-auto overscroll-contain p-5 sm:p-6">
          <div role="note" className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
            <p className="font-bold">The selected completion time is outside official working rules.</p>
            <p className="mt-1 text-xs font-semibold">Reason: {reasonHeadline(reason, holidayName)}</p>
            <p className="mt-2 text-xs leading-5 text-amber-900/80">
              Your activity has not been completed yet. Select the date and time you
              actually did this work, then choose Final Submit.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="min-w-0">
              <label htmlFor={dateId} className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-foreground-500">
                Completion date
              </label>
              <input
                id={dateId}
                type="date"
                value={day}
                max={todayKey()}
                onChange={event => { setDay(event.target.value); setTouched(true); }}
                className="w-full rounded-xl border border-foreground-300 px-3 py-2.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
              />
            </div>
            <div className="min-w-0">
              <label htmlFor={timeId} className="mb-1.5 block text-xs font-bold uppercase tracking-wider text-foreground-500">
                Completion time
              </label>
              <div className="flex gap-2">
                <select
                  id={timeId}
                  value={hour}
                  onChange={event => {
                    setHour(event.target.value);
                    if (event.target.value === '19') setMinute('00');
                    setTouched(true);
                  }}
                  className="w-full rounded-xl border border-foreground-300 px-3 py-2.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
                >
                  <option value="">Hour</option>
                  {Array.from({ length: 13 }, (_, index) => index + 7).map(value => (
                    <option key={value} value={String(value).padStart(2, '0')}>
                      {value % 12 || 12} {value < 12 ? 'AM' : 'PM'}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Completion minutes"
                  value={minute}
                  onChange={event => { setMinute(event.target.value); setTouched(true); }}
                  className="w-full rounded-xl border border-foreground-300 px-3 py-2.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
                >
                  <option value="">Minute</option>
                  {Array.from({ length: hour === '19' ? 1 : 60 }, (_, index) => String(index).padStart(2, '0')).map(value => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <p className="text-xs leading-5 text-foreground-500">
            Official working hours are Monday to Friday, 07:00-19:00 UK time, excluding
            bank holidays.
          </p>

          {serverError ? (
            <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-semibold text-red-900">
              {serverError}
            </p>
          ) : touched && complete && failure ? (
            <p role="status" className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-950">
              This date/time is outside official working rules.
              {' '}{failure.message}{' '}You can still submit it.
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-foreground-200 bg-white px-5 py-3 sm:px-6">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl px-4 py-2.5 text-sm font-bold text-foreground-600 transition hover:bg-background-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            aria-disabled={!canSubmit}
            title={!complete ? 'Select a completion date and time.' : undefined}
            className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-5 py-2.5 text-sm font-bold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-foreground-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500"
          >
            <AppIcon className={canSubmit ? 'ri-check-line' : 'ri-lock-line'} />
            {submitting ? 'Submitting…' : 'Final Submit'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
