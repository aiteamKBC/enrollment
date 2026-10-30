import { useEffect, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import {
  fetchImportedMeetingParticipants,
  type ImportedMeetingParticipants,
  type ImportedMeetingPerson,
} from '@/lib/curriculumApi';

const PRESENTERS: Record<string, string> = {
  everyone: 'Everyone can present',
  organization: 'People in the organiser’s organisation can present',
  organizer: 'Only the organiser can present',
  roleIsPresenter: 'Only assigned presenters can present',
};

function People({ label, people, complete }: {
  label: string;
  people?: ImportedMeetingPerson[];
  complete: boolean;
}) {
  return (
    <div>
      <p className="text-[12px] font-bold text-foreground-700">
        {label}{people && people.length > 0 ? ` (${people.length}${complete ? '' : ' visible'})` : ''}
      </p>
      {people?.length ? (
        <ul className="mt-2 max-h-52 space-y-1.5 overflow-y-auto rounded-lg border border-background-200 p-2">
          {people.map((person, index) => (
            <li key={`${person.email}:${index}`} className="break-words text-[12px] text-foreground-600">
              {person.name && <span className="font-semibold">{person.name}{person.email ? ' · ' : ''}</span>}
              {person.email}
            </li>
          ))}
        </ul>
      ) : <p className="mt-1 text-[12px] text-foreground-500">
        {people && complete ? 'None returned by Microsoft.' : 'Not available — an empty list has not been confirmed.'}
      </p>}
    </div>
  );
}

export function ImportedParticipantsPanel({ liveSessionId, savedOrganizer }: {
  liveSessionId: string;
  savedOrganizer?: string;
}) {
  const [reading, setReading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [snapshot, setSnapshot] = useState<{ id: string; data: ImportedMeetingParticipants } | null>(null);
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null);
  // The previous module's names must never flash while a new request is pending.
  const data = snapshot?.id === liveSessionId ? snapshot.data : null;
  const error = failure?.id === liveSessionId ? failure.message : '';
  useEffect(() => {
    const controller = new AbortController();
    setReading(true);
    setSnapshot(null);
    setFailure(null);
    void fetchImportedMeetingParticipants(liveSessionId, controller.signal)
      .then(result => { if (!controller.signal.aborted) setSnapshot({ id: liveSessionId, data: result }); })
      .catch(() => {
        if (!controller.signal.aborted) setFailure({ id: liveSessionId,
          message: 'The meeting’s people could not be read from Microsoft. Retry; no invitations or meeting details were changed.' });
      })
      .finally(() => { if (!controller.signal.aborted) setReading(false); });
    return () => controller.abort();
  }, [liveSessionId, refresh]);
  // The calendar invitation can name a channel mailbox. It must never replace
  // the person whose identity Microsoft associates with this online meeting.
  const organizerReport = data?.organizer;
  const organizer = organizerReport?.person || data?.roles.organizer;
  const savedEvidence = organizerReport?.source === 'saved_microsoft_records';
  const calendarReadable = data?.calendar.status === 'available' || data?.calendar.status === 'partial';
  const rolesReadable = data?.roles.status === 'available' || data?.roles.status === 'partial';
  return (
    <section aria-label="People from the existing Teams meeting" className="space-y-3 rounded-xl border border-background-200 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-bold text-foreground-800">People from the existing Teams meeting</h3>
        <button type="button" disabled={reading} onClick={() => setRefresh(value => value + 1)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 px-3 py-2 text-[12px] font-bold text-primary-700 disabled:opacity-50">
          <AppIcon className={reading ? 'ri-loader-4-line animate-spin' : 'ri-refresh-line'} />
          Refresh people from Teams
        </button>
      </div>
      <p className="text-[12px] leading-5 text-foreground-500">
        Read the existing meeting’s organiser and invitations without sending or changing anything.
        Calendar invitees are the people invited; “attended” beside a session counts people who joined
        that session. Those totals can differ, and neither is a list of all channel members.
      </p>
      {reading && <p role="status" className="text-[12px] text-foreground-500">Reading the organiser, invitations and meeting roles…</p>}
      {error && <p role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] text-amber-900">{error}</p>}
      <div aria-label="Teams meeting organiser" className="break-words rounded-lg border border-primary-200 bg-primary-50 p-3 text-[12px] text-foreground-600">
        <p className="font-bold text-foreground-800">Teams meeting organiser</p>
        <p className="mt-1 font-semibold">{organizer ? [organizer.name, organizer.email].filter(Boolean).join(' · ') : 'Not confirmed yet'}</p>
        {organizerReport && <p className="mt-2 leading-5">{organizerReport.message}</p>}
        {organizer && <p className="mt-1 text-[11px] text-foreground-500">
          {savedEvidence ? 'Source: saved Microsoft recording and attendance records.' : 'Source: Microsoft online meeting data.'}
          {savedEvidence && organizerReport?.evidenceAt ? ` Evidence dated ${new Date(organizerReport.evidenceAt).toLocaleString()}.` : ''}
        </p>}
        {!organizerReport && data && !organizer && <p className="mt-1 text-[11px] text-foreground-500">
          The online meeting organiser could not be verified. The calendar invitation address below is a separate identity.
        </p>}
      </div>
      {data && <>
        <p className="text-[11px] text-foreground-500">Last read: {new Date(data.checkedAt).toLocaleString()}</p>
        <div className="space-y-2 border-t border-background-200 pt-3">
          <div className="break-words text-[12px] text-foreground-600">
            <p className="font-bold text-foreground-700">Calendar invitation organiser</p>
            <p className="mt-1">{data.calendar.organizer
              ? [data.calendar.organizer.name, data.calendar.organizer.email].filter(Boolean).join(' · ')
              : savedOrganizer ? `${savedOrganizer} (saved address; not verified by this read)` : 'Not available'}</p>
          </div>
          {data.calendar.source === 'attendee_calendar' && <p className="text-[12px] text-foreground-600">
            Source: an attendee’s calendar copy. These are the invitees visible in that copy; this does not grant access to edit the organiser’s calendar.
          </p>}
          <p role="status" className="text-[12px] text-foreground-600">{data.calendar.message}</p>
          {typeof data.calendar.hideAttendees === 'boolean' && <p className="text-[11px] text-foreground-500">
            {data.calendar.hideAttendees ? 'Microsoft reports that the attendee list is hidden from invitees.' : 'Microsoft reports that the attendee list is not hidden.'}
          </p>}
          <People label="Calendar invitees" people={calendarReadable ? data.calendar.attendees : undefined}
            complete={data.calendar.status === 'available'} />
        </div>
        <div className="space-y-3 border-t border-background-200 pt-3">
          <p role="status" className="text-[12px] text-foreground-600">{data.roles.message}</p>
          {data.roles.allowedPresenters && <p className="text-[12px] font-semibold text-foreground-700">
            {PRESENTERS[data.roles.allowedPresenters] || 'Microsoft returned an unrecognised presenter policy.'}
          </p>}
          <People label="Assigned presenters" people={rolesReadable ? data.roles.presenters : undefined} complete={data.roles.status === 'available'} />
          <People label="Co-organisers" people={rolesReadable ? data.roles.coOrganizers : undefined} complete={data.roles.status === 'available'} />
        </div>
      </>}
      {!data && savedOrganizer && <p className="break-words text-[12px] text-foreground-500">
        Saved calendar invitation organiser — not verified by this read: {savedOrganizer}
      </p>}
      {!data && !reading && !error && <p className="text-[12px] text-foreground-500">The invitation list and meeting roles have not been read yet.</p>}
    </section>
  );
}
