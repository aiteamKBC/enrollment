import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { EntraPeopleInput } from './EntraPeopleInput';
import { CalendarActionDialog } from './CalendarActionDialog';
import { CreateProgressPanel } from './CreateProgressPanel';
import { LeftoverSlotsPanel } from './LeftoverSlotsPanel';
import { AttendeeComparisonPanel } from './AttendeeComparisonPanel';
import { MeetingScopePanel } from './MeetingScopePanel';
import { ExternalCalendarPanel } from './ExternalCalendarPanel';
import { pendingInvitations } from './attendeeComparison';
import { emailList } from "../module-builder/EmailChipsInput.helpers";
import { forwardScheduleEmail } from './forwardScheduleEmail';
import { updateProgressSteps } from './updateProgress';
import { reviewDateLabel } from './calendarTime';
import { offScheduleRunSentence } from './offScheduleRuns';
import { createPortal } from 'react-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import { formatSystemTimestamp } from '@/lib/format';
import { Modal } from '@/pages/users/components/Modal';
import {
  parseUtcInstant,
  saveTeamsRecordingEvents,
  teamsMeetingArtifactPreviewUrl,
  type TeamsMeetingArtifact,
  type TeamsRecordingEventInput,
} from '../module-builder/moduleAuthoringData';
import { ModuleSessionSchedulePreview, TeamsCalendarFormBody, TeamsMeetingOptionFields, TeamsPeopleFields } from './createCalendarForm';
import { TEAMS_LANGUAGE_OPTIONS, TEAMS_LOBBY_OPTIONS, TEAMS_RECORDING_OPTIONS } from "./createCalendarForm.helpers";
import {
  EntityDrawer,
  FormField,
  InlineError,
  SelectControl,
} from '../shared/entities/ui';
import { TeamsPermissionCheckPanel } from './TeamsPermissionCheckPanel';
import { MicrosoftUpdateStatus } from './MicrosoftUpdateStatus';
import { readMicrosoftUpdate } from './microsoftUpdateState';
import { CalendarHealthPanel } from './CalendarHealthPanel';
import { STATE_LABELS, STATE_TONES, meetingRunState, type MeetingSettingsForm, type TeamsMeetingsWorkspace } from './useTeamsMeetingsWorkspace';


/**
 * Play a session recording in place, and record how it was watched.
 *
 * A reviewer watching the recording is standing in for having attended, so the
 * play/pause/skip trail belongs with the session the same way the attendance
 * report does. Who watched is filled in by the backend from the signed-in
 * session rather than declared here. Tracking is best-effort on purpose: a
 * failed post must never interrupt playback, so every send swallows its error.
 */
function RecordingPreview({ liveSessionId, artifact, title, onClose }: {
  liveSessionId: string;
  artifact: TeamsMeetingArtifact;
  title: string;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const queued = useRef<TeamsRecordingEventInput[]>([]);
  const previewSessionId = useRef('');
  const lastTime = useRef(0);
  const watchedSinceSend = useRef(0);

  const flush = useCallback(async () => {
    if (!queued.current.length) return;
    const events = queued.current.splice(0, queued.current.length);
    try {
      const result = await saveTeamsRecordingEvents(liveSessionId, artifact.id, {
        previewSessionId: previewSessionId.current || undefined,
        browser: {
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          userAgent: window.navigator.userAgent,
          pageUrl: window.location.href,
        },
        events,
      });
      previewSessionId.current = result.previewSessionId || previewSessionId.current;
    } catch {
      // Watch tracking is not worth a broken player.
    }
  }, [artifact.id, liveSessionId]);

  const record = useCallback((type: TeamsRecordingEventInput['type'], extra: Partial<TeamsRecordingEventInput> = {}) => {
    const video = videoRef.current;
    queued.current.push({
      type,
      videoTimeSeconds: video?.currentTime ?? 0,
      durationSeconds: Number.isFinite(video?.duration) ? video?.duration : undefined,
      playbackRate: video?.playbackRate ?? 1,
      watchedSecondsDelta: watchedSinceSend.current,
      eventTime: new Date().toISOString(),
      ...extra,
    });
    watchedSinceSend.current = 0;
  }, []);

  // Opening and closing bracket the watch, and a heartbeat keeps a long sit
  // from arriving as one event at the end.
  useEffect(() => {
    record('open');
    const timer = window.setInterval(() => { record('heartbeat'); void flush(); }, 15000);
    return () => {
      window.clearInterval(timer);
      record('close');
      void flush();
    };
  }, [flush, record]);

  const preview = (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-3 backdrop-blur-sm sm:p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Recording — ${title}`}
        className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-background-200 bg-background-50 shadow-2xl"
        onClick={event => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-background-200 px-5 py-4">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-primary-600">Session recording</p>
            <h3 className="mt-0.5 truncate text-[15px] font-heading font-bold text-foreground-950">{title}</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-background-200 bg-background-50 px-3 text-[12px] font-bold text-foreground-600 transition-smooth hover:bg-background-100"
          >
            <AppIcon className="ri-close-line text-sm"></AppIcon>
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto bg-black/90 p-3">
          <video
            ref={videoRef}
            controls
            preload="metadata"
            className="mx-auto max-h-[62vh] w-full rounded-xl bg-black"
            src={teamsMeetingArtifactPreviewUrl(liveSessionId, artifact.id)}
            onPlay={() => record('play')}
            onPause={() => { if (!videoRef.current?.ended) record('pause'); }}
            onEnded={() => { record('ended'); void flush(); }}
            onTimeUpdate={() => {
              const current = videoRef.current?.currentTime ?? 0;
              const delta = current - lastTime.current;
              // Only forward, real-time progress counts as watched; a jump is a
              // skip and is reported as one by the seek handler instead.
              if (delta > 0 && delta < 2) watchedSinceSend.current += delta;
              lastTime.current = current;
            }}
            onSeeked={() => {
              const current = videoRef.current?.currentTime ?? 0;
              record('seeked', {
                previousVideoTimeSeconds: lastTime.current,
                skipFromSeconds: lastTime.current,
                skipToSeconds: current,
              });
              lastTime.current = current;
            }}
          />
        </div>
        <p className="shrink-0 border-t border-background-200 px-5 py-3 text-[11px] font-semibold text-foreground-400">
          Watching is recorded against this session — who watched, and which parts were skipped.
        </p>
      </div>
    </div>
  );
  // A plain child of the page sits inside the same DOM branch as the module
  // detail Modal it is opened from, and that Modal already portals itself to
  // document.body -- so this stayed a sibling of #root while the Modal's own
  // content moved past it, and lost the paint order regardless of matching
  // z-index. Portalling here too puts both at the same level, and this one
  // mounts after (only once "Watch recording" is clicked), so it paints on top.
  return typeof document === 'undefined' ? preview : createPortal(preview, document.body);
}

interface TranscriptCue {
  start: string;
  speaker: string;
  text: string;
}

/** `00:12:03.400` -> `12:03`; drops the hour segment when it is `00`. */
function formatVttTimestamp(value: string): string {
  const match = /^(\d+):(\d{2}):(\d{2})/.exec(value.trim());
  if (!match) return value.trim();
  const [, hours, minutes, seconds] = match;
  return hours === '00' ? `${minutes}:${seconds}` : `${hours}:${minutes}:${seconds}`;
}

/** WEBVTT text, one cue per timed block, into `{ start, speaker, text }`. */
function parseVttCues(vtt: string): TranscriptCue[] {
  return vtt
    .replace(/\r/g, '')
    .split(/\n\n+/)
    .map((block): TranscriptCue | null => {
      const lines = block.split('\n').filter(Boolean);
      const timeIndex = lines.findIndex(line => line.includes('-->'));
      if (timeIndex === -1) return null;
      const start = formatVttTimestamp(lines[timeIndex].split('-->')[0]);
      const raw = lines.slice(timeIndex + 1).join(' ');
      const speakerMatch = /<v\s+([^>]+)>/.exec(raw);
      const text = raw.replace(/<\/?v[^>]*>/g, '').trim();
      return text ? { start, speaker: speakerMatch?.[1].trim() || '', text } : null;
    })
    .filter((cue): cue is TranscriptCue => cue !== null);
}

/**
 * The transcript, read in place rather than downloaded as a `.vtt` file — the
 * same "watch it here" treatment `RecordingPreview` gives the recording, and
 * portalled to `document.body` for the same reason: a plain child would sit
 * inside the module detail Modal's DOM branch and lose the paint order to it
 * even at an equal z-index, because that Modal already portals itself out.
 */
function TranscriptPreview({ liveSessionId, artifact, title, onClose }: {
  liveSessionId: string;
  artifact: TeamsMeetingArtifact;
  title: string;
  onClose: () => void;
}) {
  const [state, setState] = useState<
    | { status: 'loading' }
    | { status: 'error'; message: string }
    | { status: 'ready'; cues: TranscriptCue[] }
  >({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    fetch(teamsMeetingArtifactPreviewUrl(liveSessionId, artifact.id))
      .then(response => {
        if (!response.ok) throw new Error(`The transcript could not be loaded (${response.status}).`);
        return response.text();
      })
      .then(text => { if (!cancelled) setState({ status: 'ready', cues: parseVttCues(text) }); })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: 'error', message: err instanceof Error ? err.message : 'The transcript could not be loaded.' });
        }
      });
    return () => { cancelled = true; };
  }, [artifact.id, liveSessionId]);

  const preview = (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-3 backdrop-blur-sm sm:p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Transcript — ${title}`}
        className="flex max-h-[92vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-background-200 bg-background-50 shadow-2xl"
        onClick={event => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-background-200 px-5 py-4">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-primary-600">Session transcript</p>
            <h3 className="mt-0.5 truncate text-[15px] font-heading font-bold text-foreground-950">{title}</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-background-200 bg-background-50 px-3 text-[12px] font-bold text-foreground-600 transition-smooth hover:bg-background-100"
          >
            <AppIcon className="ri-close-line text-sm"></AppIcon>
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {state.status === 'loading' && (
            <p className="text-[12px] font-semibold text-foreground-500">Loading transcript…</p>
          )}
          {state.status === 'error' && (
            <p className="text-[12px] font-semibold text-red-600">{state.message}</p>
          )}
          {state.status === 'ready' && (
            state.cues.length ? (
              <ul className="space-y-3">
                {state.cues.map((cue, index) => (
                  <li key={index} className="text-[13px] leading-relaxed">
                    <span className="font-bold text-foreground-400">{cue.start}</span>
                    {cue.speaker && <span className="ml-2 font-bold text-primary-700">{cue.speaker}</span>}
                    <p className="mt-0.5 text-foreground-800">{cue.text}</p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[12px] font-semibold text-foreground-500">No transcript text was found.</p>
            )
          )}
        </div>
      </div>
    </div>
  );
  return typeof document === 'undefined' ? preview : createPortal(preview, document.body);
}

/**
 * The settings fields, shared by every place that edits an existing meeting.
 * The option values are the create form's own (`TEAMS_*_OPTIONS`), so a meeting
 * can only ever be set to something it could have been created with.
 */
function MeetingSettingsFields({ form, patch }: {
  form: MeetingSettingsForm;
  patch: (value: Partial<MeetingSettingsForm>) => void;
}) {
  return (
    <>
      <FormField label="Recording">
        <SelectControl value={form.recording} onChange={value => patch({ recording: value })} options={TEAMS_RECORDING_OPTIONS} />
      </FormField>
      <FormField label="Who can bypass the lobby?">
        <SelectControl value={form.lobbyBypass} onChange={value => patch({ lobbyBypass: value })} options={TEAMS_LOBBY_OPTIONS} />
      </FormField>
      <FormField label="Spoken language" hint="Used for the recording and transcript.">
        <SelectControl value={form.spokenLanguage} onChange={value => patch({ spokenLanguage: value })} options={TEAMS_LANGUAGE_OPTIONS} />
      </FormField>
      <FormField label="Presenters" hint="Only these people get the presenter role in Teams: they can share and record.">
        <EntraPeopleInput label="Presenters" value={form.presenters} onChange={value => patch({ presenters: value })} />
      </FormField>
      <FormField
        label="Co-organisers"
        hint="Internal Microsoft 365 users who run the meeting with the organiser: start and stop the recording, admit people from the lobby and change the meeting options. Invited automatically."
      >
        <EntraPeopleInput label="Co-organizers" value={form.coOrganizers} onChange={value => patch({ coOrganizers: value })} />
      </FormField>
    </>
  );
}


/**
 * The Teams Meetings dialog and everything it opens, for one workspace.
 *
 * Rendered by the Teams Meetings page and by a module's own screen alike, so
 * both show the same calendar the same way and act on it through the same code.
 */
/**
 * An extra tab alongside the module's own calendar.
 *
 * The Teams Meetings page passes none and is unchanged: without one there is no
 * tab strip and this dialog is exactly what it has always been. The Module
 * Builder passes the additional-week-meeting form. The two tabs share nothing
 * but the dialog frame — the module calendar's state, its footer actions and
 * its backend calls are untouched while the second tab is open, and the second
 * tab carries its own Create button rather than borrowing the footer's.
 */
export interface TeamsDialogSecondTab {
  label: string;
  /** Replaces the module's name/cohort line while this tab is open. */
  subtitle?: string;
  render: () => ReactNode;
}

export function TeamsMeetingDialogs({ workspace, secondTab }: {
  workspace: TeamsMeetingsWorkspace;
  secondTab?: TeamsDialogSecondTab;
}) {
  const [activeTab, setActiveTab] = useState<'calendar' | 'second'>('calendar');
  const [forwardOpen, setForwardOpen] = useState(false);
  const [forwardRecipients, setForwardRecipients] = useState('');
  const [forwardSaving, setForwardSaving] = useState(false);
  const [forwardError, setForwardError] = useState('');
  const [forwardNotice, setForwardNotice] = useState('');
  const [updateConfirmOpen, setUpdateConfirmOpen] = useState(false);
  const onSecondTab = Boolean(secondTab) && activeTab === 'second';
  // Coming back from the additional-meeting tab: a week may have been booked or
  // cancelled there, which changes which calendar runs it.
  const { refreshMeetingScopes: refreshScopes } = workspace;
  const leftSecondTab = useRef(false);
  useEffect(() => {
    if (activeTab === 'second') { leftSecondTab.current = true; return; }
    if (leftSecondTab.current) { leftSecondTab.current = false; refreshScopes(); }
  }, [activeTab, refreshScopes]);
  const {
    calendarActionTarget, loadTeamsState, notifyChanged, setSelectedId, setCalendarActionTarget, selected,
    requestCloseSelected, notice, openCalendarAction, busy, detailLoading, detail, graphConfigured,
    graphStatus, checkGraphConfiguration,
    checkCalendarAction, selectedForDisplay, createCalendar, createDrawer, createRecovery,
    createDraft, createDraftSaving, saveCreateDraft, leftoverCancelling, cancelLeftover,
    runCalendarSync, calendarSyncing, autoSyncEnabled, setAutoSyncEnabled,
    detailError, loadDetail, holidayLabelFor,
    detailOccurrenceFor, now, setPreview, setTranscriptPreview, invitedPrefilling, prefillNotice, prefillInvitees, preview,
    transcriptPreview, settingsDrawer, drawerTarget, saveSettings, blockedReason,
    teamsLoaded, teamsError, createProgress, updateProgress, saveInvitations, addedInvitees, updateDrawer, pushDates, resendSchedule,
    selectedScopeRows, chooseMeetingScope, reattach, externalCalendar, calendarLinksLoading, calendarLinksError,
    comparison, comparing, comparisonError, compareAttendees, publishedInvitees,
  } = workspace;
  // Who the author has typed in but not saved. Named under the comparison so a
  // reader can see why Microsoft does not have them.
  const pendingInvitees = selected
    ? pendingInvitations([...emailList(updateDrawer.form.attendees), ...emailList(updateDrawer.form.presenters),
      ...emailList(updateDrawer.form.coOrganizers)], publishedInvitees(selected))
    : [];
  const submitForward = async () => {
    if (!selected?.summary?.liveSessionId) return;
    const recipients = emailList(forwardRecipients);
    if (!recipients.length) { setForwardError('Add at least one email address.'); return; }
    setForwardSaving(true); setForwardError(''); setForwardNotice('');
    try {
      const result = await forwardScheduleEmail(selected.summary.liveSessionId, recipients);
      if (result.uncertain || result.failed || result.queued) {
        setForwardError(`The schedule email is incomplete: ${result.accepted} accepted, ${result.failed} failed, ${result.uncertain + result.queued} pending.`);
      } else {
        setForwardNotice(`Schedule emailed to ${result.accepted} recipient${result.accepted === 1 ? '' : 's'}.`);
        setForwardRecipients('');
      }
    } catch (error) {
      setForwardError(error instanceof Error ? error.message : 'The schedule could not be forwarded.');
    } finally { setForwardSaving(false); }
  };
  return (
    <>
        {calendarActionTarget && <CalendarActionDialog target={calendarActionTarget}
          onChanged={async () => { await loadTeamsState(); notifyChanged(calendarActionTarget.moduleId); }}
          onClose={() => { setSelectedId(calendarActionTarget.moduleId); setCalendarActionTarget(null); }} />}

        {/* The module row exists as soon as the module list does, but its session
            dates and its calendar arrive with the Teams state. Until that first
            read lands the row has neither, and judging it then told a saved,
            scheduled module it had "no stored session dates" -- and briefly
            offered Create on a module that already had a calendar. */}
        {/* One dialog throughout: while loading it holds the notice and no
            actions, then the same dialog fills in rather than a second popping up. */}
        {selected && (
          <Modal
            /* Two lines rather than one long "name — Teams meeting" string: the
               module is the subject, its cohort and group are the context. */
            title={(
              <span className="block min-w-0">
                <span className="block truncate">{selected.name}</span>
                <span className="mt-1 block truncate text-[11px] font-semibold text-foreground-400">
                  {(onSecondTab && secondTab?.subtitle)
                    || [selected.cohortName, selected.groupName, selected.programmeName].filter(Boolean).join(' · ')
                    || 'Teams meeting'}
                </span>
              </span>
            )}
            /* The comparison table needs the width; the empty state does not,
               and a wide box around two sentences is what made this look bare. */
            size={selected.summary && teamsLoaded ? 'max-w-5xl' : 'max-w-3xl'}
            onClose={requestCloseSelected}
            /* The footer drives the module's own calendar. The second tab has
               its own button inside it, so this is hidden rather than left
               there acting on a calendar the reader is not looking at. */
            footer={!teamsLoaded || calendarLinksLoading || calendarLinksError || onSecondTab ? undefined : (
              <div className="flex w-full flex-wrap items-center justify-end gap-2">
                {/* Only the actions this module can actually take: a footer of
                    greyed-out buttons reads as broken rather than as guidance.
                    Closing is the title bar's X — no second Close down here. */}
                {/* Background checks also finish while another module is open.
                    Keep their warnings on the page, with their module name. */}
                {notice && (!notice.moduleId || notice.moduleId === selected.catalogueId) && (
                  <p role="status" className={`mb-4 w-full rounded-lg border p-3 text-sm ${
                    notice.tone === 'error'
                      ? 'border-red-200 bg-red-50 text-red-700'
                      : notice.tone === 'warning'
                        ? 'border-amber-200 bg-amber-50 text-amber-800'
                        : 'border-primary-100 bg-primary-50 text-primary-700'
                  }`}
                  >
                    {notice.text}
                    {/* Recording a cancellation Microsoft shows is still the
                        author's own Cancel, through its own review. */}
                    {notice.recordCancel && (
                      <span className="mt-2 flex flex-wrap gap-2">
                        {(notice.recordCancel.series ? [undefined] : notice.recordCancel.sessions).map(sessionNumber => (
                          <button key={sessionNumber ?? 'series'} type="button"
                            onClick={() => openCalendarAction('cancel', sessionNumber)}
                            disabled={Boolean(busy) || detailLoading || !graphConfigured}
                            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 text-[12px] font-bold text-amber-800 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50">
                            {sessionNumber === undefined ? 'Record the series cancellation' : `Record session ${sessionNumber} as cancelled`}
                          </button>
                        ))}
                      </span>
                    )}
                  </p>
                )}
            {externalCalendar ? (
              <p className="text-[12px] font-semibold text-primary-800">Existing Teams links are protected. Manage the original meeting in Teams.</p>
            ) : selected.summary ? (
                  <div className="flex w-full flex-wrap items-center justify-end gap-2">
                    <button type="button" onClick={() => openCalendarAction('reschedule')}
                      disabled={Boolean(busy) || Boolean(updateProgress) || Boolean(blockedReason) || detailLoading || detail?.series.id !== selected.summary.liveSessionId || !graphConfigured}
                      className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-background-200 bg-background-50 px-3 text-[12px] font-bold text-foreground-700 transition-colors hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-40"><AppIcon className="ri-calendar-line text-sm"></AppIcon>Edit session dates</button>
                    <button type="button" onClick={() => openCalendarAction('cancel')}
                      disabled={Boolean(busy) || Boolean(updateProgress) || Boolean(blockedReason) || detailLoading || detail?.series.id !== selected.summary.liveSessionId || !graphConfigured}
                      className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 text-[12px] font-bold text-red-700 transition-colors hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-40"><AppIcon className="ri-close-circle-line text-sm"></AppIcon>Cancel series</button>
                    <button type="button" onClick={() => void checkCalendarAction()} disabled={Boolean(busy) || Boolean(updateProgress) || !graphConfigured}
                      className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 text-[12px] font-bold text-primary-700 transition-colors hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-40"><AppIcon className={busy ? 'ri-loader-4-line animate-spin text-sm' : 'ri-refresh-line text-sm'}></AppIcon>{busy ? 'Checking…' : 'Check action status'}</button>
                    {/* Its own action, deliberately outside the update: it
                        sends nothing to Microsoft and opens no review. It
                        re-sends the joining schedule -- the creation email,
                        every date and every link -- to everyone this calendar
                        invites, which is what an author reaches for when the
                        first email was missed rather than when a date moved. */}
                    <button type="button" onClick={() => void resendSchedule(selectedForDisplay || selected)}
                      disabled={Boolean(busy) || Boolean(updateProgress) || Boolean(blockedReason) || detailLoading || updateDrawer.saving || !graphConfigured}
                      title="Send the full schedule email -- the complete timetable and join links, the same message a new calendar sends -- to every invited learner, the organiser, the co-organisers and the presenters. They are all emailed, including anyone who already received it. The Teams calendar is not changed."
                      className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 text-[12px] font-bold text-primary-700 transition-colors hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-40">
                      <AppIcon className={busy === `${selected.catalogueId}:resend` ? 'ri-loader-4-line animate-spin text-sm' : 'ri-mail-send-line text-sm'}></AppIcon>
                      Email the schedule to everyone as the original creation email (not an update)
                    </button>
                    <button type="button" onClick={() => { setForwardOpen(true); setForwardError(''); setForwardNotice(''); }}
                      disabled={Boolean(busy) || Boolean(updateProgress) || Boolean(blockedReason) || detailLoading || updateDrawer.saving || !graphConfigured}
                      title="Email the current timetable and Teams join links to the addresses you choose. This is an LMS email: it does not add the meeting to anyone's Outlook calendar."
                      className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-background-200 bg-background-50 px-3 text-[12px] font-bold text-foreground-700 transition-colors hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-40">
                      <AppIcon className="ri-mail-line text-sm"></AppIcon>
                      Email schedule to…
                    </button>
                    {updateConfirmOpen && (
                      <div className="w-full rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-900">
                        <p className="font-bold">Before updating the Teams calendar</p>
                        <p className="mt-1 leading-relaxed">Teams will move the session dates to the planned schedule. If any date changes, Microsoft sends one update to everyone already invited, and the LMS emails each of them what changed, so Outlook and the email agree. If only the invitations changed, nobody already invited is contacted: the people you add get the Teams invitation and their schedule.</p>
                        <button type="button" onClick={() => setUpdateConfirmOpen(false)} className="mt-2 font-semibold text-foreground-600 underline">Cancel</button>
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        if (!updateConfirmOpen) { setUpdateConfirmOpen(true); return; }
                        void pushDates(selectedForDisplay || selected);
                      }}
                      // Waits for the calendar's own read, so the invitation
                      // fields it sends with the dates hold the saved values.
                      disabled={!(selectedForDisplay || selected).sessions.length || Boolean(busy) || Boolean(updateProgress) || Boolean(blockedReason) || detailLoading || updateDrawer.saving || !graphConfigured}
                      title="Move the Teams calendar onto this module's stored session dates, holiday shifts included, with any changes to the invitations below."
                      className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-[12px] font-bold text-white shadow-sm transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <AppIcon className={busy === `${selected.catalogueId}:dates` ? 'ri-loader-4-line animate-spin text-sm' : 'ri-calendar-check-line text-sm'}></AppIcon>
                      {/* The calendar already exists by the time this button is
                          rendered, so the action is an update, not a first send
                          -- which is what its own confirmation has always said. */}
                      {updateConfirmOpen ? 'Confirm update' : 'Update Teams calendar'}
                    </button>
                   </div>
                 ) : (
                  <>
                  {createDraft?.catalogueId === selected.catalogueId && !createDrawer.dirty && (
                    <span className="mr-auto text-[11px] font-semibold text-foreground-500">
                      Draft saved {formatSystemTimestamp(createDraft.updatedAt, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}
                      {createDraft.updatedByName ? ` by ${createDraft.updatedByName}` : ''}. Nothing is in Teams yet.
                    </span>
                  )}
                  {/* Keeps the answers on the server for this module and
                      nothing more: no Microsoft call, no email. */}
                  <button
                    type="button"
                    onClick={() => void saveCreateDraft(selected)}
                    disabled={!createDrawer.dirty || createDraftSaving || createDrawer.saving
                      || Boolean(createProgress) || Boolean(createRecovery && createRecovery.phase !== 'failed')}
                    title="Keep what you have filled in for this module without creating anything in Teams. Nobody is emailed."
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-background-300 bg-white px-4 text-[12px] font-bold text-foreground-700 transition-smooth hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <AppIcon className={createDraftSaving ? 'ri-loader-4-line animate-spin text-sm' : 'ri-save-3-line text-sm'}></AppIcon>
                    Save draft
                  </button>
                  <button
                    type="button"
                    onClick={() => void createCalendar(selected)}
                    // While a lost Create is being checked, still running, or ended
                    // uncertain, a second Create is exactly what must not be sent.
                    disabled={!(selectedForDisplay || selected).sessions.length || createDrawer.saving || Boolean(busy) || Boolean(blockedReason) || !graphConfigured
                      || Boolean(createRecovery && createRecovery.phase !== 'failed')}
                    title="Create one Teams meeting on each of this module's stored session dates."
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-4 text-[12px] font-bold text-white transition-smooth hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <AppIcon className={createDrawer.saving ? 'ri-loader-4-line animate-spin text-sm' : 'ri-calendar-check-line text-sm'}></AppIcon>
                    Create
                  </button>
                  </>
                )}
              </div>
            )}
          >
            {secondTab && (
              /* Only ever rendered when a caller supplies a second tab, so the
                 Teams Meetings page shows no tab strip at all. */
              <div role="tablist" aria-label="Teams meeting type" className="mb-4 flex flex-wrap gap-1 rounded-xl border border-background-200 bg-background-100/70 p-1">
                {([
                  { key: 'calendar' as const, label: 'Module Teams calendar' },
                  { key: 'second' as const, label: secondTab.label },
                ]).map(tab => (
                  <button
                    key={tab.key}
                    type="button"
                    role="tab"
                    aria-selected={activeTab === tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={`inline-flex h-9 min-w-0 flex-1 items-center justify-center gap-1.5 rounded-lg px-3 text-[12px] font-bold transition-smooth ${
                      activeTab === tab.key
                        ? 'bg-primary-600 text-white shadow-sm'
                        : 'text-foreground-600 hover:bg-background-200'
                    }`}
                  >
                    <AppIcon className={tab.key === 'calendar' ? 'ri-calendar-schedule-line text-sm' : 'ri-calendar-2-line text-sm'}></AppIcon>
                    <span className="truncate">{tab.label}</span>
                  </button>
                ))}
              </div>
            )}
            {onSecondTab ? secondTab?.render() : !teamsLoaded || calendarLinksLoading || calendarLinksError ? (
              calendarLinksError || teamsError ? (
                <div className="space-y-3">
                  <p role="alert" className="text-sm text-red-700">{calendarLinksError || teamsError}</p>
                  <button type="button" onClick={() => { refreshScopes(); void loadTeamsState(); }}
                    className="rounded-lg border px-3 py-2 text-[12px] font-bold">Try again</button>
                </div>
              ) : (
                <p role="status" className="flex items-center gap-1.5 rounded-xl border border-background-200 bg-background-100/60 p-3 text-[11px] font-semibold text-foreground-500">
                  <AppIcon className="ri-loader-4-line animate-spin"></AppIcon>
                  Loading this module&rsquo;s session dates and Teams calendar, and checking its existing Teams links…
                </p>
              )
            ) : (<>
            {/* Where this module stands, in the same chips the table uses. */}
            <div className="mb-4 flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-wide">
              <span className={`inline-flex items-center rounded-full border px-2.5 py-1 ${STATE_TONES[selected.state]}`}>
                {externalCalendar && !selected.summary ? 'Existing link · dates unconfirmed' : STATE_LABELS[selected.state]}
              </span>
              <span className="inline-flex items-center rounded-full border border-background-200 bg-background-100 px-2.5 py-1 text-foreground-600">
                {(selectedForDisplay || selected).sessions.length} module session{(selectedForDisplay || selected).sessions.length === 1 ? '' : 's'}
              </span>
              {selected.differingSessions > 0 && (
                <span className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-amber-800">
                  {selected.differingSessions === 1 ? '1 date differs' : `${selected.differingSessions} dates differ`}
                </span>
              )}
            </div>

            {/* Two different facts, so two different sentences. Only the first
                one has been established; the second is this screen admitting it
                does not know, and it disables nothing. */}
            {graphStatus === 'unconfigured' && (
              <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-semibold text-amber-800">
                Microsoft Graph credentials are missing from the backend, so nothing here can reach the Teams calendar.
              </p>
            )}
            {graphStatus === 'unknown' && (
              <p className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-background-200 bg-background-50 px-3 py-2 text-[11px] font-semibold text-foreground-600">
                The Teams calendar check did not finish, so this screen could not confirm the backend is set up for
                Microsoft. The actions below still work &mdash; if the setup is missing, the action itself will say so.
                <button type="button" onClick={() => void checkGraphConfiguration()} className="font-bold text-primary-700 underline hover:text-primary-800">
                  Check again
                </button>
              </p>
            )}
            {blockedReason && (
              <div role="status" className="mb-4 flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
                <AppIcon className="ri-error-warning-line mt-0.5 shrink-0 text-sm text-amber-700"></AppIcon>
                <p className="text-[11px] font-semibold leading-relaxed text-amber-900">{blockedReason}</p>
              </div>
            )}

            <MeetingScopePanel
              rows={selectedScopeRows}
              busy={busy}
              disabled={Boolean(blockedReason) || Boolean(updateProgress) || createDrawer.saving || updateDrawer.saving}
              onChoose={(componentId, scope) => void chooseMeetingScope(componentId, scope)}
              externalCalendar={Boolean(externalCalendar)}
            />

            {externalCalendar ? (
              <ExternalCalendarPanel
                calendar={externalCalendar}
                row={selectedForDisplay || selected}
                onReattach={() => void reattach(selected)}
                busy={Boolean(busy)}
                disabled={Boolean(blockedReason)}
              />
            ) : selected.summary ? updateProgress ? (
              <CreateProgressPanel
                moduleName={selected.name}
                sessionCount={(selectedForDisplay || selected).sessions.length}
                progress={null}
                recovery={null}
                busy
                onCheckAgain={() => undefined}
                onConfirmUncertain={() => undefined}
                steps={updateProgressSteps(updateProgress, (selectedForDisplay || selected).sessions.length)}
                title={`Updating the Teams calendar for ${selected.name}`}
                description="Microsoft is applying the calendar and invitation changes. The update emails are sent after the saved meeting is confirmed."
              />
            ) : (
              <div className="space-y-4">
                {/* Invitations are edited beside the people they name, rather
                    than from a button on every table row. */}
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-foreground-400">The meeting</p>
                  <span className="flex flex-wrap items-center gap-2">
                  {/* Once the dates are on the calendar the next thing anyone
                      wants is the way in, so the join link is a button here
                      rather than a line of text further down the panel. */}
                  {Boolean(selected.summary.joinUrl) && (
                    <a
                      href={selected.summary.joinUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="meeting-join-action inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-[11px] font-bold transition-smooth"
                    >
                      <AppIcon className="ri-microsoft-teams-line text-sm"></AppIcon>
                      Join Teams meeting
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={() => void runCalendarSync(selected.summary!.liveSessionId, 'manual')}
                    disabled={calendarSyncing.has(selected.summary.liveSessionId) || !graphConfigured}
                    title="Check Microsoft for cancellations and update the LMS."
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-2.5 text-[11px] font-bold text-primary-700 hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <AppIcon className={`${calendarSyncing.has(selected.summary.liveSessionId) ? 'ri-loader-4-line animate-spin' : 'ri-refresh-line'} text-sm`}></AppIcon>
                    {calendarSyncing.has(selected.summary.liveSessionId) ? 'Checking calendar...' : 'Sync calendar status'}
                  </button>
                  {/* Attendance, transcripts and recordings are not asked for
                      from here. The durable server worker polls for them every
                      minute and owns the transfer, so the only honest place to
                      ask for them sooner is beside the sessions they belong to,
                      in the module component's Sessions & Recordings. */}

                  {/* This switch is one browser's preference for the in-page
                      cancellation sweep, and nothing more: the server worker
                      collects attendance and files whether it is on or off.
                      Without Graph credentials the sweep cannot run at all, so
                      the control says that instead of showing a green "on" for
                      something that is not happening. */}
                  <button
                    type="button"
                    role="switch"
                    aria-checked={graphConfigured && autoSyncEnabled}
                    disabled={!graphConfigured}
                    onClick={() => setAutoSyncEnabled(enabled => !enabled)}
                    title={graphConfigured
                      ? 'Checks this calendar for cancellations every five minutes while this page is open, in this browser only. Attendance and files are collected by the server either way.'
                      : 'Microsoft Graph credentials are missing from the backend, so no calendar check can run.'}
                    className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-bold transition-smooth disabled:cursor-not-allowed disabled:opacity-50 ${
                      graphConfigured && autoSyncEnabled
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                        : 'border-background-200 bg-background-50 text-foreground-500 hover:bg-background-100'
                    }`}
                  >
                    <AppIcon className={`${graphConfigured && autoSyncEnabled ? 'ri-checkbox-circle-line' : 'ri-close-circle-line'} text-sm`}></AppIcon>
                    {graphConfigured ? `Auto-sync ${autoSyncEnabled ? 'on' : 'off'}` : 'Auto-sync unavailable'}
                  </button>
                  {/* Meeting settings are edited in the Invitations and meeting
                      settings section under the dates, and Sessions &
                      Recordings opens from the module's own component, so
                      neither is repeated here. */}
                  </span>
                </div>

                {detailError && <InlineError message={detailError} onRetry={() => void loadDetail(selected.summary!.liveSessionId)} />}

                {/* One list, not two. The module's own dates, the meetings
                    Teams holds on them and what each meeting offers -- the way
                    in, who attended, what it left behind -- are facts about the
                    same sessions, so they hang off the same rows. */}
                <ModuleSessionSchedulePreview
                  row={selectedForDisplay || selected}
                  title="Module dates sent to Teams"
                  holidayLabelFor={holidayLabelFor}
                  renderActions={(index, durationMinutes, teamsUtc) => {
                    const plannedUtc = (selectedForDisplay || selected).plannedStarts[index] || '';
                    const occurrence = detailOccurrenceFor(index, teamsUtc, plannedUtc);
                    if (occurrence?.status === 'cancelled') {
                      return <span role="status" className="inline-flex items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-2.5 py-1 text-[11px] font-bold text-red-700"><AppIcon className="ri-close-circle-line text-sm"></AppIcon>Cancelled</span>;
                    }
                    // Taken out of the plan, never cancelled: its Teams slot is
                    // still there until somebody presses Cancel on it.
                    if (occurrence?.status === 'superseded') {
                      return <span role="status" title="This session is no longer in the module plan, but nobody cancelled it, so its Teams meeting is still on the calendar. Cancel it from the slots below if it should not run." className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-800"><AppIcon className="ri-calendar-event-line text-sm"></AppIcon>Not in plan – still on Teams</span>;
                    }
                    // The meeting runs when Teams says it does, so the clock
                    // is read against the calendar entry when there is one
                    // and against the module's own date when there is not.
                    const runsAt = occurrence?.scheduled_start || teamsUtc || plannedUtc;
                    const runState = meetingRunState(runsAt, durationMinutes, now);
                    if (runState === 'ended') {
                      return (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold text-foreground-400">
                          <AppIcon className="ri-check-double-line"></AppIcon>
                          Session ended
                        </span>
                      );
                    }
                    // A session Teams would only accept as an event of its own
                    // has a link of its own, so the row's own link comes first
                    // and the series' link is the fallback the rest share.
                    // A row Teams holds nothing for has no meeting to join yet.
                    const joinUrl = occurrence?.join_url || (teamsUtc ? selected.summary?.joinUrl : '') || '';
                    if (!joinUrl) {
                      return <span className="text-[11px] font-semibold text-foreground-400">Not on Teams yet</span>;
                    }
                    return (
                      <span className="flex flex-wrap items-center justify-end gap-2">
                      {occurrence?.status === 'scheduled' && !occurrence.actual_start && !occurrence.attendance_report_id && !occurrence.participant_count && (
                        <>
                          <button type="button" disabled={Boolean(busy) || Boolean(blockedReason) || detailLoading || !graphConfigured}
                            onClick={() => openCalendarAction('reschedule', occurrence.session_number)}
                            className="inline-flex h-8 items-center gap-1 rounded-lg border border-background-200 bg-background-50 px-2.5 text-[11px] font-bold text-foreground-700 transition-colors hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-50"><AppIcon className="ri-time-line text-sm"></AppIcon>Edit time</button>
                          <button type="button" disabled={Boolean(busy) || Boolean(blockedReason) || detailLoading || !graphConfigured}
                            onClick={() => openCalendarAction('cancel', occurrence.session_number)}
                            className="inline-flex h-8 items-center gap-1 rounded-lg border border-red-200 bg-red-50 px-2.5 text-[11px] font-bold text-red-700 transition-colors hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-50"><AppIcon className="ri-close-circle-line text-sm"></AppIcon>Cancel session</button>
                        </>
                      )}
                      <a
                        href={joinUrl}
                        target="_blank"
                        rel="noreferrer"
                        className={`meeting-join-action inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[11px] font-bold transition-smooth ${
                          runState === 'live'
                            ? ''
                            : 'opacity-80 hover:opacity-100'
                        }`}
                      >
                        <AppIcon className="ri-microsoft-teams-line text-sm"></AppIcon>
                        {runState === 'live' ? 'Join now' : 'Join Teams'}
                      </a>
                      </span>
                    );
                  }}
                  renderFacts={(index, _durationMinutes, teamsUtc) => {
                    const occurrence = detailOccurrenceFor(index, teamsUtc);
                    // Nothing is written down for a meeting that has not run:
                    // "0 attended, nothing yet" on every future session is a
                    // column of absences, and the note under the list already
                    // says these facts arrive afterwards.
                    if (!occurrence) return null;
                    const attended = occurrence.participant_count || occurrence.attendance?.length || 0;
                    const artifacts = occurrence.artifacts || [];
                    // A call on this link that was not this session (the link tried
                    // early, another day's use) is said, with both dates, even before
                    // the session runs: Teams lists it as the meeting being held then.
                    const offSchedule = occurrence.off_schedule_runs || [];
                    if (!attended && !artifacts.length && !offSchedule.length) return null;
                    return (
                      <>
                        {offSchedule.map(run => (
                          <span key={`${run.runStartUtc}-${run.runEndUtc}`} role="note" className="basis-full font-semibold text-amber-800">
                            {offScheduleRunSentence(run, iso => reviewDateLabel(iso, detail?.series.timeZoneIana || 'Europe/London'))}
                          </span>
                        ))}
                        {Boolean(attended) && (
                          <span className="font-semibold text-foreground-600">{attended} attended</span>
                        )}
                        {artifacts.map(artifact => (artifact.artifact_type === 'recording' ? (
                          // Played here rather than downloaded, so the watch
                          // is recorded against the session.
                          <button
                            key={artifact.id}
                            type="button"
                            onClick={() => setPreview({
                              liveSessionId: selected.summary!.liveSessionId,
                              artifact,
                              title: `${selected.name} — meeting ${index + 1}`,
                            })}
                            className="inline-flex items-center gap-1 font-bold text-primary-700 hover:underline"
                          >
                            <AppIcon className="ri-play-circle-line text-sm"></AppIcon>
                            Watch recording
                          </button>
                        ) : artifact.artifact_type === 'transcript' ? (
                          // Read here rather than downloaded, matching the
                          // recording's own in-place preview.
                          <button
                            key={artifact.id}
                            type="button"
                            onClick={() => setTranscriptPreview({
                              liveSessionId: selected.summary!.liveSessionId,
                              artifact,
                              title: `${selected.name} — meeting ${index + 1}`,
                            })}
                            className="font-bold text-primary-700 hover:underline"
                          >
                            Transcript
                          </button>
                        ) : (
                          <a
                            key={artifact.id}
                            href={teamsMeetingArtifactPreviewUrl(selected.summary!.liveSessionId, artifact.id)}
                            target="_blank"
                            rel="noreferrer"
                            className="font-bold text-primary-700 hover:underline"
                          >
                            {artifact.artifact_type}
                          </a>
                        )))}
                      </>
                    );
                  }}
                />

                {/* Slots Teams holds that the plan does not. Only their own
                    Cancel removes one -- never Update or the status check. */}
                <LeftoverSlotsPanel
                  slots={detail?.series.id === selected.summary?.liveSessionId ? detail?.leftoverSlots || [] : []}
                  cancelling={leftoverCancelling}
                  disabled={Boolean(busy) || Boolean(updateProgress) || updateDrawer.saving || !graphConfigured}
                  onCancel={slot => void cancelLeftover(slot)}
                />

                {/* Under the dates, the same fields the create form asks for,
                    filled with what the calendar holds. Update sends them with
                    the dates; the two buttons below send them alone. */}
                <section aria-labelledby="teams-invitations-heading" className="space-y-4 rounded-xl border border-background-200 p-4">
                  <div>
                    <h3 id="teams-invitations-heading" className="text-[11px] font-bold uppercase tracking-wider text-foreground-400">Invitations and meeting settings</h3>
                    <p className="mt-1 text-[11px] text-foreground-500">
                      Only what you change here is updated, and the join link stays the same, and no session date moves. Save without notifying sends nothing to anybody. Save and invite added people sends the Teams invitation and the schedule email to the people you added, and to nobody else.
                    </p>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <FormField label="Organizer Microsoft 365 email" hint="This organizer owns the existing calendar.">
                      <input aria-label="Organizer" readOnly value={updateDrawer.form.organizerEmail}
                        className="h-11 w-full rounded-lg border border-background-200 bg-background-100 px-3 text-[13px]" />
                    </FormField>
                    <TeamsMeetingOptionFields form={updateDrawer.form} patch={updateDrawer.patch} />
                    <TeamsPeopleFields
                      form={updateDrawer.form}
                      patch={updateDrawer.patch}
                      prefilling={invitedPrefilling}
                      prefillNotice={prefillNotice}
                      onPrefill={() => void prefillInvitees(selected, updateDrawer.patch)}
                    />
                  </div>
                  {/* Beside the fields it reports on, and above Save rather
                      than in its row: pressing it is not a step towards
                      saving, and it stays available when Save is not. */}
                  <AttendeeComparisonPanel
                    comparison={comparison}
                    comparing={comparing}
                    error={comparisonError}
                    pending={pendingInvitees}
                    disabled={Boolean(blockedReason) || !graphConfigured}
                    onCompare={() => void compareAttendees(selected)}
                  />
                  {/* Three separate questions, never folded into one status: have
                      Microsoft applied the meeting settings; is the calendar
                      structurally sound; is each session's lifecycle and history
                      trustworthy. */}
                  <section aria-label="Meeting health" className="space-y-2">
                    <div>
                      <p className="text-[11px] font-bold uppercase tracking-wider text-foreground-600">Meeting health</p>
                      <p className="text-[11px] text-foreground-500">
                        Meeting settings show whether Microsoft applied them. Calendar health shows whether every session is on the right Teams meeting, and its lifecycle and history.
                      </p>
                    </div>
                    {/* Saved / Pending Microsoft update / Failed, with Retry: re-sends
                        only the meeting settings Microsoft has not applied. */}
                    <MicrosoftUpdateStatus
                      liveSessionId={selected.summary?.liveSessionId || ''}
                      status={readMicrosoftUpdate(selected.summary?.microsoftUpdate)}
                      disabled={!graphConfigured}
                    />
                    {/* Read-only, like the comparison: it says whether Microsoft lets the
                        LMS manage this organiser's meeting, and never changes it. */}
                    <TeamsPermissionCheckPanel
                      liveSessionId={selected.summary?.liveSessionId || ''}
                      disabled={!graphConfigured}
                    />
                    {/* Read-only until a resolution is reviewed and confirmed: meeting type,
                        link integrity, lifecycle and attribution for every session. */}
                    <CalendarHealthPanel
                      liveSessionId={selected.summary?.liveSessionId || ''}
                      disabled={!graphConfigured}
                    />
                  </section>
                  {updateDrawer.error && <InlineError message={updateDrawer.error} />}
                  {addedInvitees(selected).length > 0 && (
                    <p role="note" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] font-semibold text-amber-900">
                      With Save without notifying, added people will be saved to the Teams meeting but will not receive an invitation or LMS email. Use Save and invite added people to send them both.
                    </p>
                  )}
                  <div className="flex flex-wrap items-center justify-end gap-3">
                    {updateDrawer.dirty && <span className="text-[11px] font-semibold text-amber-700">Changes not sent yet</span>}
                    <button
                      type="button"
                      onClick={() => void saveInvitations(selected)}
                      disabled={!updateDrawer.dirty || updateDrawer.saving || Boolean(busy) || Boolean(blockedReason) || detailLoading || !graphConfigured}
                      title="Save only these invitation and setting changes. Nobody is emailed, including people you add, and every session date stays where Teams holds it."
                      className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 text-[12px] font-bold text-primary-700 transition-smooth hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <AppIcon className={updateDrawer.saving ? 'ri-loader-4-line animate-spin text-sm' : 'ri-save-line text-sm'}></AppIcon>
                      Save without notifying
                    </button>
                    {addedInvitees(selected).length > 0 && (
                      <button
                        type="button"
                        onClick={() => void saveInvitations(selected, { invite: true })}
                        disabled={!updateDrawer.dirty || updateDrawer.saving || Boolean(busy) || Boolean(blockedReason) || detailLoading || !graphConfigured}
                        title="Save these changes, then send the Teams invitation and the schedule email to the people you added. Nobody already invited is contacted, and no session date moves."
                        className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-[12px] font-bold text-white shadow-sm transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <AppIcon className={updateDrawer.saving ? 'ri-loader-4-line animate-spin text-sm' : 'ri-user-add-line text-sm'}></AppIcon>
                        Save and invite added people
                      </button>
                    )}
                  </div>
                </section>

                <p className="text-[11px] font-semibold text-foreground-400">
                  Auto-sync checks this calendar for cancellations every five minutes while this page is open, in this browser only; Sync calendar status checks now. Attendance, transcripts and recordings are collected by the server after a meeting has run — open Sessions & Recordings to see them or to ask for them sooner.
                </p>
              </div>
            ) : (
              /* No calendar yet, so this dialog *is* the create form: the dates
                 it will be built on, the settings Teams needs, and one Create
                 at the end. There is no second drawer to step through.

                 The form itself is the shared one, not a copy of it. This page
                 and the Module Builder are two doors onto the same record, and
                 while each kept its own field list they drifted -- this one lost
                 the "keep each session's own length" duration option and the
                 series-and-links choice, so a length picked here silently
                 overrode every session with no way back. */
              createProgress || (createRecovery && createRecovery.phase !== 'failed') ? (
                /* Once the review is confirmed the create is under way, and the
                   form has nothing more to ask: its progress takes its place. */
                <CreateProgressPanel
                  moduleName={selected.name}
                  sessionCount={(selectedForDisplay || selected).sessions.length}
                  progress={createProgress}
                  recovery={createRecovery}
                  busy={createDrawer.saving}
                  onCheckAgain={() => void createCalendar(selected, { checkOnly: true })}
                  onConfirmUncertain={() => void createCalendar(selected, { confirmUncertain: true })}
                />
              ) : (
              <div className="space-y-4">
                {/* The Microsoft time zone is already stated once at the top of
                    the page, so it is not repeated inside the dialog. */}
                <TeamsCalendarFormBody
                  /* Named after the module's own live sessions, the same as the
                     schedule an existing calendar shows. */
                  row={selectedForDisplay || selected}
                  form={createDrawer.form}
                  patch={createDrawer.patch}
                  holidayLabelFor={holidayLabelFor}
                  prefilling={invitedPrefilling}
                  prefillNotice={prefillNotice}
                  onPrefill={() => void prefillInvitees(selected, createDrawer.patch)}
                />
                {createRecovery?.phase === 'failed' ? (
                  <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
                    <p className="font-semibold">{createRecovery.message}</p>
                  </div>
                ) : createDrawer.error && <InlineError message={createDrawer.error} />}
              </div>
              )
            )}
            </>)}
          </Modal>
        )}

      {forwardOpen && selected?.summary && (
        <Modal
          title="Email the schedule"
          size="max-w-xl"
          onClose={() => { if (!forwardSaving) setForwardOpen(false); }}
          footer={(
            <div className="flex w-full items-center justify-end gap-2">
              <button type="button" onClick={() => setForwardOpen(false)} disabled={forwardSaving}
                className="rounded-lg border border-background-200 bg-background-50 px-3 py-2 text-[12px] font-bold text-foreground-700">Cancel</button>
              <button type="button" onClick={() => void submitForward()} disabled={forwardSaving || !emailList(forwardRecipients).length}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-2 text-[12px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50">
                <AppIcon className={forwardSaving ? 'ri-loader-4-line animate-spin text-sm' : 'ri-send-plane-line text-sm'}></AppIcon>
                {forwardSaving ? 'Sending...' : 'Send email'}
              </button>
            </div>
          )}
        >
          <div className="space-y-4">
            <p className="text-sm text-foreground-600">Email the current timetable, session details and Teams join links from the saved calendar. This is an LMS email: it does not add the meeting to anyone's Outlook calendar or invite them to it in Teams.</p>
            <FormField label="Recipients" hint="Search your Microsoft 365 tenant or type full email addresses. You can add more than one.">
              <EntraPeopleInput label="Forward recipients" value={forwardRecipients} onChange={setForwardRecipients} />
            </FormField>
            {forwardError && <InlineError message={forwardError} />}
            {forwardNotice && <p role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm font-semibold text-emerald-800">{forwardNotice}</p>}
          </div>
        </Modal>
      )}

      {preview && (
        <RecordingPreview
          liveSessionId={preview.liveSessionId}
          artifact={preview.artifact}
          title={preview.title}
          onClose={() => setPreview(null)}
        />
      )}

      {transcriptPreview && (
        <TranscriptPreview
          liveSessionId={transcriptPreview.liveSessionId}
          artifact={transcriptPreview.artifact}
          title={transcriptPreview.title}
          onClose={() => setTranscriptPreview(null)}
        />
      )}

      <EntityDrawer
        open={settingsDrawer.open}
        title="Meeting settings"
        subtitle={drawerTarget
          ? `How ${drawerTarget.name} runs in Teams. Saving updates the existing meeting: its join link and every session date stay exactly as they are.`
          : undefined}
        onClose={settingsDrawer.close}
        onSubmit={saveSettings}
        submitLabel="Save meeting settings"
        saving={settingsDrawer.saving}
        error={settingsDrawer.error}
        dirty={settingsDrawer.dirty}
      >
        <MeetingSettingsFields form={settingsDrawer.form} patch={settingsDrawer.patch} />
        <p className="text-[11px] font-semibold leading-relaxed text-foreground-500">
          The organiser cannot be changed after the meeting is created. Language and automatic recording apply from the next session; a session already in progress keeps the settings it started with.
        </p>
      </EntityDrawer>
    </>
  );
}
