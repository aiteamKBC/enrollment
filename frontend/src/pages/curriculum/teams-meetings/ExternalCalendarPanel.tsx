import { AppIcon } from '@/components/feature/AppIcon';
import { formatDateLabel } from '../shared/entities/model';
import type { ExternalCalendar } from './externalCalendar';
import type { MeetingRow } from './useTeamsMeetingsWorkspace';
import { ImportedParticipantsPanel } from './ImportedParticipantsPanel';

export function ExternalCalendarPanel({ calendar, row, onReattach, busy, disabled }: {
  calendar: ExternalCalendar;
  row: MeetingRow;
  onReattach: () => void;
  busy: boolean;
  disabled: boolean;
}) {
  const savedStarts = calendar.canReattach ? row.summary?.occurrenceDates || [] : [];
  const matched = (start: string) => {
    const instant = Date.parse(start);
    return Number.isFinite(instant)
      && row.plannedStarts.filter(planned => Date.parse(planned) === instant).length === 1
      && savedStarts.filter(saved => Date.parse(saved) === instant).length === 1;
  };
  const pending = row.plannedStarts.filter(start => !matched(start)).length;
  return (
    <section aria-label="Existing Teams link management" className="space-y-4">
      <div className="rounded-xl border border-primary-200 bg-primary-50 p-4">
        <p className="flex items-center gap-2 text-sm font-bold text-primary-900">
          <AppIcon className="ri-link-m text-lg" />
          {calendar.source === 'imported' ? 'Series linked from Teams' : 'Existing Teams link protected'}
        </p>
        <p className="mt-2 text-[12px] leading-5 text-primary-900">
          This module uses a meeting link added from Teams. Pasting the link changed where learners join;
          it did not update the original calendar or invitations. Creating a new module meeting is blocked
          so it cannot replace this link.
        </p>
        <p className="mt-2 text-[12px] leading-5 text-primary-900">
          Add sessions or change dates, attendees and presenters directly in Teams using the original
          meeting organiser&rsquo;s account.
        </p>
        <div className="mt-3 space-y-2">
          {calendar.joinUrls.map((link, index) => (
            <div key={link} className="rounded-lg border border-primary-200 bg-white p-3">
              <p className="text-[11px] font-bold text-foreground-600">{calendar.joinUrls.length > 1 ? `Saved join link ${index + 1}` : 'Saved join link'}</p>
              <p className="mt-1 break-all text-[11px] text-foreground-500">{link}</p>
              {/^(https?):\/\//i.test(link) && (
                <a href={link} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-[12px] font-bold text-primary-700 underline">
                  Open existing meeting <AppIcon className="ri-external-link-line" />
                </a>
              )}
            </div>
          ))}
        </div>
      </div>

      {calendar.canReattach && row.summary && (
        <ImportedParticipantsPanel liveSessionId={row.summary.liveSessionId} savedOrganizer={row.summary.organizerEmail} />
      )}

      <div className="rounded-xl border border-background-200 bg-background-50 p-4 text-[12px] leading-5 text-foreground-600">
        <p className="font-bold text-foreground-800">When a week is inserted</p>
        <p className="mt-1">
          Choose Module calendar to reuse the existing main link. The LMS matches each week to the saved
          Teams session on its planned date; moving weeks does not move the original Teams calendar.
          A date without a matching session stays awaiting confirmation. The organiser must add any missing
          session to the existing series in Teams. Additional meetings keep their own links and are excluded from this calendar.
        </p>
      </div>

      {!calendar.canReattach && (
        <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] leading-5 text-amber-900">
          {calendar.source === 'replacement' && row.summary
            ? 'The pasted link differs from the saved module calendar. Its old dates cannot confirm this link. An administrator needs to connect the original meeting before its calendar can be matched.'
            : 'The original calendar has not been connected to these links yet. The links are preserved, but their dates are not confirmed. An administrator needs to connect the existing meeting; do not create a replacement.'}
        </p>
      )}

      <div className="overflow-hidden rounded-xl border border-background-200">
        <div className="border-b border-background-200 bg-background-50 p-3">
          <p className="text-[12px] font-bold text-foreground-800">Module plan and saved Teams dates</p>
          <p className="mt-1 text-[11px] text-foreground-500">
            {row.summary?.timeZone ? `Times: ${row.summary.timeZone}. ` : ''}
            This comparison uses the last saved calendar data. It does not confirm a new date in Microsoft.
          </p>
        </div>
        <ul className="divide-y divide-background-200">
          {row.sessions.map((session, index) => {
            const confirmed = matched(row.plannedStarts[index] || '');
            return (
              <li key={session.componentId || `${session.id}:${index}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-[12px]">
                <div>
                  <p className="font-semibold text-foreground-800">{session.componentTitle || session.title || `Session ${index + 1}`}</p>
                  <p className="text-foreground-500">{formatDateLabel(session.date)}{session.startTime ? ` · ${session.startTime}` : ''}</p>
                </div>
                <span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${confirmed ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-900'}`}>
                  {confirmed ? 'Matches saved Teams date' : 'Awaiting calendar confirmation'}
                </span>
              </li>
            );
          })}
        </ul>
        {!row.sessions.length && <p className="p-3 text-[12px] text-foreground-500">No main-calendar sessions have been planned yet.</p>}
      </div>
      {pending > 0 && (
        <div role="status" className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[12px] leading-5 text-amber-900">
          <p className="font-semibold">{pending} planned session{pending === 1 ? '' : 's'} still need{pending === 1 ? 's' : ''} calendar confirmation.</p>
          <p>
            This module uses an existing Teams series. Open Teams using the original meeting organiser&rsquo;s
            account, edit that series to include any missing planned sessions on the dates shown above,
            and send the update to the invitees. Keep the existing meeting link. Adding a session in the LMS
            does not add it to the Teams calendar.
          </p>
        </div>
      )}

      {calendar.canReattach && (
        <div className="rounded-xl border border-background-200 p-4">
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={onReattach} disabled={disabled || busy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-[12px] font-bold text-white disabled:opacity-50">
              <AppIcon className={busy ? 'ri-loader-4-line animate-spin' : 'ri-link-m'} />
              Reattach saved Teams data
            </button>
          </div>
          <p className="mt-2 text-[11px] leading-5 text-foreground-500">
            Reattach uses already saved dates to repair the LMS links. It does not create meetings, extend the
            series, or import newly added dates.
          </p>
        </div>
      )}
    </section>
  );
}
