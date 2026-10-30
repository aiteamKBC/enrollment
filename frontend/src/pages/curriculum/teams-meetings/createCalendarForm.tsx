import { DEFAULT_START_TIME, DEFAULT_DURATION_MINUTES, minuteKey, sessionNaiveLocal, minutesBetween, naiveLocalFromUtc, calendarLabel, teamsGapNote, overrideDurationMinutes, emptyTeamsCalendarForm, teamsCalendarOccurrences, buildTeamsCalendarInput, teamsUpdateChanges, TEAMS_LOBBY_OPTIONS, TEAMS_RECORDING_OPTIONS, TEAMS_LANGUAGE_OPTIONS, type TeamsPlannedSession, type TeamsSchedulePreviewRow, type GroupDeliveryPattern, type TeamsCalendarForm, type TeamsCalendarTarget, type TeamsUpdateFields } from './createCalendarForm.helpers';
export type { TeamsPlannedSession, TeamsSchedulePreviewRow, GroupDeliveryPattern, TeamsCalendarForm, TeamsCalendarTarget } from './createCalendarForm.helpers';
import type { ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import {
  formatCalendarDateTime,
  getCalendarTimeZone,
  parseUtcInstant,
  utcIsoToCalendarParts,
  zonedNaiveToUtcIso,
  type TeamsMeetingInput,
} from '../module-builder/moduleAuthoringData';
import { emailList } from "../module-builder/EmailChipsInput.helpers";
import { EntraPeopleInput } from './EntraPeopleInput';
import { cleanText, formatDateLabel } from '../shared/entities/model';
import { CompactSchedulePreview, type HolidayShiftPlan } from '../shared/entities/sessionShiftPreview';
import { buildHolidayShiftPlan } from "../shared/entities/sessionShiftPreview.helpers";
import { FormField, SelectControl, TextAreaControl, TextControl } from '../shared/entities/ui';
import { calendarInputError, normalizedClock, clockLabel, durationLabel, pairHeldDates } from './calendarTime';

/**
 * The holidays inside this module's dates, named above the dates themselves.
 *
 * A warning and nothing else. The cascade it used to describe -- a session
 * landing on a closed date moving to the next delivery day and dragging the
 * whole tail of the run with it -- is parked: no session moves, none is
 * dropped, and the module still ends where its own delivery pattern says it
 * ends. What to do about these dates is the author's call, made on the week.
 *
 * Parked with that rule, alongside the cascade paragraph itself:
 *   const endMoved = Boolean(plan.originalEndDate) && Boolean(plan.shiftedEndDate)
 *     && plan.originalEndDate !== plan.shiftedEndDate;
 *   {Boolean(plan.movedCount) && (
 *     <p>{plan.movedRangeLabel} {plan.movedCount === 1 ? 'runs' : 'run'} later
 *       {endMoved && <>, so the module now ends {plan.shiftedEndDate} instead of
 *       {plan.originalEndDate}</>}.</p>
 *   )}
 */
function HolidayWarningNote({ plan }: { plan: HolidayShiftPlan }) {
  if (!plan.closures.length) return null;
  const names = Array.from(new Set(plan.closures.map(closure => closure.label).filter(Boolean)));
  // One holiday named twice reads as two, so the dates only carry their own
  // label when more than one holiday falls on them.
  const closureList = plan.closures
    .map(closure => (names.length > 1 && closure.label
      ? `${formatDateLabel(closure.date)} (${closure.label})`
      : formatDateLabel(closure.date)))
    .join(', ');
  return (
    <div className="flex items-start gap-2.5 border-b border-amber-200 bg-amber-50/70 px-3 py-2.5">
      <AppIcon className="ri-error-warning-line mt-0.5 shrink-0 text-sm text-amber-700"></AppIcon>
      <div className="space-y-1 text-[11px] leading-relaxed text-amber-900">
        <p>
          <span className="font-bold">Heads up: </span>
          <span className="font-bold">{names.length ? names.join(', ') : 'A cohort holiday'}</span>
          {' falls on '}
          <span className="font-semibold">{closureList}</span>
          {' inside this module’s dates.'}
        </p>
        <p>
          Nothing is changed by that. Every session keeps its own date, none is moved or dropped, and
          the module ends where its delivery pattern says it ends — whether these sessions run is
          yours to decide on the week itself.
        </p>
      </div>
    </div>
  );
}

/**
 * Where the session length on these rows came from.
 *
 * The group owns the weekly slot, so it owns the length: a group that meets
 * Friday 12:00 PM to 2:00 PM delivers two-hour sessions, and a module under it
 * inherits that unless one of its own sessions was saved with a different clock.
 * This says the group's slot out loud and counts the sessions that disagree with
 * it, because a list of 60s with a single 120 in the middle is otherwise
 * unexplainable from this screen. It reports; it never rewrites a stored time.
 */
function GroupPatternNote({ pattern, lengths, overrideDuration }: {
  pattern?: GroupDeliveryPattern;
  lengths: number[];
  overrideDuration: number;
}) {
  if (!pattern) return null;
  const slot = pattern.startTime && pattern.endTime
    ? `${clockLabel(pattern.startTime)} - ${clockLabel(pattern.endTime)}`
    : '';
  const differing = pattern.durationMinutes && !overrideDuration
    ? lengths.filter(value => value !== pattern.durationMinutes).length
    : 0;
  return (
    <div className="border-b border-background-200 bg-background-100/40 px-3 py-2.5 text-[11px] leading-relaxed text-foreground-600">
      <p>
        <span className="font-bold text-foreground-700">{pattern.name}</span>
        {pattern.days ? ` delivers ${pattern.days}` : ' has no delivery day set'}
        {slot ? `, ${slot}` : ', and no session times'}
        {pattern.durationMinutes ? ` — ${durationLabel(pattern.durationMinutes)} a session.` : '.'}
      </p>
      {Boolean(differing) && (
        <p className="mt-1 font-semibold text-amber-700">
          {differing === 1 ? '1 session below keeps' : `${differing} sessions below keep`} a different length.
          Each is booked at its own stored time — correct those on the module, not here.
        </p>
      )}
      {Boolean(overrideDuration) && (
        <p className="mt-1 font-semibold text-amber-700">
          Duration is set to {durationLabel(overrideDuration)}, so every session below is booked at that
          length whatever the module stored.
        </p>
      )}
    </div>
  );
}

export function ModuleSessionSchedulePreview({
  row,
  title = 'Module session plan',
  calendarDates,
  holidayLabelFor,
  renderActions,
  renderFacts,
  overrideDuration = 0,
  showAlternateTimeZones = true,
}: {
  row: TeamsSchedulePreviewRow;
  title?: string;
  calendarDates?: string[];
  holidayLabelFor?: (date: string) => string;
  /**
   * The way into one meeting, shown on the session's own row. `teamsUtc` is the
   * start of the Teams session paired with this row by date, '' when Teams holds
   * none for it -- the meeting a row's buttons act on, never its neighbour's.
   */
  renderActions?: (index: number, durationMinutes: number, teamsUtc: string) => ReactNode;
  /** What one meeting left behind — attendance, transcript, recording. */
  renderFacts?: (index: number, durationMinutes: number, teamsUtc: string) => ReactNode;
  /**
   * One length for every session, when the form is overriding them.
   *
   * The preview is a promise about what will be booked, so an override has to
   * be visible on the rows it changes. Without it the list went on showing each
   * session's stored length while Create sent a different one.
   */
  overrideDuration?: number;
  showAlternateTimeZones?: boolean;
}) {
  const plan = buildHolidayShiftPlan(row.sessions, holidayLabelFor);
  const hasCalendar = Boolean(row.summary);
  // A meeting can outlive the module's stored dates. When that happens the
  // calendar entries are still the rows worth showing, so they are listed
  // against the date Teams holds rather than dropped for an empty state.
  const sessionRows: Array<TeamsPlannedSession | undefined> = row.sessions.length
    ? row.sessions
    : row.teamsStarts.map(() => undefined);
  const held = calendarDates?.length ? calendarDates : row.teamsStarts;
  const plannedStarts = sessionRows.map((session, index) => (session
    ? row.plannedStarts[index] || zonedNaiveToUtcIso(sessionNaiveLocal(session), session.timeZone || row.timeZone)
    : ''));
  // Paired by date, so one session missing from Teams reads as that one
  // session, not as every later row "still held" a week out. Rows that are the
  // Teams dates themselves (no module sessions) are their own pairing.
  const pairing = row.sessions.length && hasCalendar
    ? pairHeldDates(plannedStarts, held, value => calendarLabel(value, row.timeZone).split(', ')[0])
    : { paired: sessionRows.map((_, index) => held[index] || ''), extra: [] as string[] };
  // One clock pair for the whole list, taken from the first session that has
  // one. Repeated under every row it was read as decoration rather than as a
  // fact, and it crowded out the things that really are per session. Stated
  // once, a row only has to speak up where a clock change actually moves it --
  // which it does: a series running through late October is 09:00 Egypt at
  // both ends, but 07:00 in England before the change and 06:00 after it.
  const countryPair = (iso: string) => {
    const egypt = utcIsoToCalendarParts(iso, 'Africa/Cairo');
    const england = utcIsoToCalendarParts(iso, 'Europe/London');
    return egypt.time && england.time
      ? `Egypt: ${clockLabel(egypt.time)} · England: ${clockLabel(england.time)}${england.date < egypt.date ? ' (previous day)' : ''}`
      : '';
  };
  const usualCountryPair = sessionRows
    .map((_session, index) => countryPair(plannedStarts[index] || pairing.paired[index] || ''))
    .find(Boolean) || '';
  const occurrences = sessionRows.map((session, index) => {
    const teamsUtc = pairing.paired[index] || '';
    const plannedUtc = plannedStarts[index];
    const durationMinutes = overrideDuration || (session
      ? session.durationMinutes || Math.max(15, minutesBetween(session.startTime, session.endTime) || row.durationMinutes)
      : row.durationMinutes);
    const gap = teamsGapNote(plannedUtc, teamsUtc, hasCalendar);
    const facts = renderFacts?.(index, durationMinutes, teamsUtc);
    const rowCountryPair = showAlternateTimeZones ? countryPair(plannedUtc || teamsUtc) : '';
    const driftedCountryPair = rowCountryPair && rowCountryPair !== usualCountryPair ? rowCountryPair : '';
    return {
      session,
      name: session?.componentTitle,
      plannedUtc: plannedUtc || teamsUtc,
      teamsUtc,
      durationMinutes,
      shift: plan.shifts[index] && { ...plan.shifts[index], sessionNumber: session?.sessionNumber ?? plan.shifts[index].sessionNumber },
      matches: gap.matches,
      actions: renderActions?.(index, durationMinutes, teamsUtc),
      extra: (driftedCountryPair || !session || facts || gap.note) ? (
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {driftedCountryPair && <span className="font-semibold">{driftedCountryPair}</span>}
          {!session && <span className="font-semibold text-amber-700">Held on Teams, but the module has no session on this date.</span>}
          {facts}
          {gap.note && <span className="font-semibold text-amber-700">{gap.note}</span>}
        </span>
      ) : undefined,
    };
  });
  // Stated once when they all agree, and per row when they do not: a length
  // repeated down twenty rows is read as decoration, not as a fact.
  const durations = Array.from(new Set(occurrences.map(item => item.durationMinutes)));
  const uniformDuration = durations.length === 1 ? durations[0] : 0;
  // Stated once for the whole list. A session a clock change moves off it says
  // so on its own row; the rest stay quiet.
  const countryTimes = usualCountryPair ? [usualCountryPair] : [];

  return (
    <div className="overflow-hidden rounded-xl border border-background-200 bg-background-50">
      {calendarInputError(row) && <p role="alert" className="p-3 text-sm text-red-700">{calendarInputError(row)}</p>}
      <div className="flex flex-col gap-2 border-b border-background-200 bg-background-100/50 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wider text-primary-700">{title}</p>
          <p className="mt-0.5 text-[12px] font-semibold text-foreground-900">
            {row.name}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide">
          <span className="rounded-full border border-background-200 bg-background-50 px-2.5 py-1 text-foreground-600">
            {occurrences.length} session{occurrences.length === 1 ? '' : 's'}
          </span>
          {Boolean(uniformDuration) && (
            <span className="rounded-full border border-background-200 bg-background-50 px-2.5 py-1 text-foreground-600">
              {uniformDuration} min each
            </span>
          )}
          {countryTimes.length > 0 && (
            <>
              <span role="note" aria-label="Session start times in Egypt and England"
                className="rounded-full border border-background-200 bg-background-50 px-2.5 py-1 normal-case text-foreground-600">
                {countryTimes.map(label => <span key={label}>{label}</span>)}
              </span>
              <span className="basis-full text-[10px] font-normal normal-case text-foreground-500">
                England changes between BST and GMT during the year, so the difference from Egypt can be 2 or 3 hours depending on the session date.
              </span>
            </>
          )}
          {/* Counts the dates a holiday lands on, not dates a holiday moved:
              nothing moves. Parked with the clash rule:
                {plan.movedCount ? `${plan.movedCount} moved by holidays` : 'No holiday shifts'} */}
          <span className={`rounded-full border px-2.5 py-1 ${plan.closures.length ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
            {plan.closures.length
              ? `${plan.closures.length} session${plan.closures.length === 1 ? '' : 's'} on a holiday`
              : 'No holiday clashes'}
          </span>
        </div>
      </div>
      <GroupPatternNote
        pattern={row.groupPattern}
        lengths={occurrences.map(item => item.durationMinutes)}
        overrideDuration={overrideDuration}
      />
      <HolidayWarningNote plan={plan} />
      {occurrences.length ? (
        <>
          <CompactSchedulePreview
            occurrences={occurrences}
            showDuration={!uniformDuration}
            showSessionNumbers={false}
            formatLabel={(plannedUtc, date) => calendarLabel(plannedUtc || date, row.timeZone)}
          />
          {pairing.extra.length > 0 && (
            <p className="border-t border-background-200 px-3 py-2 text-[11px] font-semibold text-amber-700">
              Teams also holds {pairing.extra.map(value => calendarLabel(value, row.timeZone)).join('; ')}, which
              {' '}{pairing.extra.length === 1 ? 'is not a session' : 'are not sessions'} in this module — sending removes
              {' '}{pairing.extra.length === 1 ? 'it' : 'them'}.
            </p>
          )}
          {/* A note about a pending change is only useful next to the thing
              that makes it happen. */}
          {(occurrences.some(item => !item.matches) || pairing.extra.length > 0) && (
            <p className="border-t border-background-200 px-3 py-2 text-[11px] font-semibold text-foreground-500">
              Nothing on the Teams calendar changes until you press
              {' '}
              <span className="font-bold text-foreground-700">Update Teams calendar</span>.
            </p>
          )}
        </>
      ) : (
        <div className="border-t border-amber-200 bg-amber-50/70 px-3 py-3.5">
          {/* Names the missing work rather than implying a saved schedule is
              all that is needed: a meeting exists per live-session component,
              so a module with none has nothing to put on a calendar. */}
          <p className="flex items-center gap-1.5 text-[12px] font-bold text-amber-900">
            <AppIcon className="ri-error-warning-line shrink-0 text-sm text-amber-600"></AppIcon>
            This module has no dated live sessions
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-amber-900">
            One live-session component in the Course structure is one meeting, on that component&rsquo;s own
            date. Add them in the Module Builder, or give the ones it already has a date, and save.
          </p>
        </div>
      )}
    </div>
  );
}

const DURATION_OPTIONS = [30, 45, 60, 90, 120, 180];

/**
 * The standard lengths plus whatever the group was actually created with, so a
 * non-standard session duration still shows its own value rather than blank.
 */
function durationOptions(picked: string, ...groupMinutes: number[]) {
  const minutes = Array.from(new Set([...DURATION_OPTIONS, ...groupMinutes, Number(picked) || 0]))
    .filter(value => value > 0)
    .sort((left, right) => left - right);
  return minutes.map(value => {
    const hours = Math.floor(value / 60);
    const rest = value % 60;
    const parts = [];
    if (hours) parts.push(`${hours} hour${hours === 1 ? '' : 's'}`);
    if (rest || !hours) parts.push(`${rest} minute${rest === 1 ? '' : 's'}`);
    return { value: String(value), label: parts.join(' ') };
  });
}

/**
 * Every choice the Duration field offers, leading with "leave them alone".
 *
 * A list that omits the empty option is a one-way door: the form opens with no
 * override, the select shows its first length instead, and the reader cannot
 * get back to "each session keeps its own" once they have touched it. That is
 * what the Teams Meetings page's own copy of this field had become.
 */
function durationSelectOptions(form: TeamsCalendarForm, row: TeamsSchedulePreviewRow) {
  return [
    { value: '', label: 'Use scheduled duration for each session' },
    ...durationOptions(form.durationMinutes, row.durationMinutes, row.groupPattern?.durationMinutes || 0),
  ];
}

/** Where the offered default comes from, and what choosing one does. */
function durationFieldHint(row: TeamsSchedulePreviewRow, firstPlannedUtc: string): string {
  return [
    row.groupPattern?.durationMinutes
      ? `${row.groupPattern.name} runs ${durationLabel(row.groupPattern.durationMinutes)} sessions.`
      : '',
    firstPlannedUtc ? `First session ${calendarLabel(firstPlannedUtc, row.timeZone)}.` : '',
    'Choosing a length here books every session above at it.',
  ].filter(Boolean).join(' ');
}

type TeamsFieldProps = {
  form: Pick<TeamsCalendarForm, 'lobbyBypass' | 'recording' | 'spokenLanguage' | 'presenters' | 'coOrganizers' | 'attendees'>;
  patch: (value: Partial<TeamsCalendarForm>) => void;
};

/**
 * Lobby, recording and language: grid cells of a two-column form. Create and
 * Update render these same fields, so a calendar is updated with exactly the
 * choices it could have been created with.
 */
export function TeamsMeetingOptionFields({ form, patch }: TeamsFieldProps) {
  return (
    <>
      <FormField label="Who can bypass the lobby?">
        <SelectControl
          value={form.lobbyBypass}
          onChange={value => patch({ lobbyBypass: value })}
          options={TEAMS_LOBBY_OPTIONS}
        />
      </FormField>
      <FormField label="Recording">
        <SelectControl
          value={form.recording}
          onChange={value => patch({ recording: value })}
          options={TEAMS_RECORDING_OPTIONS}
        />
      </FormField>
      <FormField label="Spoken language">
        <SelectControl
          value={form.spokenLanguage}
          onChange={value => patch({ spokenLanguage: value })}
          options={TEAMS_LANGUAGE_OPTIONS}
        />
      </FormField>
    </>
  );
}

/** Presenters, co-organisers and attendees, with the optional roster prefill; grid cells, as above. */
export function TeamsPeopleFields({ form, patch, prefilling, prefillNotice, onPrefill }: TeamsFieldProps & {
  prefilling?: boolean;
  prefillNotice?: { tone: 'ok' | 'error'; text: string } | null;
  onPrefill?: () => void;
}) {
  return (
    <>
      {onPrefill && (
        <div className="sm:col-span-2 -mb-2 flex flex-col items-end gap-1">
          <button
            type="button"
            disabled={prefilling}
            onClick={onPrefill}
            className="inline-flex items-center gap-1 text-[11px] font-bold text-primary-700 hover:underline disabled:opacity-50"
          >
            <AppIcon className="ri-refresh-line text-sm"></AppIcon>
            {prefilling ? 'Loading…' : 'Prefill from the learners who have this module on their plan'}
          </button>
          {/* What the prefill actually did. Without it, a request that failed
              and one that found nobody were both a button that did nothing. */}
          <p aria-live="polite" className={`text-right text-[11px] ${prefillNotice?.tone === 'error' ? 'text-red-700' : 'text-foreground-500'}`}>
            {prefillNotice?.text || ''}
          </p>
        </div>
      )}
      <FormField label="Presenters" hint="These people can share and record.">
        <EntraPeopleInput label="Presenters"
          value={form.presenters}
          onChange={value => patch({ presenters: value })}
        />
      </FormField>
      <FormField
        label="Co-organisers"
        hint="They run the meeting with the organizer: start and stop the recording, admit people from the lobby and change the meeting options. Invited automatically."
      >
        <EntraPeopleInput label="Co-organizers"
          value={form.coOrganizers}
          onChange={value => patch({ coOrganizers: value })}
        />
      </FormField>
      <FormField label="Attendees" hint="Presenters and co-organisers are invited automatically.">
        <EntraPeopleInput label="Attendees" includeGuests
          value={form.attendees}
          onChange={value => patch({ attendees: value })}
        />
      </FormField>
    </>
  );
}

/**
 * The form itself: what the calendar is built on, then what Teams needs.
 *
 * Controlled on purpose — both callers already own a form state with dirty
 * tracking and a Save button in their own dialog footer, so this renders their
 * value and reports edits rather than keeping a third copy of it.
 */
export function TeamsCalendarFormBody({
  row,
  form,
  patch,
  holidayLabelFor,
  prefilling,
  prefillNotice,
  onPrefill,
  timeZoneLabel,
  existingCalendar = false,
  showAlternateTimeZones = true,
}: {
  row: TeamsCalendarTarget;
  form: TeamsCalendarForm;
  patch: (value: Partial<TeamsCalendarForm>) => void;
  holidayLabelFor?: (date: string) => string;
  /** Loading state of the tutor/learner prefill, when the caller offers it. */
  prefilling?: boolean;
  /** What the last prefill did, shown beside its button. */
  prefillNotice?: { tone: 'ok' | 'error'; text: string } | null;
  onPrefill?: () => void;
  timeZoneLabel?: string;
  existingCalendar?: boolean;
  showAlternateTimeZones?: boolean;
}) {
  const previewRow = { ...row, timeZone: form.scheduleTimeZone || row.timeZone,
    plannedStarts: teamsCalendarOccurrences(row, form.scheduleTimeZone).map(item => item.startDateTimeUtc) };
  const firstPlanned = previewRow.plannedStarts[0] || '';
  const override = overrideDurationMinutes(form, row);
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-primary-100 bg-primary-50/60 px-4 py-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-primary-600 text-white">
          <AppIcon className="ri-microsoft-teams-line text-base"></AppIcon>
        </span>
        <p className="text-[12px] text-foreground-600">
          {existingCalendar
            // The dates below are what Update SENDS, not what Teams currently
            // holds -- they are the module's plan, and the two disagree exactly
            // when the calendar is out of date, which is when this dialog
            // matters most. Each row says for itself if Teams is holding it on
            // another day, so the banner does not have to claim a provenance
            // that would be wrong on the rare calendar whose live-session
            // components have all been deleted.
            ? 'Update sends the dates below to this existing Teams calendar and refreshes the links in the module’s live-session components. A session Teams is still holding on another day is marked on its own row.'
            : row.sessions.length
            ? `Create puts one Teams meeting on each of the ${row.sessions.length} session date${row.sessions.length === 1 ? '' : 's'} below and writes the join link into this module’s live-session components. The dates come from the module, not from this form.`
            : 'This module has no live-session components yet, so there is nothing to put on a calendar. Add a Live session to each week that should meet — every one becomes a Teams meeting on its date.'}
        </p>
      </div>

      {Boolean(row.sessions.length) && (
        <>
          <ModuleSessionSchedulePreview
            row={previewRow}
            title={existingCalendar ? 'Dates Update will send' : 'Dates the calendar will be created on'}
            holidayLabelFor={holidayLabelFor}
            overrideDuration={override}
            showAlternateTimeZones={showAlternateTimeZones}
          />

          <FormField label="Schedule time zone" hint={showAlternateTimeZones
            ? 'Group/module times are interpreted in this zone. Each date above shows the same meeting in Egypt and England, including daylight-saving changes.'
            : 'Dates and times above use this schedule time zone, including daylight-saving changes.'}>
            <SelectControl value={form.scheduleTimeZone || getCalendarTimeZone()}
              disabled={existingCalendar}
              onChange={value => patch({ scheduleTimeZone: value as TeamsCalendarForm['scheduleTimeZone'] })}
              options={[{ value: 'Africa/Cairo', label: 'Egypt (Africa/Cairo)' }, { value: 'Europe/London', label: 'England (Europe/London)' }]} />
          </FormField>
          <FormField label="Teams series and links" hint="Different start times or durations need a separate weekly series for each day. Each day's link repeats every week.">
            <SelectControl disabled={existingCalendar} value={form.seriesMode || 'auto'} onChange={value => patch({ seriesMode: value as TeamsCalendarForm['seriesMode'] })} options={[
              { value: 'auto', label: 'Automatic: share a link when times match' },
              { value: 'per_day', label: 'Separate series and link for each day' },
              ...(existingCalendar || new Set(teamsCalendarOccurrences(row).map(item => `${naiveLocalFromUtc(item.startDateTimeUtc).slice(11)}:${form.durationMinutes || item.durationMinutes}`)).size <= 1 ? [{ value: 'shared', label: 'One series and link for all days' }] : []),
            ]} />
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="Organizer Microsoft 365 email"
              required
              hint={existingCalendar ? 'This organizer owns the existing calendar.' : 'The calendar this series is created in. The selected account must allow this app to manage Teams meetings.'}
            >
              {existingCalendar ? <input aria-label="Organizer" readOnly value={form.organizerEmail}
                className="h-11 w-full rounded-lg border border-background-200 bg-background-100 px-3 text-[13px]" /> : <EntraPeopleInput single label="Organizer"
                value={form.organizerEmail}
                onChange={value => patch({ organizerEmail: value })}
              />}
            </FormField>
            <FormField label="Duration" hint={durationFieldHint(previewRow, firstPlanned)}>
              <SelectControl
                value={form.durationMinutes}
                onChange={value => patch({ durationMinutes: value })}
                options={durationSelectOptions(form, row)}
              />
            </FormField>
            <TeamsMeetingOptionFields form={form} patch={patch} />
            {existingCalendar ? <p className="self-center text-[11px] text-foreground-500">The existing invitation text is preserved. Edit its details in Outlook.</p> : <FormField label="Details" hint="Optional. Included in the calendar invitation.">
              <TextAreaControl
                value={form.details}
                onChange={value => patch({ details: value })}
                rows={2}
              />
            </FormField>}
            <TeamsPeopleFields form={form} patch={patch} prefilling={prefilling} prefillNotice={prefillNotice} onPrefill={onPrefill} />
          </div>
          {(form.scheduleTimeZone || timeZoneLabel) && (
            <p className="text-[10px] font-semibold text-foreground-400">
              <AppIcon className="ri-time-line mr-1"></AppIcon>
              Schedule time zone: {form.scheduleTimeZone === 'Africa/Cairo' ? 'Egypt (Africa/Cairo)' : form.scheduleTimeZone === 'Europe/London' ? 'England (Europe/London)' : timeZoneLabel}
            </p>
          )}
        </>
      )}
    </div>
  );
}
