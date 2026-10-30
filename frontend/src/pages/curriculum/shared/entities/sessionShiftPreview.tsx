import { buildHolidayShiftPlan, monthKeyOf, monthLabelOf, weekdayLabel, holidayCausePhrase, type HolidayShiftSessionLike, type SessionShift, type HolidayShiftPlan } from './sessionShiftPreview.helpers';
export type { HolidayShiftSessionLike, SessionShift, HolidayShiftPlan } from './sessionShiftPreview.helpers';
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { cleanText, formatDateLabel } from './model';

/** A holiday as the curriculum stores it, in the shape every plan serves. */
export interface ReadingWeekHoliday {
  id?: string;
  label: string;
  startDate: string;
  endDate: string;
  type?: string;
  notes?: string;
  /** Which calendar the holiday came from: the mirrored GOV.UK feed, or one
   *  somebody entered on the Holidays page. */
  source?: string;
}

/** A closed delivery slot: a curriculum position with no live session in it. */
export interface HolidayReadingWeekSlot {
  slotNumber: number;
  date: string;
  day?: string;
  holidays: ReadingWeekHoliday[];
}

/**
 * The holiday notice a week's own delivery day puts on that week.
 *
 * It states a fact and stops there. The week is a completely ordinary week --
 * same date, same place in the run, same components, same live session -- and
 * what to do about the holiday is the author's call: rename the week a reading
 * week, drop its live session, move a component, or leave it. Nothing here and
 * nothing behind it changes the plan, so no date moves and the module still
 * ends where its delivery pattern says it ends.
 *
 * It names the holiday's own label, dates, type and any note the GOV.UK feed
 * carried, all read off the holiday record the scheduler already matched, so
 * the author can judge the clash without opening the holidays page.
 *
 * A strip inside the week card rather than a card of its own: there is no
 * second week to draw, and drawing one made a single ticked holiday read as an
 * extra week in the course with a number that collided with a real one.
 */
export function WeekHolidayNotice({
  slot,
  weekDate,
  compact = false,
  note,
}: {
  slot: HolidayReadingWeekSlot;
  /**
   * The date the week's own header already prints, directly above this notice.
   *
   * When the closed day IS that date, naming it here said the same day twice on
   * two adjacent lines. It is still named when the two differ, which is the case
   * that needs it: a week delivering twice can have its second day closed while
   * the header shows its first.
   */
  weekDate?: string;
  /** Tighter type and spacing, for the narrow Course structure rail. */
  compact?: boolean;
  /**
   * The author's hint for this week, when the reader is allowed to write one.
   *
   * Omitted everywhere else, which is what makes this curriculum-only: a screen
   * that merely reads the notice never passes it, so it renders the warning and
   * nothing else. The control appears only inside this notice, so it exists only
   * for a week a holiday actually lands on.
   */
  note?: WeekHolidayNoteEditor;
}) {
  const holidays = (slot.holidays || []).filter(holiday => cleanText(holiday.label) || cleanText(holiday.startDate));
  const slotDate = cleanText(slot.date);
  const shownAbove = Boolean(slotDate) && slotDate.slice(0, 10) === cleanText(weekDate).slice(0, 10);
  return (
    <div
      data-testid="week-holiday-notice"
      className={`relative border-t border-amber-200 bg-amber-50/70 ${compact ? 'px-2.5 py-1.5' : 'px-3 py-2'}`}
    >
      <span aria-hidden className="pointer-events-none absolute inset-y-0 left-0 w-[3px] bg-amber-400"></span>
      <p className={`flex flex-wrap items-center gap-x-1.5 gap-y-0.5 font-semibold text-amber-900 ${compact ? 'text-[10px]' : 'text-[11px]'}`}>
        <AppIcon className="ri-calendar-close-line shrink-0 text-sm text-amber-600"></AppIcon>
        <span>Heads up: this week falls on a holiday</span>
        {!shownAbove && slotDate && (
          <span className="font-medium text-foreground-500">
            {formatDateLabel(slotDate)} {weekdayLabel(slotDate)}
          </span>
        )}
      </p>
      {holidays.map((holiday, index) => (
        <p
          key={`${holiday.id || holiday.label}-${holiday.startDate}-${index}`}
          className={`mt-0.5 leading-snug text-amber-900 ${compact ? 'text-[10px]' : 'text-[11px]'}`}
        >
          <span className="font-bold">{cleanText(holiday.label) || 'Holiday'}</span>
          {' '}
          {/* Where the day came from decides who can change it: a GOV.UK row is
              mirrored reference data, a manual one was entered on the Holidays
              page. Same wording as that page's Source filter. */}
          <span className={`rounded-full border px-1.5 py-px align-middle font-bold ${
            holiday.source === 'gov.uk'
              ? 'border-sky-200 bg-sky-50 text-sky-700'
              : 'border-amber-300 bg-amber-100 text-amber-800'
          } ${compact ? 'text-[9px]' : 'text-[10px]'}`}>
            {holiday.source === 'gov.uk' ? 'GOV.UK' : 'Manual'}
          </span>
          {' · '}
          {formatDateLabel(holiday.startDate)}
          {cleanText(holiday.endDate) && holiday.endDate !== holiday.startDate && ` – ${formatDateLabel(holiday.endDate)}`}
          {cleanText(holiday.type) && ` · ${cleanText(holiday.type)}`}
          {cleanText(holiday.notes) && ` · ${cleanText(holiday.notes)}`}
        </p>
      ))}
      {note && <WeekHolidayNoteControl note={note} compact={compact} />}
    </div>
  );
}

/**
 * The hint a curriculum author writes on a week a holiday falls on, and the
 * switch that publishes it.
 *
 * Two values rather than one, because "written" and "shown" are different
 * decisions: an author drafting a note has not published it, and one turning
 * the hint off for a week that ended up running normally should not have to
 * delete what they wrote to do it. The learner side reads the switch, so text
 * alone reaches nobody.
 *
 * Empty text cannot be published -- the switch is disabled until something is
 * written -- so "shown" never means an empty hint on a learner's week.
 */
export interface WeekHolidayNoteEditor {
  enabled: boolean;
  message: string;
  onChange: (next: { enabled: boolean; message: string }) => void;
}

/** How much a hint can say. Long enough for instructions, short enough to read on a week card. */
export const WEEK_HOLIDAY_NOTE_MAX_LENGTH = 400;

function WeekHolidayNoteControl({ note, compact }: { note: WeekHolidayNoteEditor; compact: boolean }) {
  const [open, setOpen] = useState(false);
  const fieldId = useId();
  const message = String(note.message || '');
  const written = Boolean(message.trim());
  // Text with the switch off is a draft, and a draft is not a hint anybody has.
  const published = written && note.enabled;
  const textSize = compact ? 'text-[10px]' : 'text-[11px]';

  if (!open) {
    return (
      <div className={`mt-1 flex flex-wrap items-center gap-1.5 ${textSize}`} data-testid="week-holiday-note-summary">
        {written ? (
          <>
            <span className={`rounded-full border px-1.5 py-px font-bold ${
              published
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                : 'border-foreground-200 bg-background-100 text-foreground-500'
            }`}>
              {published ? 'Shown to learners' : 'Hidden from learners'}
            </span>
            <span className="min-w-0 flex-1 truncate font-medium text-amber-900" title={message}>{message}</span>
          </>
        ) : (
          <span className="font-medium text-foreground-500">No note for learners</span>
        )}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-md border border-amber-300 bg-white px-1.5 py-px font-bold text-amber-800 transition-smooth hover:bg-amber-100"
        >
          {written ? 'Edit note for learners' : 'Add note for learners'}
        </button>
      </div>
    );
  }

  return (
    <div className={`mt-1.5 rounded-lg border border-amber-300 bg-white p-2 ${textSize}`} data-testid="week-holiday-note-editor">
      <label className="block font-bold text-amber-900" htmlFor={fieldId}>Note for learners in this week</label>
      <textarea
        id={fieldId}
        value={message}
        maxLength={WEEK_HOLIDAY_NOTE_MAX_LENGTH}
        rows={compact ? 3 : 4}
        placeholder="What should learners do about this holiday week?"
        // Clearing the text un-publishes with it: an empty hint on a learner's
        // week is not a hint, and leaving the switch on would put one there.
        onChange={event => note.onChange({
          enabled: note.enabled && Boolean(event.target.value.trim()),
          message: event.target.value,
        })}
        className={`mt-1 w-full rounded-md border border-background-300 px-2 py-1.5 text-foreground-800 outline-none focus:border-amber-400 ${textSize}`}
      />
      <label className={`mt-1.5 flex items-start gap-1.5 font-semibold ${written ? 'text-foreground-700' : 'text-foreground-400'}`}>
        <input
          type="checkbox"
          checked={published}
          disabled={!written}
          onChange={event => note.onChange({ enabled: event.target.checked, message })}
          className="mt-px"
        />
        <span>Show this note to learners in this week</span>
      </label>
      {!written && (
        <p className="mt-1 font-medium text-foreground-400">Write the note first, then choose whether learners see it.</p>
      )}
      <div className="mt-1.5 flex justify-end">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-md border border-amber-300 bg-amber-50 px-2 py-px font-bold text-amber-800 transition-smooth hover:bg-amber-100"
        >
          Done
        </button>
      </div>
    </div>
  );
}

/**
 * The compact session timeline: sessions grouped by the month they land in.
 *
 * Every session is an ordinary row on its own date. A holiday falling on one
 * adds a WARNING under that row and changes nothing else -- no red "blocked"
 * card, no green "replacement", no second date, nothing skipped. What to do
 * about the clash is the author's call, made on the week itself.
 */
export function CompactSchedulePreview({
  occurrences,
  formatLabel = (plannedUtc, date) => formatDateLabel(plannedUtc || date),
  showDuration = true,
  showSessionNumbers = true,
}: {
  occurrences: Array<{
    session?: HolidayShiftSessionLike;
    plannedUtc: string;
    durationMinutes: number;
    shift?: SessionShift;
    /**
     * What is taught on this date: the title of the week's live-session
     * component. A date and a number say when a session runs; the name says
     * which session it is. Optional, because a caller that has not read the
     * module's weeks has no name to give — the row then reads as it always did.
     */
    name?: string;
    /** Extra read-only info for this occurrence (e.g. a Teams calendar status), rendered under the date. */
    extra?: ReactNode;
    /** Controls this one session offers — a join link, a recording to play. */
    actions?: ReactNode;
  }>;
  /** How to render an occurrence's date (and, if the caller wants, its time). Defaults to a bare date label. */
  formatLabel?: (plannedUtc: string, date: string) => string;
  /**
   * Whether each row states its own length. Off when every session runs for the
   * same number of minutes and the caller says so once above the list: repeated
   * down twenty rows, "120 min" stops being read as information.
   */
  showDuration?: boolean;
  /** Keep the number as a fallback when a caller supplies no session title. */
  showSessionNumbers?: boolean;
}) {
  // One kind of row. The parked clash rule used to emit two more -- a red
  // `blocked` card on the closed date and a green `replacement` card on the day
  // the session was pushed to:
  //   | { kind: 'blocked'; number; name; date; durationMinutes; causePhrase; replacementDate }
  //   | { kind: 'replacement'; number; name; date; plannedUtc; durationMinutes; extra?; actions? }
  type Entry = {
    kind: 'session';
    number: number;
    name: string;
    date: string;
    plannedUtc: string;
    durationMinutes: number;
    /** The holidays on this session's own date, named. A warning, nothing more. */
    holidayNote: string;
    extra?: ReactNode;
    actions?: ReactNode;
  };

  const entries: Entry[] = [];
  occurrences.forEach((item, index) => {
    const number = item.shift?.sessionNumber || index + 1;
    const name = cleanText(item.name);
    // Named off the session's own clashes, which is all a holiday produces now.
    const holidayNote = item.shift?.clashes.length ? holidayCausePhrase(item.shift) : '';
    if (!item.session) {
      if (item.plannedUtc) {
        entries.push({ kind: 'session', number, name, date: cleanText(item.plannedUtc).slice(0, 10), plannedUtc: item.plannedUtc, durationMinutes: item.durationMinutes, holidayNote, extra: item.extra, actions: item.actions });
      }
      return;
    }
    // Parked: a moved session split into a blocked row plus a replacement row.
    //   if (item.shift?.moved) {
    //     entries.push({ kind: 'blocked', number, name, date: item.shift.originalDate, durationMinutes: item.durationMinutes, causePhrase: holidayCausePhrase(item.shift), replacementDate: item.shift.actualDate });
    //     entries.push({ kind: 'replacement', number, name, date: item.shift.actualDate, plannedUtc: item.plannedUtc, durationMinutes: item.durationMinutes, extra: item.extra, actions: item.actions });
    //     return;
    //   }
    entries.push({ kind: 'session', number, name, date: cleanText(item.session.date), plannedUtc: item.plannedUtc, durationMinutes: item.durationMinutes, holidayNote, extra: item.extra, actions: item.actions });
  });

  const monthGroups: Array<{ key: string; label: string; entries: Entry[] }> = [];
  entries.forEach(entry => {
    const key = monthKeyOf(entry.date);
    const current = monthGroups[monthGroups.length - 1];
    if (current && current.key === key) {
      current.entries.push(entry);
      return;
    }
    monthGroups.push({ key, label: monthLabelOf(key), entries: [entry] });
  });

  return (
    <div className="divide-y divide-background-200">
      {monthGroups.map(group => {
        const delivered = group.entries.length;
        // Every session in the month is delivered -- a holiday skips nothing.
        // Parked with the clash rule:
        //   const delivered = group.entries.filter(entry => entry.kind !== 'blocked').length;
        //   const skipped = group.entries.filter(entry => entry.kind === 'blocked').length;
        //   {skipped > 0 && <p className="...text-red-600">{skipped} skipped</p>}
        return (
          <div key={group.key || 'unscheduled'} className="flex flex-col gap-3 px-3 py-4 sm:flex-row">
            <div className="w-full shrink-0 sm:w-32">
              <p className="text-[13px] font-heading font-bold text-foreground-900">{group.label || 'Not scheduled yet'}</p>
              <p className="mt-1 text-[11px] font-semibold uppercase tracking-wider text-foreground-400">
                {delivered} delivered
              </p>
            </div>
            <div className="min-w-0 flex-1 space-y-2">
              {group.entries.map((entry, index) => {
                // Parked: the red "Shifted to replacement" card emitted for a
                // `blocked` entry, and the green "Replacement delivered"
                // styling on the row that followed it.
                //   if (entry.kind === 'blocked') { ...red card, "Blocked by X;
                //     replacement scheduled on Y."... }
                //   const isReplacement = entry.kind === 'replacement';
                return (
                  <div
                    key={`${entry.kind}-${index}`}
                    className={`overflow-hidden rounded-lg border text-[12px] ${
                      entry.holidayNote ? 'border-amber-200 bg-amber-50/40' : 'border-background-200 bg-background-0'
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-bold text-foreground-900">
                          {formatLabel(entry.plannedUtc, entry.date)}
                        </span>
                        {/* The weekday is the part of a date a reader acts on:
                            a session is moved by changing a delivery day. */}
                        <span className="text-foreground-400">{weekdayLabel(entry.date)}</span>
                        {/* Numbered so a note talking in session numbers stays
                            actionable: session 8 can be found in the list. */}
                        {(showSessionNumbers || !entry.name) && (
                          <span className="text-[11px] font-bold text-foreground-400">Session {entry.number}</span>
                        )}
                        {/* What is taught on the date, next to when it runs. */}
                        {entry.name && (
                          <span className="font-semibold text-foreground-700">
                            {entry.name}
                          </span>
                        )}
                        {showDuration && <span className="text-foreground-400">{entry.durationMinutes} min</span>}
                      </span>
                      {entry.actions && (
                        <span className="flex flex-wrap items-center gap-2">
                          {entry.actions}
                        </span>
                      )}
                    </div>
                    {/* The whole of what a holiday does to a session: says so.
                        The date, the session and everything below it stand. */}
                    {entry.holidayNote && (
                      <div className="flex items-center gap-1.5 border-t border-amber-200 bg-amber-50/70 px-3 py-1.5 text-[11px] font-semibold text-amber-900">
                        <AppIcon className="ri-error-warning-line shrink-0 text-sm text-amber-600"></AppIcon>
                        <span>Heads up: this session falls on a holiday ({entry.holidayNote}). It runs as planned.</span>
                      </div>
                    )}
                    {entry.extra && (
                      <div className="border-t border-background-200 bg-background-50 px-3 py-1.5 text-[11px] font-medium text-foreground-600">
                        {entry.extra}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
