import type { TrainingPlanDashboard, PlanSession, PlanCurriculumSlot, PlanSlotHoliday } from '@/api/trainingPlanDashboard';
import type { Subject } from '../my-learning/SubjectWorkspace';
import type { PlanSubjectSummary } from '@/api/learnerOverview';

export function dateKey(value: string | null | undefined) {
  if (!value) return '';
  const date = value.slice(0, 10);
  const parsed = Date.parse(date);
  return /^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(parsed) && new Date(parsed).toISOString().startsWith(date) ? date : '';
}
export function weekKey(value: string) {
  const key = dateKey(value);
  if (!key) return '';
  const date = new Date(`${key}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  return date.toISOString().slice(0, 10);
}
export function sessionDay(value: string) {
  return Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleDateString('en-CA', { timeZone: 'Europe/London' }) : '';
}
export function percent(done: number, total: number) { return total > 0 ? Math.min(100, Math.round(done / total * 10000) / 100) : 0; }
export function reviewDate(review: TrainingPlanDashboard['reviews'][number]) { return review.scheduledDate || review.targetDate || review.date || ''; }

export function timelineYears(dates: string[], currentYear: number, selectedYear: number) {
  const years = [currentYear, selectedYear, ...dates.map(dateKey).filter(Boolean).map(date => Number(date.slice(0, 4)))];
  const first = Math.min(...years), last = Math.max(...years);
  return Array.from({ length: last - first + 1 }, (_, index) => first + index);
}

export function reviewToBook(reviews: TrainingPlanDashboard['reviews']) {
  return reviews.filter(review => review.source === 'progress-review' && review.status === 'not-scheduled' && dateKey(reviewDate(review)))
    .sort((a, b) => reviewDate(a).localeCompare(reviewDate(b)))[0] || null;
}

export function buildPlanModules(subjects: (Subject | PlanSubjectSummary)[], data: TrainingPlanDashboard) {
  return subjects.map(subject => {
    const initialSummary = 'activities' in subject ? null : subject;
    const activities = 'activities' in subject ? subject.activities : [];
    // Imported courses carry an explicit curriculum link in student-activity.
    // The schedule's moduleLinks only contains current subjects, so retain that
    // link when resolving the course's hours, attendance and KSB evidence.
    const moduleId = data.moduleLinks[subject.id]?.id
      || ('activities' in subject ? subject.moduleId : null)
      || (subject.id.startsWith('current:') ? subject.id.slice(8) : null);
    // Reuse the already-loaded overview summary for current modules too. The
    // detail panel receives these summaries, while the timeline previously
    // only used them for legacy subjects, dropping direct recorded hours and
    // canonical activity/KSB values for selected current modules.
    const summary = initialSummary || data.planSubjects?.find(item =>
      item.id === subject.id || (moduleId != null && item.moduleIds.includes(moduleId)
        && item.id.startsWith('legacy:') === subject.id.startsWith('legacy:')));
    const detail = data.modules.find(module => module.id === moduleId);
    const sessions = data.sessions.filter(session => session.moduleId === moduleId).map(session => {
      const matches = activities.filter(activity => Date.parse(activity.native?.sessionDateTimeUtc || '') === Date.parse(session.start)
        || (activity.category === 'live_session' && activity.schedule.date === sessionDay(session.start)));
      if (summary) {
        const titles = summary.sessionTitles.filter(item => item.date === sessionDay(session.start));
        return titles.length === 1 ? { ...session, title: titles[0].title } : session;
      }
      return matches.length === 1 ? { ...session, title: matches[0].title } : session;
    });
    const activityDates = summary?.dates || activities.filter(a => !a.schedule.date_needs_review).map(a => dateKey(a.schedule.date)).filter(Boolean);
    const dates = [...new Set([...activityDates, ...sessions.map(session => sessionDay(session.start))])].filter(Boolean).sort();
    const start = dateKey(detail?.start_date) || dates[0] || '';
    const end = dateKey(detail?.end_date) || dates.at(-1) || start;
    const done = summary?.completed ?? activities.filter(activity => activity.completed).length;
    const activityCount = summary?.total ?? activities.length;
    const groupId = subject.id.startsWith('legacy:') ? subject.id.slice(7) : '';
    const actual = data.actual.filter(row => groupId && row.groupId === groupId);
    const activityCounts = summary?.activityCounts || activities.reduce<Record<string, number>>((counts, activity) => {
      counts[activity.category] = (counts[activity.category] || 0) + 1;
      return counts;
    }, {});
    const ksbCodes = summary?.ksbCodes || [...new Set(activities.flatMap(activity =>
      (activity.native?.ksbMappings || []).map(mapping => mapping.code).filter(Boolean)))].sort();
    // Achievement comes from accepted learner evidence, not curriculum mappings.
    const entryProgress = moduleId ? data.moduleProgress?.[moduleId] : undefined;
    const ksbProgress = entryProgress?.ksb ?? summary?.ksbProgress ?? null;
    const historicalHours = actual.length ? actual.reduce((sum, row) => sum + row.hours, 0) : null;
    const summaryHours = summary?.directHours != null
      ? groupId ? data.actualAvailable ? (historicalHours || 0) + summary.directHours : null : summary.directHours
      : historicalHours;
    const recordedHours = entryProgress?.hours.actual ?? summaryHours;
    return { ...subject, activities, activityCount, activityCounts, ksbCodes,
      monthlyActivities: summary?.monthlyActivities,
      achievedKsbCodes: entryProgress?.ksb.achievedCodes ?? null,
      ksbMappingMissing: summary ? summary.ksbMappingMissing : activities.some(activity => !activity.native?.ksbMappings),
      moduleId, detail, sessions, dates, start, end,
      weeks: new Set(dates.map(weekKey)).size, done, progress: percent(done, activityCount), ksbProgress,
      attendanceProgress: entryProgress?.attendance, actual: recordedHours };
  });
}
export type TimelineModule = ReturnType<typeof buildPlanModules>[number];

/** The fields rendered by the timeline and module cards; activity bodies stay in My Learning. */
export type DisplayTimelineModule = Pick<TimelineModule,
  'id' | 'title' | 'moduleId' | 'start' | 'end' | 'progress' | 'done' | 'activityCount' |
  'activityCounts' | 'ksbCodes' | 'achievedKsbCodes' | 'ksbMappingMissing' | 'weeks' | 'actual' | 'detail' | 'sessions'> & {
  activities: Array<{ id: string; title: string; completed: boolean }>;
  weekAnchor?: string;
};

/**
 * The last date a module's bar has to cover: the delivery that actually happens.
 *
 * A closed delivery day moves the module's last session past its stored end
 * date, so a bar drawn to `end` alone stops short of the final session it is
 * meant to contain. `effectiveEndDate` is the scheduler's own answer -- the
 * date of the last DELIVERED session, carried on the payload by
 * `attach_curriculum_slots`. Nothing is recomputed here, no holiday is read,
 * and no stored date is written: this is display only.
 *
 * It can only ever push the bar later. The scheduler states a DELIVERY end, not
 * an authoring end, and the two disagree in both directions on real data: a
 * module authored to run to October while teaching its last session in June has
 * an earlier `effectiveEndDate` for reasons no holiday caused, and letting that
 * win would collapse bars a closure never touched.
 */
export function moduleVisualEnd(module: { end: string; detail?: { effectiveEndDate?: string } }) {
  const delivered = dateKey(module.detail?.effectiveEndDate);
  return delivered > module.end ? delivered : module.end;
}

/**
 * One row of the learner's curriculum timeline: a taught slot, always.
 *
 * `holidays` names any ticked holiday landing on this slot's own day. It moves
 * nothing -- the session still runs, and `holidays` is only a heads-up so the
 * learner is not surprised by a quiet room on a day the calendar shows a
 * closure. Empty for every ordinary slot.
 */
export type CurriculumRow =
  | { kind: 'session'; slotNumber: number; date: string; sessionNumber: number;
      title: string; start: string | null; minutes: number | null; attended: boolean | null; joinUrl: string | null;
      holidays: PlanSlotHoliday[]; weekId?: string; weekTitle?: string; learningOutcomes?: string[];
      /** The curriculum team's published hint for this holiday week, if there is one. */
      holidayNote?: string }
  /** Kept for a payload from an older, genuinely closing scheduler; today's spine never emits one. */
  | { kind: 'reading-week'; slotNumber: number; date: string; holidays: PlanSlotHoliday[] };

/**
 * The learner's curriculum timeline for one module, in curriculum slot order.
 *
 * The spine is the scheduler's `curriculumSlots` and nothing else, so the order
 * is the curriculum's own — Session 4, Reading Week, Session 5 — rather than a
 * list of session dates with the closure hidden somewhere else. A Reading Week
 * is never inferred from a gap between session dates: only the scheduler knows
 * a date was closed, and only it knows which holiday closed it.
 *
 * A taught slot is matched to the learner's REAL session (a Teams occurrence,
 * with its title, time and attendance) by delivery date, because the slot and
 * the occurrence describe the same teaching. When no occurrence has been created
 * yet the row still appears, dated and numbered from the plan — the schedule is
 * a fact about the module, not about whether a meeting has been booked.
 */
export function buildCurriculumTimeline(
  slots: PlanCurriculumSlot[] | undefined,
  sessions: PlanSession[],
): CurriculumRow[] {
  if (!slots?.length) return [];
  const byDate = new Map<string, PlanSession>();
  sessions.forEach(session => {
    const day = sessionDay(session.start);
    if (day && !byDate.has(day)) byDate.set(day, session);
  });
  return slots.map(slot => {
    if (slot.type === 'reading-week') {
      return { kind: 'reading-week' as const, slotNumber: slot.slotNumber, date: slot.date, holidays: slot.holidays || [] };
    }
    const session = byDate.get(slot.date);
    const number = Number(slot.sessionNumber) || 0;
    return {
      kind: 'session' as const,
      slotNumber: slot.slotNumber,
      date: slot.date,
      sessionNumber: number,
      title: session?.title || `Session ${number}`,
      start: session?.start || null,
      minutes: session?.minutes ?? null,
      attended: session?.attended ?? null,
      joinUrl: session?.joinUrl || null,
      holidays: slot.holidays || [],
      weekId: slot.weekId,
      weekTitle: slot.weekTitle,
      learningOutcomes: slot.learningOutcomes || [],
      // Carried through exactly as served: the week it belongs to is the week
      // this row is, so it can never surface against another one.
      holidayNote: slot.holidayNote,
    };
  });
}

/**
 * The curriculum timeline grouped by the month each slot falls in.
 *
 * Grouped by the slot's own date, which for a taught slot is the date it is
 * actually DELIVERED on — so a session a holiday pushed from May into June is
 * read under June, while the Reading Week that pushed it stays at its own
 * curriculum position in May. Groups are emitted in slot order rather than
 * sorted, so the curriculum's sequence survives a month boundary.
 */
export function groupCurriculumTimeline(rows: CurriculumRow[]) {
  const groups: Array<{ key: string; rows: CurriculumRow[] }> = [];
  rows.forEach(row => {
    const key = row.date.slice(0, 7);
    const current = groups[groups.length - 1];
    if (current && current.key === key) { current.rows.push(row); return; }
    groups.push({ key, rows: [row] });
  });
  return groups;
}

export function uniquePlanSessions(modules: TimelineModule[]) {
  return [...new Map(modules.flatMap(module => module.sessions).map(session => [session.id, session])).values()];
}

export function monthMetrics(month: string, modules: TimelineModule[], data: TrainingPlanDashboard) {
  const dates = modules.flatMap(module => module.dates).filter(date => date.startsWith(month));
  const weeks = new Set(dates.map(weekKey)).size;
  const monthlyLog = data.monthlyLogOtjh?.[month];
  const current = data.monthlyOtjh?.[month];
  // Monthly Logs is the authoritative SSOT per-month total when it exists.
  // The canonical actual projection is the only completed-hours fallback.
  const planned = monthlyLog?.target ?? data.months[month]?.planned ?? current?.planned ?? null;
  const canonicalRows = data.actual.filter(row => row.month === month);
  const canonicalActual = canonicalRows.reduce((sum, row) => sum + row.hours, 0);
  const actual = monthlyLog ? monthlyLog.completed
    : data.actualAvailable === false && !data.monthlyOtjh ? null
      : current?.includesHistorical ? current.actual
        : canonicalActual + (current?.actual ?? 0);
  const explicit = data.months[month]?.weeklyTarget;
  return { planned, actual, remaining: planned === null || actual === null ? null : Math.max(0, planned - actual), weeks,
    weekly: explicit ?? (planned !== null && weeks > 0 ? planned / weeks : null), progress: planned === null || actual === null ? null : percent(actual, planned) };
}

export function nextSession(sessions: PlanSession[], now: number) {
  return sessions.filter(session => !['cancelled', 'deleted', 'completed'].includes(session.status) && Date.parse(session.start) > now)
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start))[0] || null;
}

export function barPosition(start: string, end: string, year: number, startMonth = 0) {
  const from = Date.UTC(year, startMonth, 1), to = Date.UTC(year + 1, startMonth, 1);
  const first = Date.parse(start), last = Date.parse(end) + 86400000;
  if (!Number.isFinite(first) || !Number.isFinite(last) || first >= to || last <= from || last < first) return null;
  // Equal month columns: place dates within their own month's real day count.
  const position = (time: number) => {
    if (time <= from) return 0;
    if (time >= to) return 100;
    const date = new Date(time), month = date.getUTCMonth();
    const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const offset = (date.getUTCFullYear() - year) * 12 + month - startMonth;
    return (offset + (date.getUTCDate() - 1) / days) / 12 * 100;
  };
  return { left: position(first), width: position(last) - position(first) };
}

/** Position a date range against the same twelve-month period using weekly columns. */
export function periodPosition(start: string, end: string, year: number, startMonth = 0) {
  const from = Date.UTC(year, startMonth, 1), to = Date.UTC(year + 1, startMonth, 1);
  const first = Date.parse(start), last = Date.parse(end) + 86400000;
  if (!Number.isFinite(first) || !Number.isFinite(last) || first >= to || last <= from || last < first) return null;
  const position = (time: number) => Math.min(100, Math.max(0, (time - from) / (to - from) * 100));
  return { left: position(first), width: position(last) - position(first) };
}

/** Position a date range against weekly columns anchored to the first real programme week. */
export function weeklyPosition(start: string, end: string, anchor: string, weekCount: number) {
  const from = Date.parse(anchor), to = from + weekCount * 7 * 86400000;
  const first = Date.parse(start), last = Date.parse(end) + 86400000;
  if (!Number.isFinite(from) || !Number.isFinite(first) || !Number.isFinite(last) || first >= to || last <= from || last < first) return null;
  const position = (time: number) => Math.min(100, Math.max(0, (time - from) / (to - from) * 100));
  return { left: position(first), width: position(last) - position(first) };
}

export function timelineMonthKeys(year: number, startMonth = 0) {
  return Array.from({ length: 12 }, (_, index) => new Date(Date.UTC(year, startMonth + index, 1)).toISOString().slice(0, 7));
}

export function timelineWeekKeys(year: number, startMonth = 0, anchor?: string) {
  const from = anchor && Number.isFinite(Date.parse(anchor)) ? Date.parse(anchor) : Date.UTC(year, startMonth, 1);
  const to = anchor ? from + 52 * 7 * 86400000 : Date.UTC(year + 1, startMonth, 1);
  const count = anchor ? 52 : Math.ceil((to - from) / (7 * 86400000));
  return Array.from({ length: count }, (_, index) => new Date(from + index * 7 * 86400000).toISOString().slice(0, 10));
}

export function timelinePeriodYear(month: string, startMonth = 0) {
  return Number(month.slice(0, 4)) - (Number(month.slice(5, 7)) - 1 < startMonth ? 1 : 0);
}

/**
 * The curriculum team's published holiday hints for one module, by week id.
 *
 * `holidayNote` reaches a learner only on a slot whose week the author both
 * clashed with a holiday and published a note for — the server decides both,
 * so every entry here is already a hint this learner is meant to read. Keyed
 * by `weekId` so a screen that lists weeks rather than delivery dates (My
 * Learning, the week rail beside an activity) can show it against the one
 * week it was written for.
 */
export function weekHolidayNotes(slots?: PlanCurriculumSlot[] | null): Map<string, string> {
  const notes = new Map<string, string>();
  for (const slot of slots || []) {
    const note = (slot.holidayNote || '').trim();
    const weekId = (slot.weekId || '').trim();
    if (note && weekId && !notes.has(weekId)) notes.set(weekId, note);
  }
  return notes;
}
