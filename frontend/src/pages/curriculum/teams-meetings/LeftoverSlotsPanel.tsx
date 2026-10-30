import { AppIcon } from '@/components/feature/AppIcon';
import type { TeamsLeftoverSlot } from '../module-builder/moduleAuthoringData';
import { leftoverSlotLabel } from './leftoverSlots';

/**
 * Teams slots the module plan does not hold, each with its own Cancel.
 *
 * Nothing automatic removes these -- not Update, not Create, not the status
 * check -- because Microsoft emails a cancellation to everyone invited the
 * moment one is removed. They stay on Teams until a person presses Cancel here
 * and confirms it.
 */
export function LeftoverSlotsPanel({ slots, cancelling, disabled, onCancel }: {
  slots: TeamsLeftoverSlot[];
  cancelling: string;
  disabled: boolean;
  onCancel: (slot: TeamsLeftoverSlot) => void;
}) {
  if (!slots.length) return null;
  return (
    <section aria-labelledby="teams-leftover-heading" className="rounded-xl border border-amber-200 bg-amber-50/70 p-4">
      <h3 id="teams-leftover-heading" className="text-[11px] font-bold uppercase tracking-wider text-amber-800">
        On Teams, but not a session of this module
      </h3>
      <p className="mt-1 text-[11px] text-amber-800">
        Microsoft still shows {slots.length === 1 ? 'this slot' : 'these slots'} to everyone invited. Nothing was
        cancelled automatically, so nobody was emailed. Cancel one only if it should go: Microsoft then emails
        everyone invited that it is cancelled.
      </p>
      <ul className="mt-3 space-y-2">
        {slots.map(slot => (
          <li key={slot.eventId} className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2">
            <span className="min-w-0 flex-1 text-[12px] font-semibold text-foreground-700">{leftoverSlotLabel(slot)}</span>
            <button
              type="button"
              onClick={() => onCancel(slot)}
              disabled={disabled || Boolean(cancelling)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 text-[11px] font-bold text-red-700 transition-smooth hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <AppIcon className={cancelling === slot.eventId ? 'ri-loader-4-line animate-spin text-sm' : 'ri-close-circle-line text-sm'}></AppIcon>
              Cancel on Teams
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
