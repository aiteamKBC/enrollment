import { slotLabel } from './SessionSlotPicker.helpers';
// ============================================================================
// The hours a first session can be booked at, taken from the case owner's
// calendar.
//
// This replaced a pair of hour/minute columns that let anyone compose any time
// of day. Two things were wrong with that: it offered hours the college does
// not work, and it offered hours the case owner was already booked for — which
// the learner only discovered when the booking failed, or worse, when it
// succeeded on top of an existing meeting.
//
// Shared because the same control appears in both places a first session is
// arranged: the learner booking their own, and the same learner moving it.
// One copy so the two cannot drift about which hours the college works.
// ============================================================================

import type { SessionSlot } from '@/api/learnerCalendar';

/**
 * The college day as a row of hours, each one bookable or not.
 *
 * A taken hour is shown rather than hidden: "10am is taken" tells the learner
 * something true about their case owner's day, where a missing 10am reads as
 * the college not working then. Only `available` decides whether an hour can
 * be chosen, never the label, so an hour that cannot be booked cannot be
 * picked by mistake.
 */
export function SessionSlotPicker({
  slots,
  value,
  onChange,
  labelledBy,
  disabled = false,
  loading = false,
  error = '',
  unconfirmed = '',
  emptyMessage = 'Your case owner has no free hours on that day. Please choose another.',
}: {
  slots: SessionSlot[];
  value: string;
  onChange: (next: string) => void;
  labelledBy?: string;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
  /** Set when the case owner's calendar could not be read, so these are the
   *  college's hours offered unchecked rather than their real free time. */
  unconfirmed?: string;
  emptyMessage?: string;
}) {
  if (loading) {
    return (
      <p role="status" className="text-[13px] text-foreground-500">
        Checking your case owner’s calendar…
      </p>
    );
  }

  // A calendar that cannot be read is not a day with no free hours. Saying so
  // plainly keeps the learner from picking another date to fix a problem that
  // is not about the date.
  if (error) {
    return (
      <p role="alert" className="text-[13px] text-red-700">
        {error}
      </p>
    );
  }

  if (!slots.length) {
    return (
      <p role="status" className="text-[13px] text-foreground-500">
        {emptyMessage}
      </p>
    );
  }

  const free = slots.some((slot) => slot.available);

  return (
    <div className="flex flex-col gap-2">
      <ul
        role="group"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : 'Available times'}
        className="flex flex-wrap gap-2"
      >
        {slots.map((slot) => {
          const selected = value === slot.time;
          return (
            <li key={slot.time}>
              <button
                type="button"
                aria-pressed={selected}
                // A booked hour stays on the page so the day reads correctly,
                // but it is not a choice. The reason is on the button itself
                // rather than only in a colour, so it survives a screen reader.
                disabled={disabled || !slot.available}
                aria-label={
                  slot.available
                    ? slotLabel(slot.time)
                    : `${slotLabel(slot.time)} — already booked`
                }
                onClick={() => onChange(slot.time)}
                className={`rounded-lg border px-3 py-2 text-[13px] font-medium tabular-nums transition-smooth ${
                  selected
                    ? 'border-primary-500 bg-primary-500 text-white font-semibold'
                    : 'border-foreground-200 bg-background-50 text-foreground-700 hover:border-primary-400 hover:bg-background-100'
                } disabled:cursor-not-allowed disabled:border-foreground-100 disabled:bg-background-100 disabled:text-foreground-400 disabled:line-through disabled:hover:border-foreground-100 disabled:hover:bg-background-100`}
              >
                {slotLabel(slot.time)}
              </button>
            </li>
          );
        })}
      </ul>
      {!free && (
        <p role="status" className="text-[12px] text-foreground-500">
          {emptyMessage}
        </p>
      )}
      {unconfirmed && (
        // Said plainly, and not as an error: the learner can and should still
        // book. What they must not do is believe the hour was checked.
        <p role="status" className="text-[12px] text-amber-700">
          We could not check your case owner{'’'}s calendar, so these are the
          college{'’'}s usual hours. Book the time that suits you — your case
          owner will confirm it, or get in touch to move it.
        </p>
      )}
    </div>
  );
}
