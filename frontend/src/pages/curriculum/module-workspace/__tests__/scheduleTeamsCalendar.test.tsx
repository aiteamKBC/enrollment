import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { fetchModuleSessionPlan, loadModuleStructure, loadTeamsMeetingArtifacts } from '../../module-builder/moduleAuthoringData';
import type {
  CurriculumCohort,
  CurriculumGroup,
  CurriculumHoliday,
  CurriculumModule,
  CurriculumProgramme,
  CurriculumTeamsMeetingSummary,
} from '@/lib/curriculumApi';
import type { ModuleCatalogueItem } from '../../module-builder/moduleAuthoringData';

/**
 * The Schedule tab reads the module's saved plan: the dates the backend
 * generates from it, the holiday shifts they caused, and the Teams calendar
 * those dates are sent to. It holds no editor of its own — the dates are typed
 * in the one module form, which the header's Edit module button opens.
 *
 * Neither it nor the Teams meeting tab writes to the Teams calendar: creating
 * the series, sending the dates, invitations and fetching results all belong to
 * the Teams Meetings page, so both tabs here only read and link out.
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
  {
    id: 'COHORT-1', name: 'Sept 2026', programmeId: 'PROG-DATA', programme: 'Data Analyst',
    status: 'active', startDate: '2026-09-01', holidayIds: ['HOL-1'],
  },
] as unknown as CurriculumCohort[];

const groups = [
  {
    id: 'GROUP-1', name: 'Group A', cohortId: 'COHORT-1', cohort: 'Sept 2026',
    programme: 'Data Analyst', coach: 'Coach One', weekDays: 'Wednesday',
    startTime: '09:30', endTime: '11:30', status: 'active',
  },
] as unknown as CurriculumGroup[];

const modules = [
  {
    id: 'MOD-1', moduleCatalogueId: 'MOD-1', name: 'Data Foundations', groupId: 'GROUP-1',
    cohortId: 'COHORT-1', programmeId: 'PROG-DATA', weeks: 2, sessionsNumber: 2,
    startDate: '2026-09-02', status: 'published', tutor: 'Tutor One',
  },
] as unknown as CurriculumModule[];

const holidays = [
  { id: 'HOL-1', label: 'Autumn closure', startDate: '2026-09-09', endDate: '2026-09-09' },
] as unknown as CurriculumHoliday[];

const teamsMeetings: CurriculumTeamsMeetingSummary[] = [
  {
    moduleCatalogueId: 'MOD-1', liveSessionId: 'LIVE-1', status: 'active',
    joinUrl: 'https://teams.microsoft.com/l/meetup-join/one', organizerEmail: 'tutor@example.com',
    eventId: 'event-1', presenters: ['tutor@example.com'], attendees: [],
    repeatPattern: 'weekly', startDateTime: '2026-09-02T08:30:00Z', durationMinutes: 120,
    occurrenceCount: 2, upcomingCount: 2, syncedCount: 0, nextOccurrence: '2026-09-02T08:30:00Z',
    updatedAt: '2026-08-20T10:00:00Z',
  },
];

// The module's Course structure IS its schedule: two weeks, one live-session
// component each, each carrying its own date. Nothing is generated from
// `sessionsNumber` any more, so a structure is what the Schedule tab has to be
// given -- without one the tab correctly reports the module as unfinished.
//
// Session two lands ON the closure and stays there: a holiday warns and moves
// nothing.
function liveSession(id: string, weekId: string, sessionDate: string) {
  return {
    id, weekId, type: 'live-session' as const, title: `Live ${id}`, description: '',
    expectedOtjh: 2, points: 0, reflectionRequired: false, reflectionQuestion: '',
    workplaceEvidenceRequired: false, tutorValidationRequired: false,
    coachValidationRequired: false, ksbMappings: [],
    settings: { sessionDate, sessionTime: '09:30', durationMinutes: 120 },
  };
}

const structure = {
  catalogueId: 'MOD-1', id: 'MOD-1', title: 'Data Foundations', sourceId: 'MOD-1',
  programmeId: 'PROG-DATA', programmeName: 'Data Analyst', cohort: 'Sept 2026', group: 'Group A',
  weeks: 2, sessionsNumber: 2, startDate: '2026-09-02', endDate: '2026-09-09',
  status: 'published', totalOtjh: 4, ksbCount: 0, lessonCount: 0, quizCount: 0,
  weekStructure: [
    { id: 'WEEK-1', weekNumber: 1, title: 'W1', sessionDate: '2026-09-02', components: [liveSession('COMP-1', 'WEEK-1', '2026-09-02')] },
    { id: 'WEEK-2', weekNumber: 2, title: 'W2', sessionDate: '2026-09-09', components: [liveSession('COMP-2', 'WEEK-2', '2026-09-09')] },
  ],
} as unknown as ModuleCatalogueItem;

const updateCurriculumModule = vi.fn(async () => ({}));

vi.mock('@/lib/curriculumApi', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/curriculumApi')>()),
  updateCurriculumModule: (...args: unknown[]) => updateCurriculumModule(...(args as [])),
  fetchCurriculumModuleKsbCoverage: vi.fn(async () => null),
}));

const updateTeamsMeetingSchedule = vi.fn(async () => ({ updated: true, meeting: {} as never, warnings: [] }));
const createTeamsMeeting = vi.fn(async () => ({
  created: true,
  meeting: { liveSessionId: 'LIVE-NEW' } as never,
  warnings: [] as string[],
}));

// Teams is holding a date of its own for session two, so the tab still has an
// off-the-plan calendar to report.
const artifacts = {
  series: { id: 'LIVE-1', module_title: 'Data Foundations', organizer_email: 'tutor@example.com', join_url: '', online_meeting_id: 'meeting-1' },
  occurrences: [
    {
      id: 'OCC-1', session_number: 1, scheduled_start: '2026-09-02T08:30:00Z', scheduled_end: '2026-09-02T10:30:00Z',
      participant_count: 0, status: 'scheduled', attendance: [], artifacts: [],
    },
    {
      id: 'OCC-2', session_number: 2, scheduled_start: '2026-09-16T08:30:00Z', scheduled_end: '2026-09-16T10:30:00Z',
      participant_count: 0, status: 'scheduled', attendance: [], artifacts: [],
    },
  ],
};

// Only the calls that reach Microsoft are stubbed; the timezone maths that turns
// 09:30 in the calendar's zone into a UTC instant is the real implementation.
vi.mock('../../module-builder/moduleAuthoringData', async importOriginal => ({
  ...(await importOriginal<typeof import('../../module-builder/moduleAuthoringData')>()),
  loadModuleStructure: vi.fn(async () => structure),
  // The module's own dated plan, read only to check the authored dates against.
  // Nothing is rendered from it, so "no plan" is the quiet default: a plan the
  // tab could not read is not a disagreement it can report.
  fetchModuleSessionPlan: vi.fn(async () => null),
  loadTeamsMeetingConfiguration: vi.fn(async () => ({
    configured: true, defaultOrganizer: 'tutor@example.com',
    timeZone: 'GMT Standard Time', timeZoneIana: 'Europe/London',
  })),
  loadTeamsMeetingArtifacts: vi.fn(async () => artifacts),
  restoreModuleTeamsMeeting: vi.fn(async () => ({ restored: true, updatedComponents: 1, meeting: {}, module: {} })),
  updateTeamsMeetingSchedule: (...args: unknown[]) => updateTeamsMeetingSchedule(...(args as [])),
  createTeamsMeeting: (...args: unknown[]) => createTeamsMeeting(...(args as [])),
}));

let entityTeamsMeetings = teamsMeetings;

vi.mock('@/hooks/useCurriculumEntities', () => ({
  useCurriculumEntities: () => ({
    programmes, cohorts, groups, modules, holidays,
    tutors: [{ id: 'STAFF-1', name: 'Tutor One', email: 'tutor.one@example.com' }],
    coaches: [], teamsMeetings: entityTeamsMeetings,
    entities: {}, loading: false, loaded: true, error: null,
    reload: vi.fn(async () => null),
  }),
}));

async function renderSchedule() {
  const { default: Page } = await import('../page');
  return render(
    <MemoryRouter initialEntries={['/curriculum/modules/MOD-1?tab=schedule']}>
      <Routes>
        <Route path="/curriculum/modules/:id" element={<Page />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Module workspace — Schedule tab Teams calendar', () => {
  beforeEach(() => {
    vi.mocked(fetchModuleSessionPlan).mockClear();
    vi.mocked(loadTeamsMeetingArtifacts).mockReset();
    vi.mocked(loadTeamsMeetingArtifacts).mockResolvedValue(artifacts as never);
    entityTeamsMeetings = teamsMeetings;
    updateTeamsMeetingSchedule.mockClear();
    createTeamsMeeting.mockClear();
    updateCurriculumModule.mockClear();
  });

  // Rewritten with the clash rule: a holiday is a warning now and nothing else,
  // so the red "blocked / replacement" pair this used to assert no longer
  // exists. What replaces it is a note on the session's own ordinary row.
  it('warns on the session a holiday falls on, and moves nothing', async () => {
    await renderSchedule();
    expect(await screen.findByText('Session 1')).toBeInTheDocument();

    // The session keeps its own date and its own single row...
    expect(await screen.findByText(/Heads up: this session falls on a holiday \(Autumn closure\)/))
      .toBeInTheDocument();
    // ...and the clash machinery is gone: no blocked card, no replacement, no
    // second date for one session, nothing counted as skipped.
    expect(screen.queryByText('Shifted to replacement')).not.toBeInTheDocument();
    expect(screen.queryByText(/Blocked by/)).not.toBeInTheDocument();
    expect(screen.queryByText('Replacement delivered')).not.toBeInTheDocument();
    expect(screen.queryByText(/skipped$/)).not.toBeInTheDocument();
    // A session that runs on a clear day stays quiet: "no clash", repeated down
    // the list, is not a fact anyone reads.
    expect(screen.queryByText(/No holiday clash/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Runs on its own day/)).not.toBeInTheDocument();
  });

  // The saved exception lives on the session itself now -- its own date, clock
  // and length, authored on the component -- rather than in a plan generated
  // from the group's timetable. The tab reads what the session holds, and
  // reading it moves nothing at Microsoft.
  it('shows the session its own authored clock instead of rebuilding the group one', async () => {
    vi.mocked(loadModuleStructure).mockResolvedValueOnce({
      ...structure,
      weekStructure: [
        {
          ...structure.weekStructure[0],
          components: [{
            ...liveSession('COMP-1', 'WEEK-1', '2026-09-04'),
            settings: { sessionDate: '2026-09-04', sessionTime: '12:00', durationMinutes: 90 },
          }],
        },
        structure.weekStructure[1],
      ],
    } as never);
    await renderSchedule();
    await screen.findByText('Session 1');
    // The plan is still read once, for the drift check and nothing else.
    expect(fetchModuleSessionPlan).toHaveBeenCalledWith('MOD-1', 2, { timeoutMs: 30000 });
    expect(screen.getByText(/4 Sept 2026, 12:00/)).toBeInTheDocument();
    expect(screen.getByText('90–120 min')).toBeInTheDocument();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('shows cancellation against its own session while preserving the next session and its evidence', async () => {
    vi.mocked(loadTeamsMeetingArtifacts).mockResolvedValue({ ...artifacts, occurrences: [
      { ...artifacts.occurrences[0], status: 'cancelled' },
      { ...artifacts.occurrences[1], status: 'held', participant_count: 3 },
    ] } as never);
    await renderSchedule();
    expect(await screen.findByText(/cancelled.*0 attended/)).toBeInTheDocument();
    expect(screen.getAllByText(/held.*3 attended/)).toHaveLength(1);
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
  });

  it('offers the meeting once, and gives each session its own status instead', async () => {
    await renderSchedule();
    expect(await screen.findByText('Session 1')).toBeInTheDocument();

    // One way in, on the series that owns the link -- not the same link
    // repeated down every row of the schedule.
    const link = screen.getByRole('link', { name: 'Open in Teams' });
    expect(link).toHaveAttribute('href', 'https://teams.microsoft.com/l/meetup-join/one');
    expect(link).toHaveAttribute('target', '_blank');

    // What a row carries about its meeting is the part that differs by date.
    expect(await screen.findAllByText('scheduled')).toHaveLength(2);
    // The calendar's copy of a date is not printed under the date it repeats.
    expect(screen.queryByText(/scheduled · 0 attended/)).not.toBeInTheDocument();
  });

  it('says the Teams calendar is off the plan, and sends the reader to the page that fixes it', async () => {
    await renderSchedule();
    expect((await screen.findAllByText('Session 2')).length).toBeGreaterThan(0);
    // Teams is holding a date of its own for session two.
    await waitFor(() => expect(
      screen.getByText('The Teams calendar is not on these dates yet — send them from the Teams Meetings page.'),
    ).toBeInTheDocument());
    // The Schedule tab reports the calendar; it never sends to it.
    expect(screen.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Manage on Teams Meetings' })[0])
      .toHaveAttribute('href', '/curriculum/teams-meetings?module=MOD-1');
  });

  it('keeps the Teams meeting card read-only: every calendar action lives on the Teams Meetings page', async () => {
    await renderSchedule();
    expect(await screen.findByText('Session 1')).toBeInTheDocument();

    // What the calendar holds is still read here...
    expect(await screen.findByText(/tutor@example\.com/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open in Teams' })).toBeInTheDocument();
    // ...but nothing here writes to it. One link out, no action buttons.
    expect(screen.getByRole('link', { name: 'Manage on Teams Meetings' }))
      .toHaveAttribute('href', '/curriculum/teams-meetings?module=MOD-1');
    expect(screen.queryByRole('button', { name: 'Update Teams calendar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create Teams calendar' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Fetch attendance/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Re-attach meeting/ })).not.toBeInTheDocument();
    expect(updateTeamsMeetingSchedule).not.toHaveBeenCalled();
    expect(createTeamsMeeting).not.toHaveBeenCalled();
    expect(updateCurriculumModule).not.toHaveBeenCalled();
  });

  it('has no schedule editor of its own, and opens the one module form instead', async () => {
    await renderSchedule();
    expect(await screen.findByText('Session 1')).toBeInTheDocument();
    // The delivery-plan form the tab used to carry is gone: the same three
    // values are edited in the module form, so there is only one of them.
    expect(screen.queryByRole('spinbutton', { name: /Number of sessions/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save schedule' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Edit module' }));

    expect(await screen.findByRole('button', { name: 'Save module' })).toBeInTheDocument();
    expect(screen.getByDisplayValue('Data Foundations')).toBeInTheDocument();
  });

  it('points at the Teams Meetings page when the module has no meeting yet', async () => {
    entityTeamsMeetings = [];
    await renderSchedule();
    expect(await screen.findByText('Session 1')).toBeInTheDocument();
    // With no meeting there is nothing to say per session, and no link to offer.
    expect(screen.queryByRole('link', { name: 'Open in Teams' })).not.toBeInTheDocument();
    expect(screen.queryByText('scheduled')).not.toBeInTheDocument();
    expect(screen.getByText(/No Teams calendar yet\. Create one on the Teams Meetings page/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create Teams calendar' })).not.toBeInTheDocument();
    expect(createTeamsMeeting).not.toHaveBeenCalled();
  });
});
