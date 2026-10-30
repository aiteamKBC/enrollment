import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AppIcon } from '@/components/feature/AppIcon';
import { curriculumNavItems } from '@/mocks/navigation';
import { viewerZoneOffset } from '../module-builder/moduleAuthoringData';
import {
  cleanText,
  cohortsForProgramme,
  matchesSearch,
  moduleIdentity,
  modulesForScope,
  namedCurriculumWorkspacePath,
  normaliseKey,
  programmeIdentity,
} from '../shared/entities/model';
import { calendarLabel } from "./createCalendarForm.helpers";
import {
  EntityEmptyState,
  EntityFilterBar,
  EntityHero,
  EntityTable,
  InlineError,
  PlainCell,
  StackedCell,
} from '../shared/entities/ui';
import { STATE_LABELS, STATE_TONES, useTeamsMeetingsWorkspace } from './useTeamsMeetingsWorkspace';
import { TeamsMeetingDialogs } from './TeamsMeetingDialogs';

// ============================================================================
// Teams Meetings — one row per module, and the schedule side of the Teams work.
//
// The module's own stored session dates are the authority here: they are the
// dates the backend generated after the cohort's holidays were applied, and the
// Teams calendar has to be made to agree with them. Graph can only hold an
// unbroken weekly recurrence, so a holiday-shifted plan is reconciled instance
// by instance — which is what `updateTeamsMeetingSchedule` does with the
// `scheduledOccurrences` this page sends.
//
// Attendance, transcripts and recordings stay read-mostly: they are pulled back
// from Teams after a meeting has run, and the same panel that shows them is in
// the module workspace. Nothing here creates a second way to author a module.
// ============================================================================

const GRID = 'grid grid-cols-[minmax(170px,1.2fr)_minmax(140px,1fr)_minmax(150px,1fr)_minmax(130px,.9fr)_minmax(104px,.7fr)_minmax(120px,.8fr)_minmax(300px,auto)]';

const COLUMNS = [
  { label: 'Module' },
  { label: 'Cohort / Group' },
  { label: 'Organizer' },
  { label: 'First meeting' },
  { label: 'Sessions', align: 'center' as const },
  { label: 'Calendar' },
  { label: 'Actions', align: 'right' as const },
];



/**
 * Row actions with their names on them. The shared `RowActions` is icon-only,
 * which works where the verb is obvious (edit, archive); here it is not — "send
 * the module's dates to Teams" and "fetch what Teams recorded" are two different
 * kinds of sync, and a glyph makes the reader guess which is which.
 */
function NamedActions({ actions }: {
  actions: Array<{
    icon: string;
    label: string;
    title: string;
    onClick: () => void;
    disabled?: boolean;
    primary?: boolean;
    busy?: boolean;
  }>;
}) {
  return (
    <span className="flex flex-wrap items-center justify-end gap-1.5 self-center">
      {actions.map(action => (
        <button
          key={action.label}
          type="button"
          title={action.title}
          disabled={action.disabled}
          onClick={event => { event.stopPropagation(); action.onClick(); }}
          className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[11px] font-bold transition-smooth disabled:cursor-not-allowed disabled:opacity-50 ${
            action.primary
              ? 'border-primary-600 bg-primary-600 text-white hover:bg-primary-700'
              : 'border-background-200 bg-background-50 text-foreground-600 hover:bg-background-100'
          }`}
        >
          <AppIcon className={`${action.busy ? 'ri-loader-4-line animate-spin' : action.icon} text-sm`}></AppIcon>
          {action.label}
        </button>
      ))}
    </span>
  );
}


export default function CurriculumTeamsMeetingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const workspace = useTeamsMeetingsWorkspace({ initialSelectedId: searchParams.get('module') || '' });
  const {
    programmes, cohorts, groups, modules, loading, loaded, refreshing, error, reload,
    teamsLoading, teamsLoaded, teamsError, graphStatus, checkGraphConfiguration, timeZoneLabel,
    selectedId, setSelectedId, notice, setNotice, rows, selected, stats, loadTeamsState,
  } = workspace;

  const [search, setSearch] = useState(() => searchParams.get('q') || '');
  const [programmeFilter, setProgrammeFilter] = useState(searchParams.get('programme') || '');
  const [cohortFilter, setCohortFilter] = useState(searchParams.get('cohort') || '');
  const [stateFilter, setStateFilter] = useState(searchParams.get('state') || '');

  // The search sits in the URL with the filters (replaced, not pushed), so Back
  // from a module's schedule returns to the list as it was left.
  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    if (programmeFilter) next.set('programme', programmeFilter); else next.delete('programme');
    if (cohortFilter) next.set('cohort', cohortFilter); else next.delete('cohort');
    if (stateFilter) next.set('state', stateFilter); else next.delete('state');
    if (search) next.set('q', search); else next.delete('q');
    if (selectedId) next.set('module', selectedId); else next.delete('module');
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true });
  }, [cohortFilter, programmeFilter, search, searchParams, selectedId, setSearchParams, stateFilter]);

  const scopedCohorts = useMemo(
    () => cohortsForProgramme(cohorts, programmes, programmeFilter),
    [cohorts, programmes, programmeFilter],
  );
  // Only once the cohorts have loaded: before that the list is empty, and a
  // cohort restored from the URL would be dropped before it could match.
  useEffect(() => {
    if (!loaded || !cohortFilter) return;
    if (scopedCohorts.some(cohort => normaliseKey(cohort.id) === normaliseKey(cohortFilter))) return;
    setCohortFilter('');
  }, [cohortFilter, loaded, scopedCohorts]);

  const visibleRows = useMemo(() => {
    const scoped = new Set(
      modulesForScope(modules, groups, cohorts, programmes, {
        programmeId: programmeFilter,
        cohortId: cohortFilter,
      }).map(module => normaliseKey(moduleIdentity(module))),
    );
    return rows.filter(row => {
      if (!scoped.has(normaliseKey(row.catalogueId))) return false;
      // Modules with neither a meeting nor a session plan have nothing to show
      // here, and they are the bulk of the catalogue — the unfiltered view is
      // "modules whose Teams calendar is a live concern".
      if (!row.summary && !row.sessions.length) return false;
      if (stateFilter && row.state !== stateFilter) return false;
      return matchesSearch(search, [
        row.name, row.catalogueId, row.cohortName, row.groupName, row.programmeName,
        row.summary?.organizerEmail, row.summary?.liveSessionId,
      ]);
    });
  }, [cohortFilter, cohorts, groups, modules, programmeFilter, programmes, rows, search, stateFilter]);

  /**
   * Whose clock these times are.
   *
   * Every meeting is one absolute instant, and each person's Teams renders it in
   * that person's own timezone -- so a page printing the college calendar's
   * clock has to say so, and say how far the reader's own Teams will differ.
   * Without that, 09:00 here and 11:00 in the reader's Teams read as two
   * different meetings rather than one.
   */
  const timeZoneNote = useMemo(() => {
    if (!timeZoneLabel) return undefined;
    const base = `Meeting times are shown in the Microsoft calendar's timezone (${timeZoneLabel}).`;
    const { viewerZoneLabel, differenceMinutes } = viewerZoneOffset();
    if (!differenceMinutes) return base;
    const hours = Math.abs(differenceMinutes) / 60;
    const amount = `${Number.isInteger(hours) ? hours : hours.toFixed(1)} hour${hours === 1 ? '' : 's'}`;
    return `${base} Your device is on ${viewerZoneLabel}, so your own Teams shows them ${amount} ${differenceMinutes > 0 ? 'later' : 'earlier'}.`;
  }, [timeZoneLabel]);

  /**
   * What a highlighted row means, said once, above the rows it applies to.
   *
   * The Actions column fills a button in to mark a row that is waiting on
   * somebody. Unstated, that reads as decoration -- and the reader cannot look
   * the convention up from a colour. It is only worth saying while there is a
   * filled-in button to explain, so it appears exactly when one is on screen.
   */
  const listNote = useMemo(() => {
    const highlight = stats.drifted
      ? `${stats.drifted} module${stats.drifted === 1 ? '' : 's'} below ${stats.drifted === 1 ? 'has a' : 'have a'} highlighted Detail button: Teams is holding dates those modules have since changed. Open one and press Update Teams calendar to put it right.`
      : '';
    return [highlight, timeZoneNote].filter(Boolean).join(' ') || undefined;
  }, [stats.drifted, timeZoneNote]);

  const programmeOptions = useMemo(
    () => programmes.map(programme => ({ value: programmeIdentity(programme), label: programme.name })),
    [programmes],
  );


  // Skeletons only while there is genuinely nothing to show. Once both halves
  // have landed once, every later read is a refresh: the rows stay put and the
  // table's own progress bar says a read is in flight. That is what keeps a row
  // on screen while the action taken on it is saved and re-read.
  const listLoading = (loading && !loaded) || (teamsLoading && !teamsLoaded);
  const listRefreshing = !listLoading && (refreshing || teamsLoading);

  return (
    <WorkspaceShell
      role="curriculum"
      roleLabel="Curriculum Designer"
      navItems={curriculumNavItems}
      workspaceLabel="Curriculum Studio"
      pageTitle="Teams Meetings"
      pageSubtitle="Every module's Teams calendar, and whether it still matches the module's session dates"
      userName="Rachel Myers"
      userRole="Curriculum Designer"
    >
      <div className="min-h-full space-y-4 bg-background-50 p-4 sm:p-5 lg:p-6">
        <EntityHero
          eyebrow="Curriculum Studio · Modules"
          title="Teams Meetings"
          description="A module's stored session dates are the authority. This page sends them to the Microsoft Teams calendar — holiday shifts included — and brings back the attendance, transcripts and recordings of the meetings that have already run."
          loading={listLoading}
          stats={[
            { icon: 'ri-vidicon-line', label: 'Tracked meetings', value: stats.tracked },
            { icon: 'ri-check-double-line', label: 'In sync', value: stats.inSync },
            { icon: 'ri-error-warning-line', label: 'Dates differ', value: stats.drifted, detail: stats.drifted ? 'Send the session dates to Teams' : undefined },
            { icon: 'ri-calendar-line', label: 'No calendar yet', value: stats.toCreate },
          ]}
        />

        {error && <InlineError message={error} onRetry={() => void reload()} />}
        {teamsError && <InlineError message={teamsError} onRetry={() => void loadTeamsState()} />}
        {graphStatus === 'unconfigured' && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[12px] font-semibold text-amber-800">
            <AppIcon className="ri-information-line mr-1"></AppIcon>
            Microsoft Graph credentials are missing from the backend, so nothing here can reach the Teams calendar. The dates and attendance already stored are still shown.
          </div>
        )}
        {graphStatus === 'unknown' && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-background-200 bg-background-50 px-4 py-3 text-[12px] font-semibold text-foreground-600">
            <AppIcon className="ri-information-line mr-1"></AppIcon>
            The Teams calendar check did not finish, so this page could not confirm the backend is set up for Microsoft.
            Everything here still works &mdash; if the setup is missing, the action itself will say so.
            <button type="button" onClick={() => void checkGraphConfiguration()} className="font-bold text-primary-700 underline hover:text-primary-800">
              Check again
            </button>
          </div>
        )}
        {notice && !selected && (
          <div className={`flex items-start gap-2 rounded-xl border px-4 py-3 text-[12px] font-semibold ${
            notice.tone === 'error'
              ? 'border-red-200 bg-red-50 text-red-700'
              : notice.tone === 'warning'
                ? 'border-amber-200 bg-amber-50 text-amber-800'
                : 'border-primary-100 bg-primary-50 text-primary-700'
          }`}
          >
            <AppIcon className="ri-information-line mt-0.5"></AppIcon>
            <span className="min-w-0 flex-1">{notice.text}</span>
            <button type="button" onClick={() => setNotice(null)} className="shrink-0 text-[11px] font-bold underline">Dismiss</button>
          </div>
        )}

        <EntityFilterBar
          search={search}
          onSearch={setSearch}
          placeholder="Search modules, cohorts, organizers..."
          selects={[
            {
              label: 'Programme',
              value: programmeFilter,
              onChange: setProgrammeFilter,
              options: [{ value: '', label: 'All programmes' }, ...programmeOptions],
            },
            {
              label: 'Cohort',
              value: cohortFilter,
              onChange: setCohortFilter,
              options: [
                { value: '', label: 'All cohorts' },
                ...scopedCohorts.map(cohort => ({ value: cohort.id, label: cohort.name })),
              ],
            },
            {
              label: 'Calendar',
              value: stateFilter,
              onChange: setStateFilter,
              options: [
                { value: '', label: 'Any state' },
                { value: 'out-of-sync', label: 'Dates differ' },
                { value: 'in-sync', label: 'In sync' },
                { value: 'not-created', label: 'Not created' },
                { value: 'no-sessions', label: 'No sessions' },
                { value: 'unverified', label: 'Unable to verify' },
              ],
            },
          ]}
          onReset={() => { setSearch(''); setProgrammeFilter(''); setCohortFilter(''); setStateFilter(''); }}
          summary={listNote}
        />

        <EntityTable
          columns={COLUMNS}
          gridClass={GRID}
          rows={visibleRows}
          rowKey={row => row.catalogueId}
          getRowHref={row => `${namedCurriculumWorkspacePath('modules', row.catalogueId, row.name)}&tab=schedule`}
          loading={listLoading}
          refreshing={listRefreshing}
          empty={(
            <EntityEmptyState
              icon="ri-vidicon-line"
              title={rows.some(row => row.summary) ? 'No modules match these filters' : 'No Teams meetings tracked yet'}
              message={rows.some(row => row.summary)
                ? 'Clear a filter, or search for a different module.'
                : 'A module needs stored session dates before its Teams calendar can be created here.'}
            />
          )}
          renderRow={row => (
            <>
              <StackedCell
                href={`${namedCurriculumWorkspacePath('modules', row.catalogueId, row.name)}&tab=schedule`}
                primary={row.name}
                secondary={row.programmeName}
              />
              <StackedCell primary={row.cohortName} secondary={row.groupName} />
              <PlainCell>{cleanText(row.summary?.organizerEmail, '—')}</PlainCell>
              <PlainCell>{row.summary ? calendarLabel(row.summary.startDateTime) : '—'}</PlainCell>
              <PlainCell align="center">
                {row.sessions.length}
                {row.summary ? ` / ${row.summary.occurrenceCount}` : ''}
              </PlainCell>
              <span className="min-w-0 self-center">
                <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${STATE_TONES[row.state]}`}>
                  {STATE_LABELS[row.state]}
                </span>
                {row.state === 'out-of-sync' && (
                  <span className="mt-0.5 block truncate text-[10px] font-semibold text-amber-700">
                    {row.differingSessions === 1 ? '1 session differs' : `${row.differingSessions} sessions differ`}
                  </span>
                )}
              </span>
              {/* One way in per row. Every Teams action for a module \u2014 creating
                  the calendar, sending the dates, invitations, fetching what
                  ran \u2014 happens in the dialog this opens, so the row does not
                  carry a bank of buttons that are half disabled. */}
              <NamedActions
                actions={[
                  row.summary
                    ? {
                      icon: 'ri-eye-line',
                      label: 'Detail',
                      // Highlighted or not, the button says why. Colour alone asks
                      // the reader to already know the convention, and the state it
                      // stands for is exactly what they open this to find out.
                      title: row.state === 'out-of-sync'
                        ? `Needs attention: Teams is holding ${row.differingSessions} date${row.differingSessions === 1 ? '' : 's'} this module no longer has. Open to compare them and press Update Teams calendar.`
                        : 'Up to date: the Teams calendar is on this module\u2019s session dates. Open to see the two side by side.',
                      primary: row.state === 'out-of-sync',
                      onClick: () => setSelectedId(row.catalogueId),
                    }
                    : {
                      icon: 'ri-calendar-line',
                      label: 'Create Teams meetings calendar',
                      title: 'Create one Teams meeting on each of this module\u2019s stored session dates.',
                      primary: true,
                      onClick: () => setSelectedId(row.catalogueId),
                    },
                ]}
              />
            </>
          )}
        />

        <TeamsMeetingDialogs workspace={workspace} />
      </div>
    </WorkspaceShell>
  );
}
