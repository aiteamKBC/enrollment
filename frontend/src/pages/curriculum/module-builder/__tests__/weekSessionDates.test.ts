import { describe, expect, it } from 'vitest';
import {
  applyModuleWeekSessionPlan,
  groupWeeksByMonth,
  createEmptyComponent,
  createEmptyWeek,
  createLocalModuleDraft,
  liveSessionSlotOverflow,
  liveSessionSlotOverflowNotice,
  moduleDeliveryDaysPerWeek,
  moduleTeamsPlannedSessions,
  weekDeliverySlotCapacity,
  moduleWeekSessionDates,
  moduleWeekIdBySessionNumber,
  moduleUsesSessionRows,
  recalculateModule,
  resequenceWeekSessionDates,
  type ModuleCatalogueItem,
  type ModuleWeekSessionPlan,
} from '../moduleAuthoringData';

/**
 * Week N is session N of the module's own dated plan, so a week added in the
 * builder has to be given the next planned date — the next delivery day the
 * cohort has not closed for a holiday — and the module has to end up knowing it
 * runs a session more than it used to.
 *
 * The dates below are the ones the backend generates for a Saturday module that
 * starts on 12 December 2026 with Christmas closed, which is the module this was
 * reported against.
 */
const SATURDAY_DATES = [
  '2026-12-12', '2027-01-02', '2027-01-09', '2027-01-16',
  '2027-01-23', '2027-01-30', '2027-02-06',
];

function planFor(
  dates: string[],
  skippedBefore: Record<string, string[]> = {},
  slotDates: Record<string, string> = {},
): ModuleWeekSessionPlan {
  return {
    sessions: dates.map((date, index) => ({
      sessionNumber: index + 1,
      date,
      day: 'Saturday',
      slotDate: slotDates[date],
      slotDay: slotDates[date] ? 'Saturday' : undefined,
      skippedHolidays: skippedBefore[date] || [],
    })),
    skippedHolidays: Object.values(skippedBefore).flat(),
    finalEndDate: dates[dates.length - 1] || '',
    warnings: [],
  };
}

/** A six-week Saturday module, dated exactly as the structure payload serves it. */
function sixWeekModule(overrides: Partial<ModuleCatalogueItem> = {}): ModuleCatalogueItem {
  const draft = createLocalModuleDraft({
    programme: 'MSN',
    title: 'Fouda-ss',
    description: '',
    weeks: 6,
    status: 'draft',
    catalogueId: 'MOD-WEEKS',
    startDate: '2026-12-08',
    endDate: '2027-01-30',
  });
  return recalculateModule({
    ...draft,
    weekStructure: draft.weekStructure.map((week, index) => ({
      ...week,
      sessionDate: SATURDAY_DATES[index],
      sessionDay: 'Saturday',
    })),
    ...overrides,
  });
}

/** The same module with a seventh week added, as the Add week button adds one. */
function withSeventhWeek(module: ModuleCatalogueItem) {
  return recalculateModule({
    ...module,
    weekStructure: [...module.weekStructure, createEmptyWeek(module.id, module.weekStructure.length + 1)],
  });
}

describe('explicit session identity when non-teaching weeks move', () => {
  it('keeps booked dates in their authored slots when Teams numbering omits orientation and an extra event', () => {
    const dates = ['2026-09-11', '2026-09-18', '2026-09-25', '2026-10-02', '2026-10-09', '2026-10-16'];
    const draft = createLocalModuleDraft({ programme: 'Programme', title: 'Risk example', description: '', weeks: 6, status: 'draft' });
    const numbers = [0, 1, 2, 0, 3, 4];
    const module = { ...draft, weekStructure: draft.weekStructure.map((week, index) => {
      const live = createEmptyComponent(week.id, 'live-session', 1);
      live.settings = { ...live.settings, teamsLiveSessionId: 'confirmed-series', teamsSessionNumber: numbers[index], sessionDate: dates[index], sessionDay: 'Friday' };
      return { ...week, components: numbers[index] ? [live] : [] };
    }) };
    const originalSettings = module.weekStructure.map(week => week.components[0]?.settings);
    const result = applyModuleWeekSessionPlan(module, planFor(dates));
    expect(result.weekStructure.map(week => week.sessionDate)).toEqual(dates);
    expect(groupWeeksByMonth(result.weekStructure).map(group => group.key)).toEqual(['2026-09', '2026-10']);
    expect(result.weekStructure.map(week => week.components[0]?.settings)).toEqual(originalSettings);
  });

  function movedModule(sessionNumber: number) {
    const module = sixWeekModule();
    const teachingWeek = module.weekStructure[0];
    const live = createEmptyComponent(teachingWeek.id, 'live-session', 1);
    live.settings = { ...live.settings, teamsSessionNumber: sessionNumber };
    const orientation = createEmptyWeek(module.id, 1);
    return {
      ...module,
      weekStructure: [orientation, { ...teachingWeek, components: [live] }, ...module.weekStructure.slice(2)],
    };
  }

  it('keeps an unbooked numbered session on its original date after orientation is moved first', () => {
    const module = movedModule(1);
    const result = applyModuleWeekSessionPlan(module, planFor(SATURDAY_DATES));
    const live = result.weekStructure[1].components[0];
    expect(live.id).toBe(module.weekStructure[1].components[0].id);
    expect(live.settings.sessionDate).toBe(SATURDAY_DATES[0]);
    expect(result.weekStructure[1].sessionDate).toBe(live.settings.sessionDate);
    expect(result.weekStructure[1].sessionDay).toBe(live.settings.sessionDay);
    expect(live.settings.teamsSessionNumber).toBe(1);
    expect(live.settings.teamsLiveSessionId).toBeFalsy();
    expect(applyModuleWeekSessionPlan(result, planFor(SATURDAY_DATES))).toEqual(result);
  });

  // Agreed 2026-10-25: the heading follows the plan at once; the booked session
  // keeps its Teams date (and shows its drift) until Update Teams moves it.
  it('moves the heading to the plan while a Teams-confirmed session keeps its date', () => {
    const module = movedModule(1);
    const live = module.weekStructure[1].components[0];
    live.settings = {
      ...live.settings,
      teamsLiveSessionId: 'confirmed-meeting',
      sessionDate: '2026-09-26',
      sessionDay: 'Saturday',
    };
    const result = applyModuleWeekSessionPlan(module, planFor(SATURDAY_DATES));
    expect(result.weekStructure[1].components[0]).toBe(live);
    expect(result.weekStructure[1].components[0].settings.sessionDate).toBe('2026-09-26');
    expect(result.weekStructure[1].sessionDate).toBe(SATURDAY_DATES[0]);
    expect(result.weekStructure[1].sessionDay).toBe('Saturday');
    expect(applyModuleWeekSessionPlan(result, planFor(SATURDAY_DATES))).toBe(result);
  });

  it('falls back to the week slot if the numbered session has been removed from the plan', () => {
    const result = applyModuleWeekSessionPlan(movedModule(99), planFor(SATURDAY_DATES));
    expect(result.weekStructure[1].components[0].settings.sessionDate).toBe(SATURDAY_DATES[1]);
    expect(result.weekStructure[1].sessionDate).toBe(SATURDAY_DATES[1]);
  });

  it('keeps ordinary unnumbered sessions following their week', () => {
    const result = applyModuleWeekSessionPlan(movedModule(0), planFor(SATURDAY_DATES));
    expect(result.weekStructure[1].components[0].settings.sessionDate).toBe(SATURDAY_DATES[1]);
    expect(result.weekStructure[1].sessionDate).toBe(SATURDAY_DATES[1]);
  });
});

describe('dating a week added in the builder', () => {
  it('schedules all sixteen imported live sessions without consuming a date for the holiday row', () => {
    const draft = createLocalModuleDraft({ programme: 'Al Fanar', title: 'PCP', description: '', weeks: 1, sessionsNumber: 3, status: 'draft' });
    const weeks = Array.from({ length: 16 }, (_, index) => {
      const week = createEmptyWeek(draft.id, index + 1);
      return { ...week, components: [createEmptyComponent(week.id, 'live-session', 1)] };
    });
    const holiday = createEmptyWeek(draft.id, 3);
    weeks.splice(2, 0, { ...holiday, title: 'Saudi National Day', components: [createEmptyComponent(holiday.id, 'reading', 1)] });
    const module = recalculateModule({ ...draft, weeklySchedule: [
      { day: 'Monday', startTime: '11:00', endTime: '14:00' },
      { day: 'Wednesday', startTime: '11:30', endTime: '14:30' },
    ], weekStructure: weeks });
    expect(module.sessionsNumber).toBe(16);
    expect(moduleUsesSessionRows(module)).toBe(true);
    const dates = ['2026-09-16', '2026-09-21', '2026-10-05', '2026-10-07', '2026-10-12', '2026-10-14', '2026-10-19', '2026-10-21', '2026-10-26', '2026-10-28', '2026-11-02', '2026-11-04', '2026-11-09', '2026-11-11', '2026-11-16', '2026-11-18'];
    const plan = planFor(dates);
    const dated = applyModuleWeekSessionPlan(module, plan);
    expect(dated.weekStructure).toHaveLength(17);
    expect(dated.weekStructure[2].components[0].id).toBe(weeks[2].components[0].id);
    expect(moduleWeekSessionDates(dated, plan.sessions).flat()).toEqual(dates);
    expect(moduleWeekSessionDates(dated, plan.sessions)[2]).toEqual([]);
    expect(moduleWeekIdBySessionNumber(dated, plan.sessions).get(16)).toBe(weeks[16].id);
    expect(dated.weekStructure.flatMap(week => week.components.filter(c => c.type === 'live-session').map(c => c.settings.sessionDate))).toEqual(dates);
  });

  it('gives two live sessions in one week their distinct delivery dates', () => {
    const draft = createLocalModuleDraft({
      programme: 'Split Programme',
      title: 'Monday and Wednesday module',
      description: '',
      weeks: 2,
      sessionsNumber: 4,
      status: 'draft',
      catalogueId: 'MOD-TWICE-WEEKLY',
      startDate: '2026-09-07',
    });
    const module = {
      ...draft,
      weekStructure: draft.weekStructure.map(week => ({
        ...week,
        components: [
          createEmptyComponent(week.id, 'live-session', 1),
          createEmptyComponent(week.id, 'live-session', 2),
        ],
      })),
    };

    const dated = applyModuleWeekSessionPlan(module, planFor([
      '2026-09-07', '2026-09-09', '2026-09-14', '2026-09-16',
    ]));
    // Read off each component's OWN date, never `week.sessionDate`: the week
    // header shows its FIRST delivery day, so falling back to it would show
    // the week's second live session sitting on the first one's date.
    const componentDates = dated.weekStructure.flatMap(week => (
      week.components.map(component => String(component.settings.sessionDate || ''))
    ));

    // A Mon+Wed group delivers twice a week, so each week has two planned
    // slots and its two live sessions take one each, in delivery order.
    expect(componentDates).toEqual(['2026-09-07', '2026-09-09', '2026-09-14', '2026-09-16']);
    expect(dated.weekStructure.map(week => week.sessionDate)).toEqual(['2026-09-07', '2026-09-14']);
  });

  it('gives the added week the next planned date and leaves the others put', () => {
    const added = withSeventhWeek(sixWeekModule());

    // Before the plan is read back the new week has no date at all — which is
    // the bug this exists to close.
    expect(added.weekStructure[6].sessionDate).toBeUndefined();

    const dated = applyModuleWeekSessionPlan(added, planFor(SATURDAY_DATES));

    expect(dated.weekStructure.map(week => week.sessionDate)).toEqual(SATURDAY_DATES);
    expect(dated.weekStructure[6].sessionDay).toBe('Saturday');
  });

  it('takes the date the plan moved off a holiday, not the closed day', () => {
    // The fourteenth Saturday of this module falls inside a closed Easter, so
    // the week that reaches it runs the Saturday after.
    const dates = [...SATURDAY_DATES.slice(0, 6), '2027-04-03'];
    const dated = applyModuleWeekSessionPlan(
      withSeventhWeek(sixWeekModule()),
      planFor(dates, { '2027-04-03': ['2027-03-27'] }),
    );

    expect(dated.weekStructure[6].sessionDate).toBe('2027-04-03');
  });

  it('shows the week on the delivered replacement date when a holiday moved it', () => {
    const module = sixWeekModule({ weekStructure: [sixWeekModule().weekStructure[0]] });
    const plan = planFor(
      ['2027-01-08'],
      { '2027-01-08': ['2026-12-25', '2027-01-01'] },
      { '2027-01-08': '2026-12-25' },
    );

    const dated = applyModuleWeekSessionPlan(module, plan);

    expect(dated.weekStructure[0].sessionDate).toBe('2027-01-08');
    expect(moduleWeekSessionDates(dated, plan.sessions)[0]).toEqual(['2027-01-08']);
  });

  it('moves the end date out when it was the plan’s own last session', () => {
    const dated = applyModuleWeekSessionPlan(withSeventhWeek(sixWeekModule()), planFor(SATURDAY_DATES));

    // 30 January was the last of six sessions; seven sessions finish a week later.
    expect(dated.endDate).toBe('2027-02-06');
  });

  it('leaves an end date that was set by hand exactly where it was put', () => {
    const module = withSeventhWeek(sixWeekModule({ endDate: '2027-06-30' }));

    const dated = applyModuleWeekSessionPlan(module, planFor(SATURDAY_DATES));

    expect(dated.endDate).toBe('2027-06-30');
    expect(dated.weekStructure[6].sessionDate).toBe('2027-02-06');
  });

  it('leaves the end date alone when it is only dating a module on sight', () => {
    // Opening a module is not an edit to when it finishes, so the dates are
    // applied and the end date is not.
    const module = withSeventhWeek(sixWeekModule());

    const dated = applyModuleWeekSessionPlan(module, planFor(SATURDAY_DATES), { followEndDate: false });

    expect(dated.weekStructure[6].sessionDate).toBe('2027-02-06');
    expect(dated.endDate).toBe('2027-01-30');
  });

  it('changes nothing when the weeks already run to the plan', () => {
    // Applied on every open, so a plan the module already agrees with must not
    // come back as an edit waiting to be saved.
    const module = sixWeekModule();

    expect(applyModuleWeekSessionPlan(module, planFor(SATURDAY_DATES.slice(0, 6)))).toBe(module);
  });

  it('reports the week count from the weeks that exist', () => {
    // The module form reopens on this number: a module whose weeks went from six
    // to seven has to offer seven, or the next save puts the sixth week back.
    expect(sixWeekModule().weeks).toBe(6);
    expect(withSeventhWeek(sixWeekModule()).weeks).toBe(7);
  });

  it('leaves the calendar session count alone when a week is added', () => {
    // Sessions are not weeks: a group delivering twice a week runs two sessions
    // per authored week, so re-deriving this from the structure would halve the
    // session plan and the Teams series.
    const module = { ...sixWeekModule(), sessionsNumber: 12 };

    expect(withSeventhWeek(module).sessionsNumber).toBe(12);
  });
});

describe('reordering the weeks of a dated module', () => {
  it('leaves the timetable where it is and moves only what is taught', () => {
    const weeks = sixWeekModule().weekStructure;
    const moved = [weeks[3], ...weeks.slice(0, 3), ...weeks.slice(4)];

    const resequenced = resequenceWeekSessionDates(moved);

    expect(resequenced.map(week => week.sessionDate)).toEqual(SATURDAY_DATES.slice(0, 6));
    // The week that was dragged to the top now runs on the first session date.
    expect(resequenced[0].id).toBe(weeks[3].id);
  });

  it('keeps undated weeks at the end of the run', () => {
    const module = withSeventhWeek(sixWeekModule());
    const moved = [module.weekStructure[6], ...module.weekStructure.slice(0, 6)];

    const resequenced = resequenceWeekSessionDates(moved);

    expect(resequenced.slice(0, 6).map(week => week.sessionDate)).toEqual(SATURDAY_DATES.slice(0, 6));
    expect(resequenced[6].sessionDate).toBeUndefined();
  });
});

/**
 * A plan as the backend now generates it: every delivery day stamped with the
 * Monday-to-Sunday window it falls in, counting the module's own starting week
 * as week 1.
 */
function mondayWeekPlan(
  entries: Array<{ date: string; weekNumber: number; day?: string }>,
): ModuleWeekSessionPlan {
  return {
    sessions: entries.map((entry, index) => ({
      sessionNumber: index + 1,
      weekNumber: entry.weekNumber,
      date: entry.date,
      day: entry.day || 'Monday',
      skippedHolidays: [],
    })),
    skippedHolidays: [],
    finalEndDate: entries[entries.length - 1]?.date || '',
    warnings: [],
  };
}

/** A module of `weekCount` empty weeks, each carrying `liveSessions` of them. */
function weeksCarryingLiveSessions(weekCount: number, liveSessions: number[]): ModuleCatalogueItem {
  const draft = createLocalModuleDraft({
    programme: 'Monday Anchor',
    title: 'Mon+Fri',
    description: '',
    weeks: weekCount,
    status: 'draft',
  });
  return recalculateModule({
    ...draft,
    weeklySchedule: [
      { day: 'Monday', startTime: '09:00', endTime: '11:00' },
      { day: 'Friday', startTime: '09:00', endTime: '11:00' },
    ],
    weekStructure: Array.from({ length: weekCount }, (_, index) => {
      const week = createEmptyWeek(draft.id, index + 1);
      return {
        ...week,
        components: Array.from(
          { length: liveSessions[index] ?? 0 },
          (_unused, offset) => createEmptyComponent(week.id, 'live-session', offset + 1),
        ),
      };
    }),
  });
}

describe('an authored week is a calendar week', () => {
  it('gives a week only the delivery days inside its own Monday-to-Sunday window', () => {
    // Mon+Fri, starting Monday 3 August 2026. Two full weeks, two dates each.
    const module = weeksCarryingLiveSessions(2, [2, 2]);
    const plan = mondayWeekPlan([
      { date: '2026-08-03', weekNumber: 1, day: 'Monday' },
      { date: '2026-08-07', weekNumber: 1, day: 'Friday' },
      { date: '2026-08-10', weekNumber: 2, day: 'Monday' },
      { date: '2026-08-14', weekNumber: 2, day: 'Friday' },
    ]);

    expect(moduleWeekSessionDates(module, plan.sessions)).toEqual([
      ['2026-08-03', '2026-08-07'],
      ['2026-08-10', '2026-08-14'],
    ]);
  });

  it('leaves a mid-week start with a short first week instead of sliding the run', () => {
    // The same Mon+Fri group, but the module starts on Wednesday 5 August. The
    // Monday of that week is before the module began, so week 1 delivers on the
    // Friday alone -- and week 2 still starts on ITS own Monday rather than
    // borrowing the date week 1 never had.
    const module = weeksCarryingLiveSessions(2, [1, 2]);
    const plan = mondayWeekPlan([
      { date: '2026-08-07', weekNumber: 1, day: 'Friday' },
      { date: '2026-08-10', weekNumber: 2, day: 'Monday' },
      { date: '2026-08-14', weekNumber: 2, day: 'Friday' },
    ]);

    expect(moduleWeekSessionDates(module, plan.sessions)).toEqual([
      ['2026-08-07'],
      ['2026-08-10', '2026-08-14'],
    ]);
    // Week 2's first live session runs on week 2's own Monday, not on the
    // Friday that week 1 delivered.
    const dated = applyModuleWeekSessionPlan(module, plan);
    expect(dated.weekStructure[1].components[0].settings.sessionDate).toBe('2026-08-10');
  });

  it('dates a live session added to a week from that week alone', () => {
    // Week 2's FIRST live session takes week 2's own date -- never week 3's,
    // and never a date already spoken for. A second live session added to the
    // week is an additional one: the week's date is the primary's, so this one
    // waits for a date of its own rather than taking the week's spare delivery
    // day and moving every time the week moves.
    const plan = mondayWeekPlan([
      { date: '2026-08-03', weekNumber: 1, day: 'Monday' },
      { date: '2026-08-07', weekNumber: 1, day: 'Friday' },
      { date: '2026-08-10', weekNumber: 2, day: 'Monday' },
      { date: '2026-08-14', weekNumber: 2, day: 'Friday' },
    ]);

    const before = applyModuleWeekSessionPlan(weeksCarryingLiveSessions(2, [2, 1]), plan);
    expect(before.weekStructure[1].components.map(c => c.settings.sessionDate)).toEqual(['2026-08-10']);

    const after = applyModuleWeekSessionPlan(weeksCarryingLiveSessions(2, [2, 2]), plan);
    expect(after.weekStructure[1].components.map(c => c.settings.sessionDate))
      .toEqual(['2026-08-10', '2026-08-14']);
  });

  it('leaves a live session over-authored beyond its week undated', () => {
    // Three live sessions in one week. The week's date belongs to the first of
    // them; the other two are additional and have no date until somebody gives
    // them one. Inventing one -- from the week's spare delivery day or from
    // anywhere else -- would put a real Teams meeting on a day nobody chose.
    const plan = mondayWeekPlan([
      { date: '2026-08-03', weekNumber: 1, day: 'Monday' },
      { date: '2026-08-07', weekNumber: 1, day: 'Friday' },
      { date: '2026-08-10', weekNumber: 2, day: 'Monday' },
      { date: '2026-08-14', weekNumber: 2, day: 'Friday' },
    ]);

    const dated = applyModuleWeekSessionPlan(weeksCarryingLiveSessions(2, [3, 2]), plan);

    // The additional ones keep the empty date they were created with, which is
    // the "No date" the rail reports and the reason they are not sent to Teams.
    expect(dated.weekStructure[0].components.map(c => c.settings.sessionDate))
      .toEqual(['2026-08-03', '2026-08-07', '']);
    // The extra sessions did not eat week 2's Monday.
    expect(dated.weekStructure[1].components[0].settings.sessionDate).toBe('2026-08-10');
  });

  it('gives a week the group never delivers in no dates at all', () => {
    // The group pauses for week 2. That week is still a week -- its reading and
    // its assignment stand -- it simply delivers no live session, and week 3
    // keeps its own dates rather than moving up into the gap.
    const module = weeksCarryingLiveSessions(3, [1, 0, 1]);
    const plan = mondayWeekPlan([
      { date: '2026-08-03', weekNumber: 1, day: 'Monday' },
      { date: '2026-08-17', weekNumber: 3, day: 'Monday' },
    ]);

    expect(moduleWeekSessionDates(module, plan.sessions)).toEqual([
      ['2026-08-03'],
      [],
      ['2026-08-17'],
    ]);
    const dated = applyModuleWeekSessionPlan(module, plan);
    expect(dated.weekStructure[2].components[0].settings.sessionDate).toBe('2026-08-17');
  });
});

/**
 * What the Teams dialog is offered to put on the calendar.
 *
 * The same slot rule the structure walk above applies, because this is the
 * list Create and Update actually send. The two disagreeing is how a module
 * with thirteen dated weeks told the Teams dialog it had no sessions.
 */
describe('moduleTeamsPlannedSessions pairs each live session with its own delivery slot', () => {
  // One week, `dates` live sessions in authored order carrying those stored
  // dates ('' for one nobody has dated).
  const weekOf = (dates: string[]): ModuleCatalogueItem => {
    const draft = createLocalModuleDraft({
      programme: 'Delivery slots',
      title: 'One week, several live sessions',
      description: '',
      weeks: 1,
      status: 'draft',
      catalogueId: 'MOD-DELIVERY-SLOTS',
      startDate: '2026-10-05',
    });
    const week = draft.weekStructure[0];
    return {
      ...draft,
      weekStructure: [{
        ...week,
        components: dates.map((date, index) => ({
          ...createEmptyComponent(week.id, 'live-session', index + 1),
          id: `COMP-${index + 1}`,
          settings: { sessionDate: date, sessionTime: '09:00' },
        })),
      }],
    } as ModuleCatalogueItem;
  };
  const resolved = (module: ModuleCatalogueItem, plan: ModuleWeekSessionPlan) => (
    moduleTeamsPlannedSessions(module, plan).map(session => [session.componentId, session.date])
  );
  // A Monday-only group: one slot a week.
  const mondayOnly = mondayWeekPlan([{ date: '2026-10-05', weekNumber: 1, day: 'Monday' }]);
  // A Mon+Fri group: two slots a week, in the calendar order the scheduler
  // emits them, never the order the delivery days were listed in.
  const mondayAndFriday = mondayWeekPlan([
    { date: '2026-10-05', weekNumber: 1, day: 'Monday' },
    { date: '2026-10-09', weekNumber: 1, day: 'Friday' },
  ]);

  it('gives a one-day group its single slot and leaves the second session on its own date', () => {
    expect(resolved(weekOf(['2026-10-05', '2026-10-07']), mondayOnly)).toEqual([
      ['COMP-1', '2026-10-05'],
      ['COMP-2', '2026-10-07'],
    ]);
  });

  it('gives a two-day group both slots, one per live session, in delivery order', () => {
    expect(resolved(weekOf(['', '']), mondayAndFriday)).toEqual([
      ['COMP-1', '2026-10-05'],
      ['COMP-2', '2026-10-09'],
    ]);
  });

  it('moves both planned sessions of a two-day week onto their own new slots', () => {
    // The week moves on by one: Monday 12 Oct and Friday 16 Oct. The third
    // session was put on the Wednesday deliberately and does not move.
    const moved = mondayWeekPlan([
      { date: '2026-10-12', weekNumber: 1, day: 'Monday' },
      { date: '2026-10-16', weekNumber: 1, day: 'Friday' },
    ]);
    expect(resolved(weekOf(['2026-10-05', '2026-10-09', '2026-10-07']), moved)).toEqual([
      ['COMP-3', '2026-10-07'],
      ['COMP-1', '2026-10-12'],
      ['COMP-2', '2026-10-16'],
    ]);
  });

  it('leaves a third live session of a two-day week on its own manual date', () => {
    // Date order, which is the order the calendar is built in.
    expect(resolved(weekOf(['', '', '2026-10-07']), mondayAndFriday)).toEqual([
      ['COMP-1', '2026-10-05'],
      ['COMP-3', '2026-10-07'],
      ['COMP-2', '2026-10-09'],
    ]);
  });

  it('keeps an undated additional session out of the calendar', () => {
    // Undated, it must not borrow a delivery slot or another session's day --
    // either would book a second Teams meeting on a day nobody chose.
    const planned = moduleTeamsPlannedSessions(weekOf(['', '', '']), mondayAndFriday);
    expect(planned.filter(session => session.date).map(session => session.componentId))
      .toEqual(['COMP-1', 'COMP-2']);
    expect(planned.find(session => session.componentId === 'COMP-3')?.date).toBe('');
  });

  it('never lets a stale stored date outvote a delivery slot', () => {
    expect(resolved(weekOf(['2026-08-03', '2026-08-07']), mondayAndFriday)).toEqual([
      ['COMP-1', '2026-10-05'],
      ['COMP-2', '2026-10-09'],
    ]);
  });
});

/**
 * Adding more live sessions to a week than its group delivers days for.
 *
 * The extras are allowed — an unplanned live session is a legitimate thing to
 * author — but they have no date to run on, so the builder asks before it
 * happens rather than leaving the author to find "No date yet" afterwards.
 * Same slot arithmetic as the date resolution above, so the warning and the
 * behaviour can never disagree.
 */
describe('liveSessionSlotOverflow warns when a week runs out of delivery days', () => {
  // `live` live sessions already in week 1, out of a two-week module.
  const moduleWithWeekOne = (live: number): ModuleCatalogueItem => {
    const draft = createLocalModuleDraft({
      programme: 'Slot overflow',
      title: 'Overflow',
      description: '',
      weeks: 2,
      sessionsNumber: 2,
      status: 'draft',
      catalogueId: 'MOD-OVERFLOW',
      startDate: '2026-10-05',
    });
    return {
      ...draft,
      weekStructure: draft.weekStructure.map((week, index) => (index === 0 ? {
        ...week,
        components: Array.from({ length: live }, (_unused, offset) => (
          createEmptyComponent(week.id, 'live-session', offset + 1)
        )),
      } : week)),
    } as ModuleCatalogueItem;
  };
  const oneDayAWeek = mondayWeekPlan([
    { date: '2026-10-05', weekNumber: 1, day: 'Monday' },
    { date: '2026-10-12', weekNumber: 2, day: 'Monday' },
  ]);
  const twoDaysAWeek = mondayWeekPlan([
    { date: '2026-10-05', weekNumber: 1, day: 'Monday' },
    { date: '2026-10-09', weekNumber: 1, day: 'Friday' },
    { date: '2026-10-12', weekNumber: 2, day: 'Monday' },
    { date: '2026-10-16', weekNumber: 2, day: 'Friday' },
  ]);

  it('says nothing while the week still has a delivery day free', () => {
    expect(liveSessionSlotOverflow(moduleWithWeekOne(0), 0, 1, oneDayAWeek.sessions)).toBeNull();
    expect(liveSessionSlotOverflow(moduleWithWeekOne(1), 0, 1, twoDaysAWeek.sessions)).toBeNull();
  });

  it('warns on the second live session of a one-delivery-day week', () => {
    expect(liveSessionSlotOverflow(moduleWithWeekOne(1), 0, 1, oneDayAWeek.sessions)).toEqual({
      weekNumber: 1, capacity: 1, existing: 1, adding: 1, beyond: 1,
    });
  });

  it('warns only on the third live session of a two-delivery-day week', () => {
    expect(liveSessionSlotOverflow(moduleWithWeekOne(2), 0, 1, twoDaysAWeek.sessions)).toEqual({
      weekNumber: 1, capacity: 2, existing: 2, adding: 1, beyond: 1,
    });
  });

  it('counts how many of a batch would be left without a date', () => {
    // Adding three at once to an empty week a one-day group delivers: the
    // first takes the Monday, the other two have nowhere to go.
    expect(liveSessionSlotOverflow(moduleWithWeekOne(0), 0, 3, oneDayAWeek.sessions)).toMatchObject({
      capacity: 1, existing: 0, adding: 3, beyond: 2,
    });
  });

  it('warns about any live session in a week the group never delivers in', () => {
    // Week 2 is paused: the plan gives it no delivery day at all, which is a
    // real zero and not a plan that has yet to be read.
    const paused = mondayWeekPlan([
      { date: '2026-10-05', weekNumber: 1, day: 'Monday' },
      { date: '2026-10-19', weekNumber: 3, day: 'Monday' },
    ]);
    expect(liveSessionSlotOverflow(moduleWithWeekOne(0), 1, 1, paused.sessions))
      .toMatchObject({ weekNumber: 2, capacity: 0, beyond: 1 });
  });

  it('falls back to the delivery pattern for a week the plan has not reached', () => {
    // A week just added in the builder, or a template copy appended a moment
    // ago. Reading the plan's silence as "no delivery days" would call every
    // live session in it unplanned.
    const module = moduleWithWeekOne(0);
    expect(weekDeliverySlotCapacity(module, 5, oneDayAWeek.sessions))
      .toBe(moduleDeliveryDaysPerWeek(module));
    expect(weekDeliverySlotCapacity(module, 0, oneDayAWeek.sessions)).toBe(1);
  });

  it('says what the group delivers and what it leaves undated', () => {
    const one = liveSessionSlotOverflowNotice({
      weekNumber: 3, capacity: 1, existing: 1, adding: 1, beyond: 1,
    });
    expect(one.title).toBe('This live session will have no date');
    expect(one.text).toContain('delivers on one day a week');
    expect(one.text).toContain('week 3 has one planned date');
    expect(one.text).toContain('No date yet');
    expect(one.text).toContain('Teams calendar');

    const many = liveSessionSlotOverflowNotice({
      weekNumber: 2, capacity: 2, existing: 2, adding: 2, beyond: 2,
    });
    expect(many.title).toBe('2 of these live sessions will have no date');
    expect(many.text).toContain('delivers on 2 days a week');
  });
});
