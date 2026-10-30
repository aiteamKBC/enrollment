import { finishTeamsCreation, finishTeamsUpdate } from '../creationResult';
import { syncTeamsCalendarState } from '../calendarState';
import { calendarAction } from '../calendarActions';
import { compareTeamsAttendees } from '../attendeeComparison';
import { loadModuleStructure, loadTeamsMeetingArtifacts, loadTeamsMeetingConfiguration, type ModuleCatalogueItem, type ModuleComponent } from '../../module-builder/moduleAuthoringData';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type {
  CurriculumCohort,
  CurriculumGroup,
  CurriculumModule,
  CurriculumProgramme,
  CurriculumSession,
  CurriculumTeamsMeetingSummary,
} from '@/lib/curriculumApi';

/**
 * The point of this page is that the module's own session dates are the
 * authority: it has to say when the Teams calendar disagrees with them, and
 * pressing the button has to send those exact dates rather than a plain weekly
 * recurrence Graph invented for itself. A holiday landing on one of those dates
 * warns and changes nothing, so it never alters what is sent.
 */

vi.mock('@/components/feature/WorkspaceShell', () => ({
  WorkspaceShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/feature/CurriculumSweetAlert', () => ({
  showCurriculumAlert: vi.fn(async () => undefined),
  showCurriculumConfirm: vi.fn(async () => undefined),
  showCurriculumLoading: vi.fn(),
  closeCurriculumLoading: vi.fn(),
}));

const programmes = [
  { id: 'programme-data', sourceId: 'PROG-DATA', name: 'Data Analyst', level: '4' },
] as CurriculumProgramme[];

const cohorts = [
  { id: 'COHORT-1', name: 'Sept 2026', programmeId: 'PROG-DATA', programme: 'Data Analyst', status: 'active' },
] as unknown as CurriculumCohort[];

const groups = [
  {
    id: 'GROUP-1', name: 'Group A', cohortId: 'COHORT-1', cohort: 'Sept 2026',
    programme: 'Data Analyst', coach: 'Coach One', weekDays: 'Wednesday',
    startTime: '09:30', endTime: '11:30', status: 'active',
  },
] as unknown as CurriculumGroup[];

const modules = [
  { id: 'MOD-1', moduleCatalogueId: 'MOD-1', name: 'Data Foundations', groupId: 'GROUP-1', weeks: 2, sessionsNumber: 2, status: 'published' },
  { id: 'MOD-2', moduleCatalogueId: 'MOD-2', name: 'Risk Management', groupId: 'GROUP-1', weeks: 2, sessionsNumber: 2, status: 'published' },
  { id: 'MOD-4', moduleCatalogueId: 'MOD-4', name: 'Ops Clinic', groupId: 'GROUP-1', weeks: 1, sessionsNumber: 1, status: 'published' },
  {
    id: 'MOD-3', moduleCatalogueId: 'MOD-3', name: 'Reporting Basics', groupId: 'GROUP-1',
    weeks: 1, sessionsNumber: 1, status: 'draft',
    // The API appends these hidden lines to every module's notes.
    notes: [
      'Bring the reporting template to the first session.',
      '__program_id:PROG-DATA',
      '__cohort_id:COHORT-1',
      '__group_id:GROUP-1',
      '__group_name:Group A',
      '__module_catalogue_id:MOD-3',
    ].join('\n'),
  },
] as unknown as CurriculumModule[];

function session(moduleId: string, date: string, extra: Partial<CurriculumSession> = {}) {
  return {
    id: `${moduleId}-${date}`, moduleCatalogueId: moduleId, moduleId,
    title: 'Live session', type: 'live-session', date, day: 'Wednesday',
    startTime: '09:30', endTime: '11:30', tutor: 'Tutor One',
    group: 'Group A', cohort: 'Sept 2026', programme: 'Data Analyst',
    venue: 'Online', module: moduleId, week: 1, ...extra,
  } as unknown as CurriculumSession;
}

// 09:30 in Europe/London during BST is 08:30 UTC — the instant Teams holds.
const sessions: CurriculumSession[] = [
  session('MOD-1', '2026-09-02'),
  session('MOD-1', '2026-09-09'),
  session('MOD-2', '2026-09-03'),
  // A session sitting ON a holiday, exactly as the backend generator leaves it
  // now that the clash rule is parked: the date is the session's own, the field
  // names the holiday that falls on it, and the page warns and stops there.
  session('MOD-2', '2026-09-17', { skippedHolidays: ['2026-09-17'] }),
  session('MOD-3', '2026-09-04'),
  // Right day, wrong hour: the calendar entry sits an hour off the module.
  session('MOD-4', '2026-09-08'),
];

// A successful read with no authored links is different from a failed/null
// response: only the former can safely offer to create a module calendar.
function savedModule(catalogueId: string, components: ModuleComponent[] = []): ModuleCatalogueItem {
  return {
    catalogueId, title: modules.find(module => module.moduleCatalogueId === catalogueId)?.name || 'Module',
    weekStructure: components.map((component, index) => ({
      id: component.weekId, weekNumber: index + 1, title: `Week ${index + 1}`, components: [component],
    })),
  } as ModuleCatalogueItem;
}

function linkedComponent(url: string): ModuleComponent {
  return {
    id: 'LINKED-COMP-1', weekId: 'LINKED-WEEK-1', type: 'live-session', title: 'Existing channel session',
    description: '', expectedOtjh: 2, points: 0, reflectionRequired: false, reflectionQuestion: '',
    workplaceEvidenceRequired: false, tutorValidationRequired: false, coachValidationRequired: true,
    ksbMappings: [], settings: { liveSessionUrl: url, teamsMeetingScope: 'main' },
  };
}

const summaries: CurriculumTeamsMeetingSummary[] = [
  {
    moduleCatalogueId: 'MOD-1', liveSessionId: 'LIVE-1', status: 'active',
    meetingType: 'live-session', calendarManagement: 'lms',
    joinUrl: 'https://teams.microsoft.com/l/meetup-join/one', organizerEmail: 'tutor@example.com',
    eventId: 'event-1', presenters: ['tutor@example.com'],
    // Two, so the head count in the dialog has names to open onto.
    attendees: ['learner@example.com', 'apprentice@example.com'],
    repeatPattern: 'weekly', startDateTime: '2026-09-02T08:30:00Z', durationMinutes: 120,
    occurrenceCount: 2, upcomingCount: 2, syncedCount: 0, nextOccurrence: '2026-09-02T08:30:00Z',
    updatedAt: '2026-08-20T10:00:00Z',
    occurrenceDates: ['2026-09-02T08:30:00Z', '2026-09-09T08:30:00Z'],
    // The backend's own verdict — the source of truth this page now displays
    // rather than a comparison it derives itself against the (possibly
    // cached) session list. Both sides agree here.
    syncState: 'in-sync', expectedOccurrenceCount: 2, differingOccurrenceCount: 0,
    missingFromTeams: [], extraInTeams: [],
  },
  {
    moduleCatalogueId: 'MOD-2', liveSessionId: 'LIVE-2', status: 'active',
    meetingType: 'live-session', calendarManagement: 'lms',
    joinUrl: 'https://teams.microsoft.com/l/meetup-join/two', organizerEmail: 'tutor@example.com',
    eventId: 'event-2', presenters: [], attendees: ['learner@example.com'],
    repeatPattern: 'weekly', startDateTime: '2026-09-03T08:30:00Z', durationMinutes: 120,
    occurrenceCount: 2, upcomingCount: 2, syncedCount: 0, nextOccurrence: '2026-09-03T08:30:00Z',
    updatedAt: '2026-08-20T10:00:00Z',
    // Teams is holding a date of its own, which is what makes this row differ.
    occurrenceDates: ['2026-09-03T08:30:00Z', '2026-09-10T08:30:00Z'],
    syncState: 'out-of-sync', expectedOccurrenceCount: 2, differingOccurrenceCount: 1,
    missingFromTeams: ['2026-09-17T08:30:00Z'], extraInTeams: ['2026-09-10T08:30:00Z'],
  },
  {
    moduleCatalogueId: 'MOD-4', liveSessionId: 'LIVE-4', status: 'active',
    meetingType: 'live-session', calendarManagement: 'lms',
    joinUrl: 'https://teams.microsoft.com/l/meetup-join/four', organizerEmail: 'tutor@example.com',
    eventId: 'event-4', presenters: [], attendees: [],
    repeatPattern: 'none', startDateTime: '2026-09-08T07:30:00Z', durationMinutes: 120,
    occurrenceCount: 1, upcomingCount: 1, syncedCount: 0, nextOccurrence: '2026-09-08T07:30:00Z',
    updatedAt: '2026-08-20T10:00:00Z',
    occurrenceDates: ['2026-09-08T07:30:00Z'],
    // Right day, wrong hour: still one occurrence that does not match.
    syncState: 'out-of-sync', expectedOccurrenceCount: 1, differingOccurrenceCount: 1,
    missingFromTeams: ['2026-09-08T08:30:00Z'], extraInTeams: ['2026-09-08T07:30:00Z'],
  },
];

import { showCurriculumAlert, showCurriculumConfirm } from '@/components/feature/CurriculumSweetAlert';

const confirmMock = vi.mocked(showCurriculumConfirm);
const alertMock = vi.mocked(showCurriculumAlert);
vi.mock('../creationResult', () => ({ finishTeamsCreation: vi.fn(), finishTeamsUpdate: vi.fn() }));
vi.mock('../calendarState', () => ({ syncTeamsCalendarState: vi.fn() }));
vi.mock('../calendarActions', () => ({ calendarAction: vi.fn() }));
// Only the Microsoft read is replaced; the pure helpers beside it are the real
// ones, so the panel's "not published yet" list is the shipped logic.
vi.mock('../attendeeComparison', async importOriginal => ({
  ...(await importOriginal<typeof import('../attendeeComparison')>()),
  compareTeamsAttendees: vi.fn(),
}));

// Every week already has its live session unless a test says otherwise.
const probeModuleTeamsAttachment = vi.fn(async () => 0);
const fetchCurriculumTeamsMeetingSummaries = vi.fn(async () => summaries);
const fetchCurriculumSessions = vi.fn<typeof import('@/lib/curriculumApi').fetchCurriculumSessions>(async () => sessions);
const fetchModuleSessionPlan = vi.fn(async (moduleId: string) => ({
  sessions: sessions
    .filter(item => item.moduleCatalogueId === moduleId)
    .map((item, index) => ({
      sessionNumber: index + 1,
      date: item.date,
      day: item.day,
      startTime: item.startTime,
      endTime: item.endTime,
      durationMinutes: 120,
      skippedHolidays: item.skippedHolidays || [],
    })),
  skippedHolidays: [], finalEndDate: '', warnings: [],
}));

vi.mock('@/lib/curriculumApi', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/curriculumApi')>()),
  fetchImportedMeetingParticipants: vi.fn(async () => ({
    checkedAt: '2026-10-19T14:00:00Z',
    calendar: { status: 'unavailable', message: 'Not available' },
    roles: { status: 'access_denied', message: 'Roles unavailable' },
  })),
  fetchCurriculumTeamsMeetingSummaries: (...args: unknown[]) => fetchCurriculumTeamsMeetingSummaries(...(args as [])),
  fetchCurriculumSessions: (...args: unknown[]) => fetchCurriculumSessions(...(args as [])),
}));

const updateTeamsMeetingSchedule = vi.fn(async () => ({
  updated: true,
  meeting: {} as never,
  warnings: [],
}));
const fetchModuleMeetingInvitees = vi.fn(async (_moduleId: string) => ({
  attendees: [],
  presenters: ['mahmoudfouda015@gmail.com'],
}));
const createTeamsMeeting = vi.fn(async () => ({ created: true, meeting: {} as never, warnings: [] }));
const fetchTeamsCreateStatus = vi.fn(async () => ({ state: 'none', claim: null, calendar: null }));
const restoreModuleTeamsMeeting = vi.fn(async () => ({
  restored: true, updatedComponents: 0, createdComponents: 1, meeting: {}, module: {},
}));
const saveTeamsRecordingEvents = vi.fn(async () => ({ saved: 1, previewSessionId: 'preview-1' }));
const syncTeamsMeetingArtifacts = vi.fn(async () => ({
  synced: { attendanceReports: 1, attendanceRecords: 3, transcripts: 1, recordings: 1 },
  errors: [], partial: false,
}));

// One meeting that has already run, so its recording is there to be watched.
const artifacts = {
  series: { id: 'LIVE-1', module_title: 'Data Foundations', organizer_email: 'tutor@example.com', join_url: '', online_meeting_id: 'meeting-1' },
  occurrences: [
    {
      id: 'OCC-1', session_number: 1, scheduled_start: '2026-09-02T08:30:00Z', scheduled_end: '2026-09-02T10:30:00Z',
      participant_count: 3, status: 'held', attendance: [],
      artifacts: [{ id: 'ART-REC-1', artifact_type: 'recording' }, { id: 'ART-VTT-1', artifact_type: 'transcript' }],
    },
  ],
};

// Only the calls that reach Microsoft are stubbed; the timezone maths this page
// relies on is the real implementation.
vi.mock('../../module-builder/moduleAuthoringData', async importOriginal => ({
  ...(await importOriginal<typeof import('../../module-builder/moduleAuthoringData')>()),
  loadTeamsMeetingConfiguration: vi.fn(async () => ({
    configured: true, defaultOrganizer: 'tutor@example.com',
    timeZone: 'GMT Standard Time', timeZoneIana: 'Europe/London',
  })),
  loadTeamsMeetingArtifacts: vi.fn(async () => artifacts),
  fetchModuleSessionPlan: (...args: unknown[]) => fetchModuleSessionPlan(...(args as [string])),
  fetchModuleMeetingInvitees: (...args: unknown[]) => fetchModuleMeetingInvitees(...(args as [string])),
  loadModuleStructure: vi.fn(async (catalogueId: string) => savedModule(catalogueId)),
  syncTeamsMeetingArtifacts: (...args: unknown[]) => syncTeamsMeetingArtifacts(...(args as [])),
  restoreModuleTeamsMeeting: (...args: unknown[]) => restoreModuleTeamsMeeting(...(args as [])),
  probeModuleTeamsAttachment: (...args: unknown[]) => probeModuleTeamsAttachment(...(args as [])),
  updateTeamsMeetingSchedule: (...args: unknown[]) => updateTeamsMeetingSchedule(...(args as [])),
  createTeamsMeeting: (...args: unknown[]) => createTeamsMeeting(...(args as [])),
  fetchTeamsCreateStatus: (...args: unknown[]) => fetchTeamsCreateStatus(...(args as [])),
  saveTeamsRecordingEvents: (...args: unknown[]) => saveTeamsRecordingEvents(...(args as [])),
}));

const holidays = [
  { id: 'HOL-1', label: 'Autumn closure', startDate: '2026-09-17', endDate: '2026-09-17' },
];

vi.mock('@/hooks/useCurriculumEntities', () => ({
  useCurriculumEntities: () => ({
    programmes, cohorts, groups, modules, holidays,
    tutors: [], coaches: [], teamsMeetings: [],
    entities: {}, loading: false, loaded: true, error: null,
    reload: vi.fn(async () => null),
  }),
}));

async function renderPage() {
  const { default: Page } = await import('../page');
  return render(
    <MemoryRouter initialEntries={['/curriculum/teams-meetings']}>
      <Routes>
        <Route path="/curriculum/teams-meetings" element={<Page />} />
      </Routes>
    </MemoryRouter>,
  );
}

function rowFor(name: string) {
  return screen.getByText(name).closest('div[class*="grid-cols"]') as HTMLElement;
}

describe('Teams Meetings page', () => {
  beforeEach(() => {
    vi.mocked(loadModuleStructure).mockReset();
    vi.mocked(loadModuleStructure).mockImplementation(async catalogueId => savedModule(catalogueId));
    vi.mocked(syncTeamsCalendarState).mockReset();
    vi.mocked(syncTeamsCalendarState).mockResolvedValue({ changed: false, seriesStatus: 'active', cancelledSessions: [], errors: [] });
    vi.mocked(loadTeamsMeetingArtifacts).mockReset();
    vi.mocked(loadTeamsMeetingArtifacts).mockResolvedValue(artifacts as never);
    updateTeamsMeetingSchedule.mockClear();
    fetchModuleMeetingInvitees.mockClear();
    createTeamsMeeting.mockClear();
    fetchTeamsCreateStatus.mockClear();
    restoreModuleTeamsMeeting.mockClear();
    saveTeamsRecordingEvents.mockClear();
    syncTeamsMeetingArtifacts.mockClear();
    fetchCurriculumTeamsMeetingSummaries.mockClear();
    fetchCurriculumTeamsMeetingSummaries.mockImplementation(async () => summaries);
    fetchCurriculumSessions.mockReset();
    fetchCurriculumSessions.mockResolvedValue(sessions);
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(false);
    probeModuleTeamsAttachment.mockClear();
    probeModuleTeamsAttachment.mockResolvedValue(0);
    // Cleared like the rest: without this the call count is cumulative across
    // the file, so any test asserting on what a click confirmed counts every
    // earlier test's alerts too.
    alertMock.mockClear();
    vi.mocked(finishTeamsCreation).mockClear();
    vi.mocked(finishTeamsUpdate).mockClear();
    window.localStorage.removeItem('curriculumTeamsAutoSync');
  });

  it('reports a calendar that matches the module session plan as in sync', async () => {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    expect(within(rowFor('Data Foundations')).getByText('In sync')).toBeInTheDocument();
    // The dates themselves are what the comparison needs, so they are asked for.
    expect(fetchCurriculumTeamsMeetingSummaries).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ occurrenceDates: true }),
    );
  });

  it('opens cancellation review for the selected series without sending an action', async () => {
    vi.mocked(calendarAction).mockClear();
    window.localStorage.setItem('curriculumTeamsAutoSync', 'off');
    await renderPage();
    await screen.findByText('Data Foundations');
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Cancel series' })).toBeEnabled());
    await userEvent.click(dialog.getByRole('button', { name: 'Cancel series' }));
    const actionDialog = within(await screen.findByRole('dialog'));
    expect(actionDialog.getByText(/Data Foundations/)).toBeInTheDocument();
    expect(actionDialog.getByText('The entire calendar series will be cancelled.')).toBeInTheDocument();
    expect(actionDialog.getByRole('button', { name: 'Review changes' })).toBeInTheDocument();
    expect(calendarAction).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('checks future calendars for cancellation without creating or updating Microsoft meetings', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-08-01T08:00:00Z'));
    try {
      await renderPage();
      await waitFor(() => expect(syncTeamsCalendarState).toHaveBeenCalledWith('LIVE-4'));
      expect(syncTeamsCalendarState).toHaveBeenCalledTimes(3);
      expect(syncTeamsMeetingArtifacts).not.toHaveBeenCalled();
      expect(createTeamsMeeting).not.toHaveBeenCalled();
      expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
      expect(finishTeamsCreation).not.toHaveBeenCalled();
    } finally { clock.mockRestore(); }
  });

  it('honours auto-sync off, and a manual check records eligible Microsoft cancellations locally', async () => {
    window.localStorage.setItem('curriculumTeamsAutoSync', 'off');
    await renderPage();
    await screen.findByText('Data Foundations');
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(syncTeamsCalendarState).not.toHaveBeenCalled();
    // Confirmed cancellations are applied only to sessions without attendance
    // evidence; the sync result tells the workspace which sessions changed.
    vi.mocked(syncTeamsCalendarState).mockResolvedValueOnce({ changed: true, seriesStatus: 'active', cancelledSessions: [1, 2], errors: [],
      seriesCancelledInMicrosoft: false });
    await userEvent.click(dialog.getByRole('button', { name: 'Sync calendar status' }));
    await waitFor(() => expect(syncTeamsCalendarState).toHaveBeenCalledWith('LIVE-1'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Record the series cancellation' })).not.toBeInTheDocument();
    expect(syncTeamsMeetingArtifacts).not.toHaveBeenCalled();
    expect(createTeamsMeeting).not.toHaveBeenCalled();
  });

  it('rechecks cancellation at five minutes without polling on every render', async () => {
    const start = Date.parse('2026-08-01T08:00:00Z');
    const clock = vi.spyOn(Date, 'now').mockReturnValue(start);
    let tick = () => {};
    const realInterval = window.setInterval.bind(window);
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, milliseconds, ...args) => {
      if (milliseconds === 60000) tick = handler as () => void;
      return realInterval(handler, milliseconds, ...args);
    });
    try {
      await renderPage();
      await waitFor(() => expect(syncTeamsCalendarState).toHaveBeenCalledTimes(3));
      clock.mockReturnValue(start + 4 * 60000);
      await act(async () => tick());
      expect(syncTeamsCalendarState).toHaveBeenCalledTimes(3);
      clock.mockReturnValue(start + 5 * 60000);
      await act(async () => tick());
      await waitFor(() => expect(syncTeamsCalendarState).toHaveBeenCalledTimes(6));
    } finally { interval.mockRestore(); clock.mockRestore(); }
  });

  it('keeps a cancelled session on its own row without borrowing the next session join link', async () => {
    window.localStorage.setItem('curriculumTeamsAutoSync', 'off');
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-08-01T08:00:00Z'));
    vi.mocked(loadTeamsMeetingArtifacts).mockResolvedValue({ ...artifacts, occurrences: [
      { ...artifacts.occurrences[0], status: 'cancelled', participant_count: 0, artifacts: [], join_url: 'https://teams.microsoft.com/meet/cancelled' },
      { ...artifacts.occurrences[0], id: 'OCC-2', session_number: 2, status: 'scheduled', scheduled_start: '2026-09-09T08:30:00Z', scheduled_end: '2026-09-09T10:30:00Z', participant_count: 0, artifacts: [], join_url: 'https://teams.microsoft.com/meet/second' },
    ] } as never);
    try {
      await renderPage();
      await screen.findByText('Data Foundations');
      await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
      const dialog = within(await screen.findByRole('dialog'));
      expect(await dialog.findByText('Cancelled')).toBeInTheDocument();
      const joins = dialog.getAllByRole('link', { name: /^Join Teams$/ });
      expect(joins).toHaveLength(1);
      expect(joins[0]).toHaveAttribute('href', 'https://teams.microsoft.com/meet/second');
    } finally { clock.mockRestore(); }
  });

  it('shows a failed calendar check without removing the series or sending changes', async () => {
    vi.mocked(syncTeamsCalendarState).mockRejectedValue(new Error('Calendar status could not be checked.'));
    await renderPage();
    const failure = await screen.findByText(/Calendar status could not be checked\./);
    // The sweep runs across every calendar on the page, so the module it failed
    // on is named rather than left for the reader to guess.
    expect(failure).toHaveTextContent(/^.+ — Calendar status could not be checked\.$/);
    expect(within(rowFor('Data Foundations')).getByText('In sync')).toBeInTheDocument();
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
    expect(syncTeamsMeetingArtifacts).not.toHaveBeenCalled();
  });

  it('keeps an unrelated background matching warning out of the create dialog', async () => {
    const message = 'Session 11 could not be matched to Microsoft; its status was preserved.';
    let finishCheck!: (value: Awaited<ReturnType<typeof syncTeamsCalendarState>>) => void;
    vi.mocked(syncTeamsCalendarState).mockImplementation(async id => id === 'LIVE-2'
      ? new Promise(resolve => { finishCheck = resolve; })
      : { changed: false, seriesStatus: 'active', cancelledSessions: [], errors: [] });
    await renderPage();
    await screen.findByText('Reporting Basics');
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() => expect(finishCheck).toBeTypeOf('function'));
    await act(async () => finishCheck({ changed: false, seriesStatus: 'active', cancelledSessions: [], errors: [message] }));
    expect(dialog.queryByText(new RegExp(message))).not.toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Create' })).toBeEnabled();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText(new RegExp(message))).toHaveTextContent('Risk Management');
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('keeps a matching warning visible in the dialog for the calendar it belongs to', async () => {
    const message = 'Session 11 could not be matched to Microsoft; its status was preserved.';
    let finishCheck!: (value: Awaited<ReturnType<typeof syncTeamsCalendarState>>) => void;
    vi.mocked(syncTeamsCalendarState).mockImplementation(async id => id === 'LIVE-1'
      ? new Promise(resolve => { finishCheck = resolve; })
      : { changed: false, seriesStatus: 'active', cancelledSessions: [], errors: [] });
    await renderPage();
    await screen.findByText('Data Foundations');
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() => expect(finishCheck).toBeTypeOf('function'));
    await act(async () => finishCheck({ changed: false, seriesStatus: 'active', cancelledSessions: [], errors: [message] }));
    expect(dialog.getByText(new RegExp(message))).toHaveTextContent('Data Foundations');
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('names the sessions whose Teams date no longer matches the module', async () => {
    await renderPage();
    expect(await screen.findByText('Risk Management')).toBeInTheDocument();
    const row = within(rowFor('Risk Management'));
    expect(row.getByText('Dates differ')).toBeInTheDocument();
    expect(row.getByText('1 session differs')).toBeInTheDocument();
  });

  // Rewritten with the clash rule: a holiday warns and does nothing else, so
  // the red "blocked" row and its green "replacement" partner this used to
  // assert are gone. One ordinary row, with a warning under it.
  it('warns on the session a holiday falls on, and gives it no second date', async () => {
    await renderPage();
    expect(await screen.findByText('Risk Management')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

    const dialog = await screen.findByRole('dialog');
    // The session keeps its own date, and the holiday is named under it.
    expect(within(dialog).getByText('17 Sept 2026, 9:30 AM').closest('div')?.parentElement)
      .toHaveTextContent('Heads up: this session falls on a holiday (Autumn closure). It runs as planned.');
    // Nothing is blocked, replaced, moved or skipped.
    expect(within(dialog).queryByText('Shifted to replacement')).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/Blocked by/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText('Replacement delivered')).not.toBeInTheDocument();
    // The session numbers the holiday note talks about are findable in the list.
    expect(within(dialog).getByText('Session 1')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Session 2').length).toBeGreaterThan(0);
  });

  // Rewritten with the clash rule: there is no cascade left to spell out. What
  // the note has to say now is the opposite — a holiday is here, and it costs
  // the plan nothing.
  it('names the holiday above the dates, and says it changes nothing', async () => {
    await renderPage();
    expect(await screen.findByText('Risk Management')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/inside this module/))
      .toHaveTextContent('Autumn closure falls on 17 Sept 2026');
    expect(within(dialog).getByText(/Every session keeps its own date/))
      .toHaveTextContent('none is moved or dropped');
    // The badge counts dates a holiday lands on, not sessions it displaced.
    expect(within(dialog).getByText('1 session on a holiday')).toBeInTheDocument();
    // The parked cascade is not stated anywhere.
    expect(within(dialog).queryByText(/moves to the next delivery day/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/runs later/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/moved by holidays/)).not.toBeInTheDocument();
  });

  it('separates a calendar entry on the wrong day from one on the wrong hour', async () => {
    await renderPage();
    expect(await screen.findByText('Ops Clinic')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Ops Clinic')).getByRole('button', { name: 'Detail' }));

    // “Will be moved” beside an unchanged date reads as a mistake, so a
    // calendar entry that is only an hour out says exactly that.
    const clinic = await screen.findByRole('dialog');
    expect(within(clinic).getByText(/Right day, wrong time/))
      .toHaveTextContent('Teams still holds 8:30 AM; sending moves it here.');
    // …and the note is only worth reading next to what makes it happen.
    expect(within(clinic).getByText(/Nothing on the Teams calendar changes/))
      .toHaveTextContent('until you press Update Teams calendar');
    await userEvent.click(within(clinic).getByRole('button', { name: 'Close' }));

    await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));
    const risk = await screen.findByRole('dialog');
    // Only the difference is written down. The module's date is already on the
    // row, so a calendar entry that agrees with it says nothing at all — the
    // "same as the module" line used to repeat that fact once per session and
    // buried the one row that had actually moved.
    expect(within(risk).queryByText(/nothing to change/)).not.toBeInTheDocument();
    expect(within(risk).getByText('Teams still holds 10 Sept 2026, 9:30 AM; sending moves it here.'))
      .toBeInTheDocument();
  });

  it('offers the way into each meeting, and says so when the session is over', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-09-01T09:00:00Z'));
    try {
      await renderPage();
      expect(await screen.findByText('Risk Management')).toBeInTheDocument();
      await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

      const dialog = await screen.findByRole('dialog');
      const join = within(dialog).getAllByRole('link', { name: /Join/ });
      expect(join.length).toBeGreaterThan(0);
      join.forEach(link => expect(link).toHaveAttribute('href', 'https://teams.microsoft.com/l/meetup-join/two'));
      expect(within(dialog).queryByText('Session ended')).not.toBeInTheDocument();
    } finally {
      clock.mockRestore();
    }
  });

  it('closes the join door once a session\u2019s end time has passed', async () => {
    // The dialog reads the clock, so the clock is what the test moves: both of
    // this module's meetings are over by the time the page renders.
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-01T09:00:00Z'));
    try {
      await renderPage();
      expect(await screen.findByText('Risk Management')).toBeInTheDocument();
      await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).getAllByText('Session ended')).toHaveLength(2);
      expect(within(dialog).queryByRole('link', { name: 'Join Teams' })).not.toBeInTheDocument();
    } finally {
      clock.mockRestore();
    }
  });

  /**
   * A filled-in Detail button marks a row that is waiting on somebody. Colour
   * cannot be looked up, so the row and the toolbar both say what it means.
   */
  it('says what a highlighted Detail button means, on the button and above the table', async () => {
    await renderPage();
    expect(await screen.findByText('Risk Management')).toBeInTheDocument();

    // Risk Management's calendar disagrees with its module dates.
    expect(within(rowFor('Risk Management')).getByText('Dates differ')).toBeInTheDocument();
    expect(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }))
      .toHaveAttribute('title', expect.stringContaining('Needs attention'));

    // A row that agrees says so rather than saying nothing.
    expect(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }))
      .toHaveAttribute('title', expect.stringContaining('Up to date'));

    // And the convention is decoded once, where the highlight is.
    expect(screen.getByText(/highlighted Detail button/)).toBeInTheDocument();
  });

  /**
   * The footer used to offer all three actions in every state, so a module with
   * every session attached and none of them run yet showed two buttons that
   * would have changed nothing. What is left appears only where it can act.
   */
  describe('the footer only offers what the module can actually do', () => {
    it('shows the update action alone when nothing is missing and nothing has run', async () => {
      await renderPage();
      expect(await screen.findByText('Risk Management')).toBeInTheDocument();
      await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

      const dialog = within(await screen.findByRole('dialog'));
      expect(dialog.getByRole('button', { name: 'Update Teams calendar' })).toBeInTheDocument();
      await waitFor(() => expect(probeModuleTeamsAttachment).toHaveBeenCalled());
      expect(dialog.queryByRole('button', { name: /missing live session/ })).not.toBeInTheDocument();
      // Collecting attendance and files is the durable worker's job, so the row
      // does not offer a button whose only effect is to queue what is queued
      // already. The sessions themselves are reached through a plain link.
      expect(dialog.queryByRole('button', { name: 'Sync attendance & files' })).not.toBeInTheDocument();
      // Sessions & Recordings opens from the module's own component, not from here.
      expect(dialog.queryByRole('button', { name: 'Sessions & Recordings' })).not.toBeInTheDocument();
      expect(dialog.getByRole('switch', { name: 'Auto-sync on' })).toHaveAttribute('aria-checked', 'true');
    });

    it('does not offer to add missing live sessions from the dialog', async () => {
      probeModuleTeamsAttachment.mockResolvedValue(3);
      await renderPage();
      expect(await screen.findByText('Risk Management')).toBeInTheDocument();
      await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

      const dialog = within(await screen.findByRole('dialog'));
      // Missing live sessions are added from the module itself, not from here.
      await waitFor(() => expect(probeModuleTeamsAttachment).toHaveBeenCalled());
      expect(dialog.queryByRole('button', { name: /missing live session/ })).not.toBeInTheDocument();
    });

    it('leaves an ended session to the worker instead of offering a sync button', async () => {
      const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-01T09:00:00Z'));
      try {
        await renderPage();
        expect(await screen.findByText('Risk Management')).toBeInTheDocument();
        await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

        const dialog = within(await screen.findByRole('dialog'));
        expect(dialog.getAllByText('Session ended').length).toBeGreaterThan(0);
        // The session having ended is exactly when the old button looked most
        // useful and did least: the worker polls every minute regardless.
        expect(dialog.queryByRole('button', { name: 'Sync attendance & files' })).not.toBeInTheDocument();
        expect(dialog.queryByRole('button', { name: 'Sessions & Recordings' })).not.toBeInTheDocument();
      } finally {
        clock.mockRestore();
      }
    });
  });

  /**
   * Asking for attendance and files now lives beside the sessions, in the
   * Sessions & Recordings panel, which queues the same durable job. The meeting
   * dialog itself offers no control that reaches Graph for them.
   */
  it('offers nothing in the meeting dialog that queues artifact work', async () => {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.queryByRole('button', { name: 'Sync attendance & files' })).not.toBeInTheDocument();
    // The calendar check is still here, because it is the one thing this dialog
    // asks Microsoft that nothing else is already doing on a timer.
    expect(dialog.getByRole('button', { name: 'Sync calendar status' })).toBeInTheDocument();
    expect(syncTeamsMeetingArtifacts).not.toHaveBeenCalled();
  });

  /**
   * The toggle used to stay green and clickable while the banner said the
   * credentials were missing, so it claimed a sweep was running when the effect
   * behind it returns on its first line.
   */
  it('says auto-sync is unavailable when Graph credentials are missing', async () => {
    vi.mocked(loadTeamsMeetingConfiguration).mockResolvedValueOnce({
      configured: false, defaultOrganizer: '', timeZone: 'GMT Standard Time', timeZoneIana: 'Europe/London',
    } as never);
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));

    const dialog = within(await screen.findByRole('dialog'));
    const toggle = await dialog.findByRole('switch', { name: 'Auto-sync unavailable' });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('checks calendar state but leaves artifact imports to the background worker', async () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-09-02T11:00:00Z'));
    try {
      await renderPage();
      expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
      await waitFor(() => expect(syncTeamsCalendarState).toHaveBeenCalledWith('LIVE-1'));
      // Artifact transfers moved to the worker: opening a page must never queue or run them.
      expect(syncTeamsMeetingArtifacts).not.toHaveBeenCalled();
    } finally {
      clock.mockRestore();
    }
  });

  it('sends the module’s own session dates to Teams', async () => {
    await renderPage();
    expect(await screen.findByText('Risk Management')).toBeInTheDocument();
    // Every Teams action for a module lives in its dialog, which the row's one
    // button opens — the row itself carries no bank of half-disabled buttons.
    await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));

    const dialog = await screen.findByRole('dialog');
    const send = within(dialog).getByRole('button', { name: 'Update Teams calendar' });
    await waitFor(() => expect(send).not.toBeDisabled());
    await userEvent.click(send);
    // A date change is always announced, by Microsoft and the LMS together:
    // there is no opt-out box to tick, and nothing to forget to tick.
    expect(within(dialog).queryByRole('checkbox', { name: 'Email existing invitees about this update' })).toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm update' }));

    await waitFor(() => expect(updateTeamsMeetingSchedule).toHaveBeenCalledTimes(1));
    const [liveSessionId, input] = updateTeamsMeetingSchedule.mock.calls[0] as unknown as [
      string,
      { scheduledOccurrences: Array<{ sessionNumber: number; startDateTimeUtc: string; durationMinutes: number }>; repeatOccurrences: number; startDateTimeUtc: string; localStartDateTime: string; notifyAttendees?: boolean },
    ];
    expect(input.notifyAttendees).toBe(true);
    // Nothing was edited, so no list or option is sent to overwrite the saved ones.
    for (const key of ['attendees', 'presenters', 'coOrganizers', 'recording', 'lobbyBypass', 'spokenLanguage']) {
      expect(input).not.toHaveProperty(key);
    }
    expect(liveSessionId).toBe('LIVE-2');
    expect(input.localStartDateTime).toBe('2026-09-03T09:30');
    expect(input.repeatOccurrences).toBe(2);
    expect(input.scheduledOccurrences.map(item => item.startDateTimeUtc)).toEqual([
      '2026-09-03T08:30:00.000Z',
      // The module's own date, not the slot Teams is still holding -- and a
      // holiday on that date changes nothing about what is sent.
      '2026-09-17T08:30:00.000Z',
    ]);
    expect(input.scheduledOccurrences.map(item => item.durationMinutes)).toEqual([120, 120]);
  });

  it('updates with the people and options edited under the dates, and emails only who was added', async () => {
    await renderPage();
    expect(await screen.findByText('Risk Management')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    const send = dialog.getByRole('button', { name: 'Update Teams calendar' });
    await waitFor(() => expect(send).not.toBeDisabled());

    // The fields sit in the dialog, filled with what the calendar holds.
    expect(dialog.getByRole('textbox', { name: 'Organizer' })).toHaveValue('tutor@example.com');
    expect(dialog.getByRole('button', { name: 'Remove learner@example.com' })).toBeInTheDocument();

    const presenters = dialog.getByRole('combobox', { name: 'Presenters' });
    fireEvent.change(presenters, { target: { value: 'guest.presenter@example.com' } });
    fireEvent.keyDown(presenters, { key: 'Enter' });
    await userEvent.click(dialog.getByRole('combobox', { name: 'Recording' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Record automatically' }));
    await userEvent.click(send);
    await userEvent.click(dialog.getByRole('button', { name: 'Confirm update' }));

    await waitFor(() => expect(updateTeamsMeetingSchedule).toHaveBeenCalledTimes(1));
    const [, input] = updateTeamsMeetingSchedule.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(input.presenters).toEqual(['guest.presenter@example.com']);
    expect(input.recording).toBe('record');
    // Untouched: the saved attendees and the other options stay as Teams has them.
    expect(input).not.toHaveProperty('attendees');
    expect(input).not.toHaveProperty('lobbyBypass');
    await waitFor(() => expect(finishTeamsUpdate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(finishTeamsUpdate).mock.calls[0][1]).toMatchObject({ liveSessionId: 'LIVE-2', addedPeople: ['guest.presenter@example.com'] });
  });

  it('keeps an attendee-only edit silent when the Teams dates already match', async () => {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    const send = dialog.getByRole('button', { name: 'Update Teams calendar' });
    await waitFor(() => expect(send).not.toBeDisabled());

    const attendees = dialog.getByRole('combobox', { name: 'Attendees' });
    fireEvent.change(attendees, { target: { value: 'new.learner@example.com' } });
    fireEvent.keyDown(attendees, { key: 'Enter' });
    await userEvent.click(send);
    await userEvent.click(dialog.getByRole('button', { name: 'Confirm update' }));

    await waitFor(() => expect(updateTeamsMeetingSchedule).toHaveBeenCalledTimes(1));
    const [, input] = updateTeamsMeetingSchedule.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(input.peopleOnly).toBe(true);
    expect(input.attendees).toEqual(['learner@example.com', 'apprentice@example.com', 'new.learner@example.com']);
    await waitFor(() => expect(finishTeamsUpdate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(finishTeamsUpdate).mock.calls[0][1]).toMatchObject({
      liveSessionId: 'LIVE-1', addedPeople: ['new.learner@example.com'],
    });
  });

  // The detail used to unfold underneath the table, which pushed every row below
  // it off screen and left the reader scrolling to find what they had opened.
  it('opens the module detail in a dialog rather than unfolding it under the table', async () => {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    const row = within(rowFor('Data Foundations'));
    await userEvent.click(row.getByRole('button', { name: 'Detail' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: /Data Foundations/ })).toBeInTheDocument();
    expect(within(dialog).getByText('Module dates sent to Teams')).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  // Presenters, co-organisers and attendees are the editable fields under the
  // dates now; the meeting facts above them show only what nothing else edits.
  it('shows each invited role once, as the editable fields, not again as a read-only fact', async () => {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));

    const dialog = within(await screen.findByRole('dialog'));
    // The Organizer, Repeats and Meetings tracked facts are gone; the
    // organizer is shown in the invitation fields below.
    expect(dialog.queryByText('Repeats')).not.toBeInTheDocument();
    expect(dialog.queryByText('Meetings tracked')).not.toBeInTheDocument();
    expect(dialog.getByRole('textbox', { name: 'Organizer' })).toBeInTheDocument();
    // The old read-only fallback text for an empty role list is gone; the
    // fields below say so in their own way (an empty search box).
    expect(dialog.queryByText(/None — everyone joins as an attendee/)).not.toBeInTheDocument();
    expect(dialog.queryByText('None invited')).not.toBeInTheDocument();
    // Each person appears once, as a removable chip in the fields below --
    // not once there and again in a read-only fact.
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Remove learner@example.com' })).toBeInTheDocument());
    expect(dialog.getAllByText('learner@example.com')).toHaveLength(1);
    expect(dialog.getByRole('button', { name: 'Remove apprentice@example.com' })).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Remove tutor@example.com' })).toBeInTheDocument();
  });

  it('hides legacy presenters and co-organisers from the attendees field', async () => {
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValueOnce([
      {
        ...summaries[0],
        attendees: ['learner@example.com', 'tutor@example.com', 'co@example.com'],
        presenters: ['tutor@example.com'],
        coOrganizers: ['co@example.com'],
      },
      ...summaries.slice(1),
    ]);
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));

    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Remove learner@example.com' })).toBeInTheDocument());
    expect(dialog.getAllByRole('button', { name: 'Remove tutor@example.com' })).toHaveLength(1);
    expect(dialog.getAllByRole('button', { name: 'Remove co@example.com' })).toHaveLength(1);
    expect(dialog.getAllByRole('button', { name: 'Remove learner@example.com' })).toHaveLength(1);
  });

  it('offers to build the calendar for a module that has session dates but no meeting', async () => {
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    const row = within(rowFor('Reporting Basics'));
    expect(row.getByText('Not created')).toBeInTheDocument();
    expect(row.getByRole('button', { name: 'Create Teams meetings calendar' })).toBeInTheDocument();
  });

  it('explains an imported series and offers no replacement or calendar write actions', async () => {
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValue(summaries.map(summary => summary.moduleCatalogueId === 'MOD-1'
      ? { ...summary, meetingType: 'linked-external', calendarManagement: 'external' as const } : summary));
    await renderPage();
    await screen.findByText('Data Foundations');
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByText('Series linked from Teams')).toBeVisible();
    expect(dialog.getByRole('link', { name: 'Open existing meeting' })).toHaveAttribute('href', summaries[0].joinUrl);
    expect(dialog.getByText(/Creating a new module meeting is blocked/)).toBeVisible();
    expect(dialog.getByText('When a week is inserted')).toBeVisible();
    expect(dialog.getByRole('button', { name: 'Reattach saved Teams data' })).toBeVisible();
    for (const name of ['Create', 'Update Teams calendar', 'Cancel series', 'Edit session dates', 'Save without notifying']) {
      expect(dialog.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('uses raw imported series metadata from an older server before offering calendar edits', async () => {
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValue(summaries.map(summary => summary.moduleCatalogueId === 'MOD-1'
      ? { ...summary, meetingType: undefined, calendarManagement: undefined } : summary));
    let release!: (value: Awaited<ReturnType<typeof loadTeamsMeetingArtifacts>>) => void;
    vi.mocked(loadTeamsMeetingArtifacts).mockImplementation(() => new Promise(resolve => { release = resolve; }));
    await renderPage();
    await screen.findByText('Data Foundations');
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByRole('status')).toHaveTextContent(/checking its existing Teams links/);
    expect(dialog.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
    expect(dialog.queryByText('Series linked from Teams')).not.toBeInTheDocument();
    await act(async () => { release({ ...artifacts, series: { ...artifacts.series, meeting_type: 'linked-external' } } as never); });
    expect(await dialog.findByText('Series linked from Teams')).toBeVisible();
    expect(dialog.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
    expect(syncTeamsCalendarState).not.toHaveBeenCalledWith('LIVE-1');
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it.each(['failed', 'unidentified'] as const)('keeps unknown ownership closed after a %s detail read and allows a safe retry', async failure => {
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValue(summaries.map(summary => summary.moduleCatalogueId === 'MOD-1'
      ? { ...summary, meetingType: undefined, calendarManagement: undefined } : summary));
    if (failure === 'failed') vi.mocked(loadTeamsMeetingArtifacts).mockRejectedValue(new Error('Saved series unavailable'));
    await renderPage();
    await screen.findByText('Data Foundations');
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByRole('alert')).toHaveTextContent("The original meeting's calendar ownership could not be checked.");
    expect(dialog.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
    expect(dialog.queryByText('Series linked from Teams')).not.toBeInTheDocument();
    vi.mocked(loadTeamsMeetingArtifacts).mockResolvedValue({
      ...artifacts, series: { ...artifacts.series, meeting_type: 'live-session' },
    } as never);
    await userEvent.click(dialog.getByRole('button', { name: 'Try again' }));
    expect(await dialog.findByRole('button', { name: 'Update Teams calendar' })).toBeVisible();
    expect(dialog.queryByText('Series linked from Teams')).not.toBeInTheDocument();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('does not let a late imported detail response change another selected module', async () => {
    window.localStorage.setItem('curriculumTeamsAutoSync', 'off');
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValue(summaries.map(summary => ({
      ...summary, meetingType: undefined, calendarManagement: undefined,
    })));
    let releaseImported!: (value: Awaited<ReturnType<typeof loadTeamsMeetingArtifacts>>) => void;
    let releaseManaged!: (value: Awaited<ReturnType<typeof loadTeamsMeetingArtifacts>>) => void;
    vi.mocked(loadTeamsMeetingArtifacts).mockImplementation(liveId => new Promise(resolve => {
      if (liveId === 'LIVE-1') releaseImported = resolve;
      else releaseManaged = resolve;
    }));
    await renderPage();
    await screen.findByText('Data Foundations');
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    await waitFor(() => expect(loadTeamsMeetingArtifacts).toHaveBeenCalledWith('LIVE-1'));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Close' }));
    await userEvent.click(within(rowFor('Risk Management')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() => expect(loadTeamsMeetingArtifacts).toHaveBeenCalledWith('LIVE-2'));
    await act(async () => { releaseImported({ ...artifacts, series: { ...artifacts.series, meeting_type: 'linked-external' } } as never); });
    expect(dialog.queryByText('Series linked from Teams')).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
    await act(async () => { releaseManaged({ ...artifacts, series: {
      ...artifacts.series, id: 'LIVE-2', meeting_type: 'live-session',
    } } as never); });
    expect(await dialog.findByRole('button', { name: 'Update Teams calendar' })).toBeVisible();
    expect(dialog.queryByText('Series linked from Teams')).not.toBeInTheDocument();
  });

  it('excludes imported series from automatic calendar checks without touching artifact work', async () => {
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValue(summaries.map(summary => summary.moduleCatalogueId === 'MOD-1'
      ? { ...summary, meetingType: 'linked-external', calendarManagement: 'external' as const } : summary));
    await renderPage();
    await waitFor(() => expect(syncTeamsCalendarState).toHaveBeenCalledWith('LIVE-4'));
    expect(syncTeamsCalendarState).not.toHaveBeenCalledWith('LIVE-1');
    expect(syncTeamsCalendarState).toHaveBeenCalledTimes(2);
    expect(syncTeamsMeetingArtifacts).not.toHaveBeenCalled();
  });

  it('refuses a direct manual status check for an imported series with guidance to its original calendar', async () => {
    window.localStorage.setItem('curriculumTeamsAutoSync', 'off');
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValue(summaries.map(summary => summary.moduleCatalogueId === 'MOD-1'
      ? { ...summary, meetingType: 'linked-external', calendarManagement: 'external' as const } : summary));
    const { useTeamsMeetingsWorkspace } = await import('../useTeamsMeetingsWorkspace');
    function StatusCheckHarness() {
      const workspace = useTeamsMeetingsWorkspace({ scopeModuleId: 'MOD-1', initialSelectedId: 'MOD-1' });
      return <>
        <button type="button" disabled={!workspace.teamsLoaded} onClick={() => void workspace.runCalendarSync('LIVE-1', 'manual')}>Check saved calendar</button>
        {workspace.notice && <p role="status">{workspace.notice.text}</p>}
      </>;
    }
    render(<MemoryRouter><StatusCheckHarness /></MemoryRouter>);
    const check = screen.getByRole('button', { name: 'Check saved calendar' });
    await waitFor(() => expect(check).toBeEnabled());
    await userEvent.click(check);
    expect(await screen.findByRole('status')).toHaveTextContent('Ask the organiser to check dates and cancellations in Teams');
    expect(syncTeamsCalendarState).not.toHaveBeenCalled();
    expect(syncTeamsMeetingArtifacts).not.toHaveBeenCalled();
  });

  it('protects a pasted main link when there is no tracked meeting summary', async () => {
    const originalLink = 'https://teams.microsoft.com/l/meetup-join/pre-lms-channel';
    vi.mocked(loadModuleStructure).mockImplementation(async catalogueId => savedModule(catalogueId,
      catalogueId === 'MOD-3' ? [linkedComponent(originalLink)] : []));
    await renderPage();
    await screen.findByText('Reporting Basics');
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByText('Existing Teams link protected')).toBeVisible();
    expect(dialog.getByRole('link', { name: 'Open existing meeting' })).toHaveAttribute('href', originalLink);
    expect(dialog.getByText(/The original calendar has not been connected to these links yet/)).toBeVisible();
    expect(dialog.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Reattach saved Teams data' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('combobox', { name: 'Organizer' })).not.toBeInTheDocument();
    expect(createTeamsMeeting).not.toHaveBeenCalled();
  });

  it('withholds Create while saved links are loading and protects the link when the read finishes', async () => {
    let release!: (module: ModuleCatalogueItem) => void;
    const pending = new Promise<ModuleCatalogueItem>(resolve => { release = resolve; });
    vi.mocked(loadModuleStructure).mockImplementation(async catalogueId => catalogueId === 'MOD-3'
      ? pending : savedModule(catalogueId));
    await renderPage();
    await screen.findByText('Reporting Basics');
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByRole('status')).toHaveTextContent(/checking its existing Teams links/);
    expect(dialog.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('combobox', { name: 'Organizer' })).not.toBeInTheDocument();
    await act(async () => { release(savedModule('MOD-3', [linkedComponent(summaries[0].joinUrl)])); });
    expect(await dialog.findByText('Existing Teams link protected')).toBeVisible();
    expect(dialog.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(createTeamsMeeting).not.toHaveBeenCalled();
  });

  it.each(['rejected', 'missing'] as const)('does not treat a %s saved-link read as permission to create', async failure => {
    vi.mocked(loadModuleStructure).mockImplementation(async catalogueId => {
      if (catalogueId !== 'MOD-3') return savedModule(catalogueId);
      if (failure === 'rejected') throw new Error('Module structure unavailable');
      return null;
    });
    await renderPage();
    await screen.findByText('Reporting Basics');
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByRole('alert')).toHaveTextContent('The saved meeting links could not be checked.');
    expect(dialog.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Try again' })).toBeVisible();
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    vi.mocked(loadModuleStructure).mockImplementation(async catalogueId => savedModule(catalogueId,
      catalogueId === 'MOD-3' ? [linkedComponent(summaries[0].joinUrl)] : []));
    await userEvent.click(dialog.getByRole('button', { name: 'Try again' }));
    expect(await dialog.findByText('Existing Teams link protected')).toBeVisible();
    expect(dialog.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
  });

  it('keeps the independent additional-meeting tab available for an imported main calendar', async () => {
    fetchCurriculumTeamsMeetingSummaries.mockResolvedValue(summaries.map(summary => summary.moduleCatalogueId === 'MOD-1'
      ? { ...summary, meetingType: 'linked-external' } : summary));
    const { TeamsMeetingDialogs } = await import('../TeamsMeetingDialogs');
    const { useTeamsMeetingsWorkspace } = await import('../useTeamsMeetingsWorkspace');
    const createAdditional = vi.fn();
    function DialogWithAdditionalTab() {
      const workspace = useTeamsMeetingsWorkspace({ scopeModuleId: 'MOD-1', initialSelectedId: 'MOD-1' });
      return <TeamsMeetingDialogs workspace={workspace} secondTab={{
        label: 'Additional week meeting',
        render: () => <button type="button" onClick={createAdditional}>Create additional meeting</button>,
      }} />;
    }
    render(<MemoryRouter><DialogWithAdditionalTab /></MemoryRouter>);
    const dialog = within(await screen.findByRole('dialog'));
    expect(await dialog.findByText('Series linked from Teams')).toBeVisible();
    await userEvent.click(dialog.getByRole('tab', { name: 'Additional week meeting' }));
    await userEvent.click(dialog.getByRole('button', { name: 'Create additional meeting' }));
    expect(createAdditional).toHaveBeenCalledTimes(1);
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
    await userEvent.click(dialog.getByRole('tab', { name: 'Module Teams calendar' }));
    expect(await dialog.findByText('Series linked from Teams')).toBeVisible();
    expect(dialog.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
  });

  it('uses the Entra people picker for every meeting role', async () => {
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByRole('combobox', { name: 'Organizer' })).toHaveAttribute('placeholder', expect.stringContaining('Search'));
    expect(dialog.getByRole('combobox', { name: 'Co-organizers' })).toHaveAttribute('placeholder', 'Search Entra by name or email...');
    expect(dialog.getByRole('combobox', { name: 'Presenters' })).toHaveAttribute('placeholder', 'Search Entra by name or email...');
    expect(dialog.getByRole('combobox', { name: 'Attendees' })).toHaveAttribute('placeholder', 'Search Entra by name or email...');
  });

  it('does not assign a presenter until the optional module prefill is requested', async () => {
    await renderPage();
    await screen.findByText('Reporting Basics');
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.queryByRole('button', { name: 'Remove mahmoudfouda015@gmail.com' })).not.toBeInTheDocument();
    expect(fetchModuleMeetingInvitees).not.toHaveBeenCalled();

    await userEvent.click(dialog.getByRole('button', { name: 'Prefill from the learners who have this module on their plan' }));
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Remove mahmoudfouda015@gmail.com' })).toBeInTheDocument());
    expect(fetchModuleMeetingInvitees).toHaveBeenCalledWith('MOD-3');
  });

  it('creates all 16 current session dates when the session cache still holds only three', async () => {
    const dates = [
      '2026-09-16', '2026-09-21', '2026-09-23', '2026-09-28',
      '2026-09-30', '2026-10-05', '2026-10-07', '2026-10-12',
      '2026-10-14', '2026-10-19', '2026-10-21', '2026-10-26',
      '2026-10-28', '2026-11-02', '2026-11-04', '2026-11-09',
    ];
    const currentSessions = dates.map((date, index) => session('MOD-3', date, {
      day: index % 2 === 0 ? 'Wednesday' : 'Monday',
      startTime: index % 2 === 0 ? '11:30' : '11:00',
      endTime: index % 2 === 0 ? '14:30' : '14:00',
      week: Math.floor(index / 2) + 1,
    }));
    const otherSessions = sessions.filter(item => item.moduleCatalogueId !== 'MOD-3');
    fetchCurriculumSessions.mockImplementation(async (_signal, options) => [
      ...otherSessions,
      ...(options?.skipCache ? currentSessions : currentSessions.slice(0, 3)),
    ]);

    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('16 module sessions')).toBeInTheDocument();
    expect(dialog.getAllByText(/^Session \d+$/)).toHaveLength(16);
    expect(dialog.getByText('16 Sept 2026, 11:30 AM')).toBeInTheDocument();
    expect(dialog.getByText('21 Sept 2026, 11:00 AM')).toBeInTheDocument();
    expect(dialog.getByText('9 Nov 2026, 11:00 AM')).toBeInTheDocument();
    // Keep exercising England's DST path explicitly now that new calendars default to Egypt.
    await userEvent.click(dialog.getByRole('combobox', { name: /Schedule time zone/ }));
    await userEvent.click(screen.getByRole('option', { name: 'England (Europe/London)' }));
    await userEvent.click(dialog.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createTeamsMeeting).toHaveBeenCalledTimes(1));
    // Keep each weekday's own time, including the October clock change in
    // the configured Microsoft calendar's London timezone.
    const expectedStarts = [
      '2026-09-16T10:30:00.000Z', '2026-09-21T10:00:00.000Z',
      '2026-09-23T10:30:00.000Z', '2026-09-28T10:00:00.000Z',
      '2026-09-30T10:30:00.000Z', '2026-10-05T10:00:00.000Z',
      '2026-10-07T10:30:00.000Z', '2026-10-12T10:00:00.000Z',
      '2026-10-14T10:30:00.000Z', '2026-10-19T10:00:00.000Z',
      '2026-10-21T10:30:00.000Z', '2026-10-26T11:00:00.000Z',
      '2026-10-28T11:30:00.000Z', '2026-11-02T11:00:00.000Z',
      '2026-11-04T11:30:00.000Z', '2026-11-09T11:00:00.000Z',
    ];
    expect(createTeamsMeeting).toHaveBeenCalledWith(expect.objectContaining({
      repeatOccurrences: 16,
      scheduledOccurrences: expectedStarts.map((startDateTimeUtc, index) => ({
        sessionNumber: index + 1, startDateTimeUtc, durationMinutes: 180,
      })),
    }), expect.objectContaining({ onSubmitted: expect.any(Function) }));
  });

  it('plays a recording in place and records how it was watched', async () => {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));

    const watch = await screen.findByRole('button', { name: /Watch recording/ });
    await userEvent.click(watch);

    // Played here rather than downloaded, so the watch can be recorded at all.
    const player = await screen.findByLabelText(/Recording — Data Foundations/);
    const video = player.querySelector('video') as HTMLVideoElement;
    expect(video).toBeTruthy();
    expect(video.getAttribute('src')).toContain('ART-REC-1');

    fireEvent.play(video);
    fireEvent.seeked(video);
    await userEvent.click(within(player).getByRole('button', { name: 'Close' }));

    await waitFor(() => expect(saveTeamsRecordingEvents).toHaveBeenCalled());
    const [liveSessionId, artifactId, payload] = saveTeamsRecordingEvents.mock.calls
      .at(-1) as unknown as [string, string, { events: Array<{ type: string }>; viewer?: unknown }];
    expect(liveSessionId).toBe('LIVE-1');
    expect(artifactId).toBe('ART-REC-1');
    expect(payload.events.map(event => event.type)).toEqual(
      expect.arrayContaining(['open', 'play', 'seeked', 'close']),
    );
    // Who watched comes from the signed-in session on the server, never from here.
    expect(payload.viewer).toBeUndefined();
  });

  // The description used to be seeded from the module's notes, which carry the
  // API's hidden `__key:value` lines — programme, cohort, group and catalogue
  // ids went straight into the calendar invitation.
  it('names the meeting after the module and leaves the details empty', async () => {
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));

    // The dialog the row opens *is* the create form: the dates it will be built
    // on, the settings, and one Create at the end.
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: /Reporting Basics/ })).toBeInTheDocument();
    expect(within(dialog).getByText('Dates the calendar will be created on')).toBeInTheDocument();
    expect(within(dialog).getByText('4 Sept 2026, 9:30 AM')).toBeInTheDocument();
    const details = within(dialog).getByLabelText(/Details/i) as HTMLTextAreaElement;
    expect(details.value).toBe('');
    expect(within(dialog).queryByText(/__program_id/)).not.toBeInTheDocument();
    expect(within(dialog).queryByDisplayValue(/__/)).not.toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createTeamsMeeting).toHaveBeenCalledTimes(1));
    const [input] = createTeamsMeeting.mock.calls[0] as unknown as [{
      title: string;
      moduleTitle: string;
      scheduledOccurrences: Array<{ startDateTimeUtc: string }>;
    }];
    expect(input.title).toBe('Reporting Basics');
    expect(input.moduleTitle).toBe('Reporting Basics');
    // 9:30 in England, the default a new calendar is scheduled in, which on
    // 4 September is BST. The Egypt default this used to assume sent the same
    // 9:30 as 06:30Z -- two hours earlier for everyone the meeting invites.
    expect(input.scheduledOccurrences.map(item => item.startDateTimeUtc)).toEqual([
      '2026-09-04T08:30:00.000Z',
    ]);
    // The create attaches links to the components already authored before
    // answering, so no follow-up restore request holds the confirmation back.
    await waitFor(() => expect(finishTeamsCreation).toHaveBeenCalledTimes(1));
    expect(restoreModuleTeamsMeeting).not.toHaveBeenCalled();
  });

  /**
   * A session's length comes from the group's weekly slot -- Group A runs
   * 09:30 to 11:30, so a session is two hours -- and the Duration field is an
   * override on top of that, not a description of it. Both have to be visible:
   * a preview that goes on showing two hours while Create books one is a
   * promise the calendar does not keep, and a select with no way back to "each
   * session keeps its own" is a one-way door.
   */
  it('names the group session length and previews a duration override before it is sent', async () => {
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText(/delivers Wednesday, 9:30 AM - 11:30 AM/)).toBeInTheDocument();
    expect(dialog.getByText('120 min each')).toBeInTheDocument();

    await userEvent.click(dialog.getByRole('combobox', { name: /^Duration/ }));
    expect(await screen.findByRole('option', { name: 'Use scheduled duration for each session' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('option', { name: '1 hour' }));

    expect(dialog.getByText('60 min each')).toBeInTheDocument();
    expect(dialog.getByText(/Duration is set to 1 hour/)).toBeInTheDocument();

    await userEvent.click(dialog.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createTeamsMeeting).toHaveBeenCalledTimes(1));
    const [input] = createTeamsMeeting.mock.calls[0] as unknown as [{
      durationMinutes: number;
      scheduledOccurrences: Array<{ durationMinutes: number }>;
    }];
    expect(input.durationMinutes).toBe(60);
    expect(input.scheduledOccurrences.map(item => item.durationMinutes)).toEqual([60]);
  });

  /**
   * Create is the end of this dialog's job. Leaving it open re-rendered the
   * module in its summary view, which reads as "nothing happened" on top of a
   * form that has just sent real invitations.
   */
  it('confirms the dates reached Teams and closes the dialog', async () => {
    // Graph accepted the meeting options, so this is the clean-success path --
    // the one whose confirmation names the dates rather than warning about them.
    createTeamsMeeting.mockResolvedValueOnce({
      created: true,
      meeting: { settingsApplied: true } as never,
      warnings: [],
    });
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));

    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(finishTeamsCreation).toHaveBeenCalledTimes(1));
    expect(finishTeamsCreation).toHaveBeenCalledWith(
      expect.objectContaining({ created: true, meeting: { settingsApplied: true } }),
      expect.objectContaining({ scheduledOccurrences: expect.any(Array) }),
    );

    // The dialog is gone, not swapped for the summary view of the same module.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  /**
   * Once the review is confirmed the form has nothing left to ask. Its only
   * sign of progress used to sit below a long scroll, which read as a stuck
   * dialog; the steps of the create now take the form's place until it answers.
   */
  it('replaces the form with the create steps while the create runs', async () => {
    let answer!: (value: unknown) => void;
    createTeamsMeeting.mockImplementationOnce(((_input: unknown, options?: { onSubmitted?: () => void }) => {
      options?.onSubmitted?.();
      return new Promise(resolve => { answer = resolve; });
    }) as never);
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    expect(await within(dialog).findByText('Creating the Teams calendar for Reporting Basics')).toBeVisible();
    expect(within(dialog).getByText('Calendar created in Microsoft Teams')).toBeVisible();
    expect(within(dialog).getByText('LMS schedule emails sent')).toBeVisible();
    expect(within(dialog).queryByLabelText(/Details/i)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/Check again/)).not.toBeInTheDocument();

    await act(async () => { answer({ created: true, meeting: { settingsApplied: true }, warnings: [] }); });
    await waitFor(() => expect(finishTeamsCreation).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText('Creating the Teams calendar for Reporting Basics')).not.toBeInTheDocument());
  });

  /**
   * A Create the browser stopped waiting for may already have finished on the
   * server. Pressing Create again used to be the only way forward, and it saved
   * the same Microsoft meeting twice. The dialog now asks the server instead.
   */
  it('recovers a timed-out create from its saved status without sending Create again', async () => {
    const { CurriculumRequestTimeout } = await import('../../module-builder/moduleAuthoringData');
    createTeamsMeeting.mockRejectedValueOnce(new CurriculumRequestTimeout());
    fetchTeamsCreateStatus.mockResolvedValueOnce({
      state: 'done', claim: { outcomeStatus: 200, outcomeCode: '', liveSessionId: 'LIVE-NEW', claimedAt: '', leaseUntil: '' },
      calendar: { liveSessionId: 'LIVE-NEW', joinUrl: 'https://teams.microsoft.com/meet/new', organizerEmail: 'tutor@example.com', warnings: [], settingsApplied: true },
    } as never);
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(finishTeamsCreation).toHaveBeenCalledTimes(1));
    expect(finishTeamsCreation).toHaveBeenCalledWith(
      expect.objectContaining({ created: true, warnings: [], meeting: expect.objectContaining({ liveSessionId: 'LIVE-NEW', settingsApplied: true }) }),
      expect.objectContaining({ scheduledOccurrences: expect.any(Array) }),
    );
    expect(createTeamsMeeting).toHaveBeenCalledTimes(1);
    // The server attached the links before its claim finished; nothing to restore.
    expect(restoreModuleTeamsMeeting).not.toHaveBeenCalled();
  });

  it('recovers a saved calendar when Graph has not exposed its meeting options yet', async () => {
    const { ApiError } = await import('../../module-builder/moduleAuthoringData');
    createTeamsMeeting.mockRejectedValueOnce(new ApiError(
      502,
      'The calendar is saved, but verification is incomplete.',
      undefined,
      { meetingCreated: true, liveSessionId: 'LIVE-PARTIAL' },
    ));
    fetchTeamsCreateStatus.mockResolvedValueOnce({
      state: 'done',
      claim: { outcomeStatus: 502, outcomeCode: '', liveSessionId: 'LIVE-PARTIAL', claimedAt: '', leaseUntil: '' },
      calendar: {
        liveSessionId: 'LIVE-PARTIAL',
        joinUrl: 'https://teams.microsoft.com/meet/partial',
        organizerEmail: 'tutor@example.com',
        warnings: ['Meeting options are pending.'],
        settingsApplied: false,
      },
    } as never);

    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(finishTeamsCreation).toHaveBeenCalledTimes(1));
    expect(finishTeamsCreation).toHaveBeenCalledWith(
      expect.objectContaining({
        created: true,
        warnings: expect.arrayContaining([
          'Meeting options are pending.',
          expect.stringContaining('did not finish verification'),
        ]),
        meeting: expect.objectContaining({ liveSessionId: 'LIVE-PARTIAL', settingsApplied: false }),
      }),
      expect.anything(),
    );
    expect(createTeamsMeeting).toHaveBeenCalledTimes(1);
  });

  it('keeps Create locked after an uncertain create until the author confirms they checked Outlook', async () => {
    const { CurriculumRequestTimeout } = await import('../../module-builder/moduleAuthoringData');
    createTeamsMeeting.mockRejectedValueOnce(new CurriculumRequestTimeout());
    fetchTeamsCreateStatus.mockResolvedValueOnce({ state: 'uncertain', claim: null, calendar: null } as never);
    await renderPage();
    expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }));

    expect(await within(dialog).findByText(/did not report back/)).toBeVisible();
    expect(within(dialog).getByRole('button', { name: 'Create' })).toBeDisabled();
    expect(createTeamsMeeting).toHaveBeenCalledTimes(1);
    expect(finishTeamsCreation).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole('button', { name: /I checked Outlook/ }));
    await waitFor(() => expect(createTeamsMeeting).toHaveBeenCalledTimes(2));
    expect(createTeamsMeeting).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({ confirmUncertain: true }));
  });
  /**
   * The create form lives in the dialog itself, so the dialog's own X, backdrop
   * and Escape are the only ways out of it. Nothing has reached Teams until
   * Create runs, which is exactly why a filled-in form must not vanish on a
   * mis-click.
   */
  describe('leaving the create form without creating', () => {
    async function openCreateForm() {
      await renderPage();
      expect(await screen.findByText('Reporting Basics')).toBeInTheDocument();
      await userEvent.click(within(rowFor('Reporting Basics')).getByRole('button', { name: 'Create Teams meetings calendar' }));
      return within(await screen.findByRole('dialog'));
    }

    it('closes an untouched form without asking', async () => {
      const dialog = await openCreateForm();
      await userEvent.click(dialog.getByRole('button', { name: 'Close' }));

      expect(confirmMock).not.toHaveBeenCalled();
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    });

    it('asks before throwing away what was filled in, and stays open until the user says so', async () => {
      const dialog = await openCreateForm();
      await userEvent.type(dialog.getByLabelText(/Details/i), 'Bring the reporting template.');
      await userEvent.click(dialog.getByRole('button', { name: 'Close' }));

      expect(confirmMock).toHaveBeenCalledTimes(1);
      expect(confirmMock.mock.calls[0][0].confirmButtonText).toBe('Discard changes');
      // Cancelling the alert is the default, so the form survives.
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(createTeamsMeeting).not.toHaveBeenCalled();
    });

    it('closes once the discard is confirmed', async () => {
      // The real alert runs onConfirm when the user picks "Discard changes".
      confirmMock.mockImplementation(async options => {
        await options.onConfirm();
        return true;
      });
      const dialog = await openCreateForm();
      await userEvent.type(dialog.getByLabelText(/Details/i), 'Bring the reporting template.');
      await userEvent.click(dialog.getByRole('button', { name: 'Close' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(createTeamsMeeting).not.toHaveBeenCalled();
    });

    it('guards the backdrop and Escape too, not just the cross', async () => {
      const dialog = await openCreateForm();
      await userEvent.type(dialog.getByLabelText(/Details/i), 'Bring the reporting template.');

      const backdrop = screen.getByRole('dialog').parentElement?.querySelector('div.absolute.inset-0');
      fireEvent.click(backdrop as HTMLElement);
      expect(confirmMock).toHaveBeenCalledTimes(1);

      fireEvent.keyDown(document, { key: 'Escape' });
      // The second way out asks again rather than closing silently; a raised
      // alert is not re-raised on top of itself.
      expect(confirmMock.mock.calls.length).toBeGreaterThanOrEqual(1);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });
  /**
   * A meeting is one absolute instant, and each person's Teams renders it in that
   * person's own timezone. The occurrence table is `timestamp without time zone`
   * holding UTC, so its values can reach the browser without an offset -- and
   * `new Date()` reads those as the reader's own local time. Read that way, a
   * calendar that matches perfectly reads as two hours out for a reader in Cairo
   * and as in sync for one in London, off the same data.
   */
  it('reads a Teams date that names no offset as UTC, so the reader’s own zone cannot invent drift', async () => {
    fetchCurriculumTeamsMeetingSummaries.mockImplementation(async () => summaries.map(summary => ({
      ...summary,
      startDateTime: String(summary.startDateTime).replace('Z', ''),
      nextOccurrence: String(summary.nextOccurrence).replace('Z', ''),
      occurrenceDates: (summary.occurrenceDates || []).map(value => String(value).replace('Z', '')),
    })));

    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    // Same dates as the stamped fixture, so the same verdict: the offset being
    // absent is not a difference.
    expect(within(rowFor('Data Foundations')).getByText('In sync')).toBeInTheDocument();
    expect(within(rowFor('Risk Management')).getByText('Dates differ')).toBeInTheDocument();
  });

  it('says whose clock the times on this page are', async () => {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    // The calendar's zone is what the column shows; a reader elsewhere is told
    // how far their own Teams will differ rather than left to wonder.
    expect(screen.getByText(/Microsoft calendar's timezone \(GMT Standard Time\)/)).toBeInTheDocument();
  });
});

/**
 * The attendee comparison: the one action on this page that asks Microsoft a
 * question instead of telling it something.
 *
 * The gap it fills is that a save only writes when the form differs from the
 * stored roster, so somebody added to the meeting in Outlook is invisible here
 * -- the form matches, Save refuses, and the extra person stays. So these tests
 * hold the two things that make it useful: it works on an untouched form, and
 * it leaves a touched one exactly as it found it.
 */
describe('Comparing the invitation list with Teams', () => {
  const comparison = {
    status: 'different' as const, lmsCount: 3, teamsCount: 3, matchingCount: 2, extraCount: 1, missingCount: 1,
    matching: [{ email: 'learner@example.com', name: 'A Learner' }, { email: 'tutor@example.com', name: 'Tutor One' }],
    extraOnTeams: [{ email: 'john@example.com', name: 'John Smith' }],
    missingFromTeams: [{ email: 'apprentice@example.com', name: 'An Apprentice' }],
    aliasPossible: true, checkedAt: '2026-09-30T16:48:00Z',
  };
  const matched = {
    ...comparison, status: 'match' as const, matchingCount: 3, extraCount: 0, missingCount: 0,
    extraOnTeams: [], missingFromTeams: [], aliasPossible: false,
  };

  beforeEach(() => {
    vi.mocked(loadModuleStructure).mockReset();
    vi.mocked(loadModuleStructure).mockImplementation(async catalogueId => savedModule(catalogueId));
    vi.mocked(syncTeamsCalendarState).mockReset();
    vi.mocked(syncTeamsCalendarState).mockResolvedValue({ changed: false, seriesStatus: 'active', cancelledSessions: [], errors: [] });
    vi.mocked(loadTeamsMeetingArtifacts).mockReset();
    vi.mocked(loadTeamsMeetingArtifacts).mockResolvedValue(artifacts as never);
    vi.mocked(compareTeamsAttendees).mockReset();
    vi.mocked(compareTeamsAttendees).mockResolvedValue(comparison);
    updateTeamsMeetingSchedule.mockClear();
    fetchCurriculumTeamsMeetingSummaries.mockImplementation(async () => summaries);
  });

  async function openInvitations() {
    await renderPage();
    expect(await screen.findByText('Data Foundations')).toBeInTheDocument();
    await userEvent.click(within(rowFor('Data Foundations')).getByRole('button', { name: 'Detail' }));
    const dialog = within(await screen.findByRole('dialog'));
    await waitFor(() => expect(dialog.getByRole('button', { name: 'Update Teams calendar' })).not.toBeDisabled());
    return dialog;
  }

  it('offers the comparison on an untouched form, where Save itself is refused', async () => {
    const dialog = await openInvitations();
    // Nothing has been edited, so the save path is closed...
    expect(dialog.getByRole('button', { name: /Save without notifying/ })).toBeDisabled();
    // ...and this is exactly when an externally added attendee is invisible.
    const compare = dialog.getByRole('button', { name: /Compare with Teams/ });
    expect(compare).toBeEnabled();

    await userEvent.click(compare);
    await waitFor(() => expect(compareTeamsAttendees).toHaveBeenCalledWith('LIVE-1'));
    // The comparison is not a save: nothing was sent to the calendar.
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('shows it is working and refuses to ask Microsoft twice at once', async () => {
    let release: (value: typeof comparison) => void = () => undefined;
    vi.mocked(compareTeamsAttendees).mockImplementation(() => new Promise(resolve => { release = resolve; }));
    const dialog = await openInvitations();
    await userEvent.click(dialog.getByRole('button', { name: /Compare with Teams/ }));

    const busy = await dialog.findByRole('button', { name: /Comparing/ });
    expect(busy).toBeDisabled();
    await userEvent.click(busy);
    expect(compareTeamsAttendees).toHaveBeenCalledTimes(1);

    await act(async () => { release(comparison); });
    await dialog.findByRole('button', { name: /Compare with Teams/ });
  });

  it('names who is on Teams and who is not, with the moment it was read', async () => {
    const dialog = await openInvitations();
    await userEvent.click(dialog.getByRole('button', { name: /Compare with Teams/ }));

    const extra = within(await dialog.findByRole('list', { name: 'Extra on Teams' }));
    expect(extra.getByText('John Smith')).toBeInTheDocument();
    expect(extra.getByText('john@example.com')).toBeInTheDocument();
    const missing = within(dialog.getByRole('list', { name: 'Missing from Teams' }));
    expect(missing.getByText('apprentice@example.com')).toBeInTheDocument();
    expect(dialog.getByText(/Last checked:/)).toBeInTheDocument();
    // Both lists are occupied, so one pair of them may be one mailbox.
    expect(dialog.getByText(/may be\s+the same person under two spellings/)).toBeInTheDocument();
    // Found only on Teams, and left there: the editable list is untouched.
    expect(dialog.queryByRole('button', { name: 'Remove john@example.com' })).not.toBeInTheDocument();
  });

  it('says so plainly when Microsoft holds exactly the published list', async () => {
    vi.mocked(compareTeamsAttendees).mockResolvedValue(matched);
    const dialog = await openInvitations();
    await userEvent.click(dialog.getByRole('button', { name: /Compare with Teams/ }));

    expect(await dialog.findByText('Teams attendees match the LMS invitation list.')).toBeInTheDocument();
    expect(dialog.queryByRole('list', { name: 'Extra on Teams' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('list', { name: 'Missing from Teams' })).not.toBeInTheDocument();
  });

  it('shows why the comparison failed rather than an empty panel', async () => {
    vi.mocked(compareTeamsAttendees).mockRejectedValue(new Error('The Microsoft calendar event could not be found.'));
    const dialog = await openInvitations();
    await userEvent.click(dialog.getByRole('button', { name: /Compare with Teams/ }));

    expect(await dialog.findByText('The Microsoft calendar event could not be found.')).toBeInTheDocument();
    expect(dialog.queryByText(/Last checked:/)).not.toBeInTheDocument();
  });

  it('leaves an unsaved edit alone, and counts it as pending rather than lost', async () => {
    vi.mocked(compareTeamsAttendees).mockResolvedValue(matched);
    const dialog = await openInvitations();
    const attendees = dialog.getByRole('combobox', { name: 'Attendees' });
    fireEvent.change(attendees, { target: { value: 'mohamed@example.com' } });
    fireEvent.keyDown(attendees, { key: 'Enter' });
    expect(dialog.getByRole('button', { name: 'Remove mohamed@example.com' })).toBeInTheDocument();

    await userEvent.click(dialog.getByRole('button', { name: /Compare with Teams/ }));
    await dialog.findByText('Teams attendees match the LMS invitation list.');

    // Microsoft was asked about the published list, so the person typed a
    // moment ago is pending -- not a learner whose invitation went missing.
    const pending = within(dialog.getByRole('list', { name: 'Not published yet' }));
    expect(pending.getByText('mohamed@example.com')).toBeInTheDocument();
    expect(dialog.getByText('Pending local changes are not included in this comparison.')).toBeInTheDocument();
    expect(dialog.queryByRole('list', { name: 'Missing from Teams' })).not.toBeInTheDocument();

    // The edit itself survived the round trip, and is still savable.
    expect(dialog.getByRole('button', { name: 'Remove mohamed@example.com' })).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: /Save without notifying/ })).toBeEnabled();
  });
});
