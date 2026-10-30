import { useEffect, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { formatDateLabel } from '../shared/entities/model';
import type { LiveSessionScopeRow } from './useTeamsMeetingsWorkspace';

// ============================================================================
// "Which calendar runs each week" -- one live session, one calendar.
//
// A week delivered by both the module's own series and an additional meeting is
// the failure this prevents: the series books the same date, and its next
// Update writes its link over the additional meeting's. So every live session
// nobody has booked yet is put to the author here, and the answer is final for
// both doors: a week given to the module calendar is refused an additional
// meeting, and one given to an additional meeting is left out of every date
// the module calendar sends.
//
// Only unbooked live sessions are listed. A booking is changed by cancelling
// it -- on the calendar that holds it -- never by a toggle here.
//
// The panel has two faces, because it has two jobs. While a week is on neither
// calendar it is a question nobody may walk past: an amber card, open, saying
// in the header itself -- which stays visible when the card is shut -- that the
// week is not being sent to Teams. The moment every week has an answer it stops
// asking and shuts itself: a settled choice is a record to look up, not a row
// of controls left open in front of the dates it has already decided. It
// reopens on a click, and on the next week that arrives unrouted.
// ============================================================================

const CHOICES = [
  { scope: 'main' as const, label: 'Module calendar', icon: 'ri-calendar-schedule-line' },
  { scope: 'additional' as const, label: 'Additional meeting', icon: 'ri-calendar-2-line' },
];

export function MeetingScopePanel({ rows, busy, disabled, onChoose, externalCalendar = false }: {
  rows: LiveSessionScopeRow[];
  /** The workspace's busy key; a choice in flight reads `<module>:scope:<component>:<scope>`. */
  busy: string;
  disabled: boolean;
  onChoose: (componentId: string, scope: 'main' | 'additional') => void;
  externalCalendar?: boolean;
}) {
  const open = rows.filter(row => row.open);
  const pending = open.filter(row => row.scope === 'pending');
  const asking = pending.length > 0;
  // Every answer currently on screen. The panel watches this rather than the
  // pending count alone: answering the last of three weeks and re-answering a
  // week that was already settled both change it, and both are "a choice was
  // just made" -- the one event that shuts the panel. It also changes when
  // another module is selected, which re-asks or re-settles accordingly.
  const answers = open.map(row => `${row.componentId}:${row.scope}`).join('|');
  const [expanded, setExpanded] = useState(asking);
  const lastAnswers = useRef(answers);
  useEffect(() => {
    if (lastAnswers.current === answers) return;
    lastAnswers.current = answers;
    setExpanded(asking);
  }, [answers, asking]);
  if (!open.length) return null;
  return (
    <details
      open={expanded}
      onToggle={event => setExpanded(event.currentTarget.open)}
      className={`mb-4 rounded-xl px-4 py-3 transition-smooth ${
        asking
          ? 'border-2 border-amber-400 bg-amber-50 shadow-sm ring-4 ring-amber-200/50'
          : 'border border-background-200 bg-background-50'
      }`}
    >
      <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
        <span className="flex flex-wrap items-center gap-2">
          {asking && <AppIcon className="ri-error-warning-fill shrink-0 text-base text-amber-600"></AppIcon>}
          <span className={`text-[11px] font-bold uppercase tracking-wider ${asking ? 'text-amber-900' : 'text-foreground-500'}`}>
            {asking ? 'Choose which calendar runs each week' : 'Which calendar runs each week'}
          </span>
          {asking ? (
            <span className="inline-flex items-center rounded-full bg-amber-600 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">
              {pending.length === 1 ? '1 to choose' : `${pending.length} to choose`}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
              <AppIcon className="ri-check-line text-[11px]"></AppIcon>
              Every week has a calendar choice
            </span>
          )}
          <span className={`ml-auto inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider ${asking ? 'text-amber-700' : 'text-foreground-400'}`}>
            {expanded ? 'Hide' : asking ? 'Choose' : 'Change'}
            <AppIcon className={`text-xs ${expanded ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></AppIcon>
          </span>
        </span>
        {/* Inside the summary, so it is read whether the card is open or shut:
            a week on neither calendar is not reaching Teams, and the dates
            below are not the whole module until it does. */}
        {asking && (
          <span className="mt-1.5 block text-[11px] font-semibold leading-relaxed text-amber-900">
            {pending.length === 1
              ? 'One week was added after this calendar was created and is not on either calendar yet. Until you choose, it is not sent to Teams.'
              : `${pending.length} weeks were added after this calendar was created and are not on either calendar yet. Until you choose, they are not sent to Teams.`}
          </span>
        )}
      </summary>
      <p className={`mt-2 text-[11px] font-semibold leading-relaxed ${asking ? 'text-amber-900/80' : 'text-foreground-500'}`}>
        Each week&rsquo;s live session runs on one calendar only. A week on the module calendar cannot take an
        additional meeting, and a week given to an additional meeting is left out of the dates sent to Teams.
        Choosing the module calendar inherits its meeting link and matches existing imported sessions by date.
        {externalCalendar
          ? ' Dates without a match must be added to the original series by its organiser in Teams. No new main meeting is created here.'
          : ' Dates without a matching session still need a calendar update.'}
        {' '}Nothing is sent to Microsoft here.
      </p>
      <ul className={`mt-3 ${asking ? 'space-y-1.5' : 'divide-y divide-background-200'}`}>
        {open.map(row => {
          const unchosen = row.scope === 'pending';
          return (
            <li
              key={row.componentId}
              className={`flex flex-wrap items-center justify-between gap-2 py-2 ${
                unchosen ? 'rounded-lg border border-amber-300 bg-white px-2.5' : ''
              }`}
            >
              <div className="min-w-0">
                <p className="truncate text-[12px] font-bold text-foreground-700">
                  Week {row.weekNumber} &mdash; {row.title}
                </p>
                <p className="text-[11px] font-semibold text-foreground-500">
                  {row.date ? formatDateLabel(row.date) : 'No date yet'}
                  {unchosen && <span className="ml-1.5 font-bold text-amber-700">&middot; Not chosen yet</span>}
                </p>
              </div>
              <div role="group" aria-label={`Calendar for week ${row.weekNumber}`} className="flex gap-1">
                {CHOICES.map(choice => {
                  const chosen = row.scope === choice.scope;
                  const saving = busy.endsWith(`:scope:${row.componentId}:${choice.scope}`);
                  return (
                    <button
                      key={choice.scope}
                      type="button"
                      aria-pressed={chosen}
                      disabled={disabled || Boolean(busy) || chosen}
                      onClick={() => onChoose(row.componentId, choice.scope)}
                      className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-bold transition-smooth disabled:cursor-not-allowed ${
                        chosen
                          ? 'border-primary-600 bg-primary-600 text-white'
                          : unchosen
                            ? 'border-amber-400 bg-white text-amber-900 hover:bg-amber-100 disabled:opacity-50'
                            : 'border-background-200 bg-white text-foreground-600 hover:bg-background-100 disabled:opacity-50'
                      }`}
                    >
                      <AppIcon className={`${saving ? 'ri-loader-4-line animate-spin' : choice.icon} text-sm`}></AppIcon>
                      {choice.label}
                    </button>
                  );
                })}
              </div>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
