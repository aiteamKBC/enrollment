import { useMemo, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import type { CurriculumTutorAvailabilityVerdict } from '@/lib/curriculumApi';

/**
 * Why a tutor cannot take the slot being filled in, shown while they are still
 * being picked rather than after the save is refused.
 *
 * The sessions are named, not counted: "already teaching on 3 dates" leaves the
 * reader to go and find out which, and the fix is nearly always to move one of
 * the two modules — a decision they can only make once they can see which
 * sessions collide and what the other module is. So each clashing module gets
 * its own row, carrying where it sits in the curriculum, the hours it holds and
 * the dated sessions this slot would land on top of.
 *
 * `freeTutors` turns the warning into the fix: the names it lists are the ones
 * the same roster call already knows are free in this slot, so swapping is one
 * click rather than a hunt through the picker.
 *
 * Pair it with `useTutorAvailability`, and render only when the verdict says the
 * tutor is unavailable.
 */
export function TutorClashNotice({
  verdict,
  sessionDates = [],
  freeTutors = [],
  onPickTutor,
}: {
  verdict: CurriculumTutorAvailabilityVerdict;
  /** Every date the slot books, so the clash can be given as "3 of 36". */
  sessionDates?: string[];
  /** Tutors the same slot has no clash for. Trimmed for display by the caller. */
  freeTutors?: string[];
  /** Given, each free name becomes a button that swaps the tutor over. */
  onPickTutor?: (tutor: string) => void;
}) {
  const clashDates = useMemo(() => {
    const dates = new Set(verdict.conflicts.flatMap(conflict => conflict.dates));
    return Array.from(dates).sort();
  }, [verdict]);

  return (
    <div className="overflow-hidden rounded-xl border border-red-200 bg-red-50">
      <div className="flex items-start gap-2.5 px-3.5 py-3">
        <AppIcon className="ri-error-warning-fill mt-0.5 shrink-0 text-base text-red-600"></AppIcon>
        <div className="min-w-0">
          <p className="text-[13px] font-bold text-red-800">
            {verdict.tutor} is already teaching in this slot
          </p>
          <p className="mt-0.5 text-[12px] leading-5 text-red-700">
            {sessionDates.length
              ? `${clashDates.length} of this module's ${sessionDates.length} sessions land on a session they already hold.`
              : `${clashDates.length} of this module's sessions land on a session they already hold.`}
            {' '}Saving will be refused.
          </p>
        </div>
      </div>

      <div className="space-y-2 border-t border-red-100 bg-background-0/60 px-3.5 py-3">
        {verdict.conflicts.map(conflict => (
          <ClashedModule key={conflict.moduleCatalogueId} conflict={conflict} />
        ))}
      </div>

      <div className="border-t border-red-100 px-3.5 py-2.5">
        {freeTutors.length ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-bold uppercase tracking-wider text-red-700">Free in this slot</span>
            {freeTutors.map(name => (
              onPickTutor ? (
                <button
                  key={name}
                  type="button"
                  onClick={() => onPickTutor(name)}
                  className="inline-flex h-7 items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 text-[11px] font-bold text-emerald-700 transition-smooth hover:bg-emerald-100"
                >
                  <AppIcon className="ri-user-follow-line text-[12px]"></AppIcon>
                  {name}
                </button>
              ) : (
                <span
                  key={name}
                  className="inline-flex h-7 items-center rounded-full border border-emerald-200 bg-emerald-50 px-2.5 text-[11px] font-bold text-emerald-700"
                >
                  {name}
                </span>
              )
            ))}
          </div>
        ) : (
          <p className="text-[11px] leading-5 text-red-700">
            No tutor is free in this slot. Change the delivery day, the time or the start date.
          </p>
        )}
      </div>
    </div>
  );
}

/** How many dated chips are shown before the rest go behind "show all". */
const DATE_PREVIEW = 6;

function ClashedModule({ conflict }: { conflict: CurriculumTutorAvailabilityVerdict['conflicts'][number] }) {
  const [expanded, setExpanded] = useState(false);
  const dates = expanded ? conflict.dates : conflict.dates.slice(0, DATE_PREVIEW);
  const hidden = conflict.dates.length - dates.length;
  const placement = [conflict.programme, conflict.cohort, conflict.group].filter(Boolean).join(' › ');

  return (
    <div className="rounded-lg border border-background-200 bg-background-0 px-3 py-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-[12px] font-bold text-foreground-900">{conflict.moduleName}</p>
        <span className="inline-flex items-center gap-1 rounded-full bg-background-100 px-2 py-0.5 text-[11px] font-bold text-foreground-600">
          <AppIcon className="ri-time-line text-[12px]"></AppIcon>
          {conflict.startTime}–{conflict.endTime}
        </span>
      </div>
      {placement && <p className="mt-0.5 text-[11px] text-foreground-500">{placement}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {dates.map(date => (
          <span
            key={date}
            className="inline-flex items-center rounded-md border border-red-200 bg-red-50 px-1.5 py-0.5 text-[11px] font-semibold text-red-700"
          >
            {clashDateLabel(date)}
          </span>
        ))}
        {hidden > 0 && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-bold text-primary-700 underline decoration-dotted transition-smooth hover:text-primary-800"
          >
            +{hidden} more session{hidden === 1 ? '' : 's'}
          </button>
        )}
        {expanded && conflict.dates.length > DATE_PREVIEW && (
          <button
            type="button"
            onClick={() => setExpanded(false)}
            className="inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-bold text-primary-700 underline decoration-dotted transition-smooth hover:text-primary-800"
          >
            Show fewer
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * `2026-09-16` -> `Wed 16 Sep 2026`. The weekday earns its place: a clash is
 * nearly always fixed by moving a delivery day, so the day of the week is the
 * part of the date the reader acts on.
 *
 * Formatted in two passes because asking for the weekday and the date together
 * puts a comma between them, which reads as a list once six of these sit in a
 * row. The date half is worded exactly as `formatDateLabel` words it.
 */
function clashDateLabel(value: string): string {
  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  const weekday = parsed.toLocaleDateString('en-GB', { weekday: 'short' });
  const date = parsed.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  return `${weekday} ${date}`;
}
