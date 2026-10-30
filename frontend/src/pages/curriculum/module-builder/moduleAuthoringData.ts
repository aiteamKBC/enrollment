import { requestSessionSync } from '@/api/sessionResults';
import type { CurriculumKsbEntry, CurriculumModule, LibraryComponent } from '@/lib/curriculumApi';
import {
  CurriculumApiError,
  clearCurriculumGetCache,
  fetchCurriculumJson,
  invalidateCurriculumCacheByEntity,
} from '@/lib/curriculumApi';
import {
  assertComponentUploadAllowed,
  uploadComponentFile,
} from '@/pages/curriculum/shared/componentUploadPolicy';
import { hoursToRoundedMinutes, roundedMinutesToHours } from '@/lib/format';
import { relinkCopiedComponents, withoutRecordingLink } from '@/pages/curriculum/shared/entities/liveSessionRecordingLink';
import { reviewCalendar } from '../teams-meetings/calendarReview';
import { normalizedClock } from '../teams-meetings/calendarTime';
import type { OffScheduleRun } from '../teams-meetings/offScheduleRuns';
import {
  ADDITIONAL_TEAMS_MEETING_SETTING_KEYS,
  componentTypeGroups,
  componentTypes,
  getDefaultComponentSettings,
  getComponentDefinition,
  normaliseComponentSettings,
  type ComponentSettings,
  type ComponentSettingValue,
  type KsbMappingType,
  type ModuleComponentType,
  type ModuleStatus,
} from './componentAuthoringModel';

export { componentTypeGroups, componentTypes, getDefaultComponentSettings };
export type { ComponentSettings, KsbMappingType, ModuleComponentType, ModuleStatus };

export type KsbWeightClass = 'hard' | 'soft' | 'possible';

export interface KsbMapping {
  id: string;
  ksbId: string;
  code: string;
  description: string;
  sourceType?: string;
  sourceId?: string;
  type: KsbMappingType;
  classification?: KsbMappingType;
  weight: number;
  weightClass: KsbWeightClass;
  weight_class?: KsbWeightClass;
}

export interface KsbRemapReportEntry {
  location: 'module' | 'week' | 'component';
  path: string;
  code: string;
  description?: string;
  reason: string;
}

export interface KsbRemapReport {
  status: 'complete' | 'partial' | 'no-source';
  sourceProgrammeId?: string;
  sourceProgrammeName?: string;
  sourceType?: string;
  sourceId?: string;
  targetProgrammeId?: string;
  targetProgrammeName?: string;
  targetType?: string;
  targetId?: string;
  matchedCount: number;
  unmatched: KsbRemapReportEntry[];
  createdAt: string;
}

export interface DuplicateKsbSourceEntry {
  id?: string | number;
  code: string;
  description?: string;
  type?: string;
}

export interface DuplicateKsbSource {
  sourceType: string;
  sourceId: string;
  programmeId?: string;
  programmeName?: string;
  entries: DuplicateKsbSourceEntry[];
}

export interface CompletionCriteria {
  quizzesCompletedRequired: boolean;
  checkpointsCompletedRequired: boolean;
  averageScoreRequiredEnabled: boolean;
  averageScoreRequired: number;
  totalScoreRequiredEnabled: boolean;
  totalScoreRequired: number;
  additionalNotes: string;
}

export interface AdvancedModuleDetails {
  intent: string;
  learnerBenefit: string;
  employerBenefit: string;
  sequencePurpose: string;
}

export interface ModuleComponent {
  id: string;
  sourceId?: string;
  /**
   * The component this one was copied from, when it came out of the reuse
   * library. Provenance only - a copy is fully independent of its source.
   * Distinct from `sourceId`, which maps a delivery row back to its catalogue.
   */
  copiedFromId?: string;
  moduleId?: string;
  weekId: string;
  type: ModuleComponentType;
  title: string;
  description: string;
  expectedOtjh: number;
  points: number;
  reflectionRequired: boolean;
  /**
   * What the learner is asked to reflect on. Stored in its own column on
   * `curriculum.components` rather than in `settings`, and only editable while
   * `reflectionRequired` is on — the answer has nowhere to go otherwise.
   */
  reflectionQuestion: string;
  workplaceEvidenceRequired: boolean;
  tutorValidationRequired: boolean;
  /**
   * Whether a coach signs this component off. On for every component - the
   * builder makes you confirm before turning it off - so anything reading a
   * component from outside this model treats "not set" as on, never as off.
   */
  coachValidationRequired: boolean;
  ksbMappings: KsbMapping[];
  settings: ComponentSettings;
}

export interface ModuleWeek {
  id: string;
  /** The week this independent copy was placed from, when it has one. */
  copiedFromId?: string;
  moduleId: string;
  weekNumber: number;
  title: string;
  summary: string;
  learningOutcomes: string[];
  components: ModuleComponent[];
  ksbMappings: KsbMapping[];
  /**
   * The day this week actually runs, from the module's own dated session plan —
   * holiday shifts included. Served by the structure payload, never authored
   * here: the schedule lives on the module, and the weeks read it.
   */
  sessionDate?: string;
  sessionDay?: string;
  sessionStartTime?: string;
  sessionDurationMinutes?: number;
  /**
   * Whether this week's holiday hint is published to its learners.
   *
   * Separate from the text on purpose: turning the hint off must not throw away
   * what was written, and an unpublished note is nobody's to read. The learner
   * side additionally requires the week to still clash with a holiday, so a
   * note left behind on a week whose dates moved shows nothing.
   */
  holidayNoteEnabled?: boolean;
  /** The hint itself. Authored here; only the curriculum team can write it. */
  holidayNote?: string;
}

export interface ModuleMonthGroup {
  /** '2026-12', or '' for the weeks whose month is not known yet. */
  key: string;
  label: string;
  weeks: ModuleWeek[];
}

/**
 * The module's weeks, split into the months they run in.
 *
 * A module is authored as a run of weeks but delivered — and reported on — by
 * month, so a six-week module that crosses Christmas is really "one week in
 * December, five in January". Grouping is by each week's own session date, so
 * the split moves with the holiday shifts rather than assuming four weeks make a
 * month.
 *
 * Returns [] when no week has a date: there is no month to name, and a single
 * "Unscheduled" heading over the whole list tells a reader nothing.
 */
export function groupWeeksByMonth(weeks: ModuleWeek[]): ModuleMonthGroup[] {
  if (!weeks.some(week => monthKeyOf(week.sessionDate))) return [];
  const groups: ModuleMonthGroup[] = [];
  weeks.forEach(week => {
    const key = monthKeyOf(week.sessionDate);
    const current = groups[groups.length - 1];
    // Only ever extends the run in progress, so the weeks stay in module order:
    // a heading is a stretch of the timetable, not a bucket to file weeks into.
    // A week with no date of its own is the next week of the run before it.
    if (current && (!key || current.key === key)) {
      current.weeks.push(week);
      return;
    }
    groups.push({ key, label: monthLabelOf(key), weeks: [week] });
  });
  return groups;
}

function monthKeyOf(value?: string) {
  const text = String(value || '').trim();
  const match = /^(\d{4})-(\d{2})/.exec(text);
  return match ? `${match[1]}-${match[2]}` : '';
}

function monthLabelOf(key: string) {
  if (!key) return 'Not scheduled yet';
  const parsed = new Date(`${key}-01T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return key;
  return parsed.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
}

/**
 * The module's dated session plan, as the backend generates it.
 *
 * `skippedHolidays` on a session names the delivery days that were closed on the
 * way to it, so a date that moved can say why it moved.
 *
 * Each session carries two dates, because a closure moves only one thing.
 * `date` is when the LIVE SESSION runs — walked past every closed delivery day
 * on the way to it. `slotDate` is the delivery day it was due on counting from
 * the module's start with no closure at all, and that is where the WEEK sits:
 * a holiday closes the room, not the reading, the assignment or anything else
 * the week holds, so those stay on the week they were authored into. The two
 * agree until the first closure and differ by one delivery slot per closure
 * after it.
 */
export interface ModuleWeekSessionPlan {
  /**
   * `weekNumber` is the Monday-to-Sunday window this date falls in, counting
   * the module's own starting week as 1. It is what pairs a planned date with
   * an authored week: week N owns the sessions stamped N, however many that is.
   * Absent on payloads generated before weeks were calendar weeks.
   */
  sessions: Array<{ sessionNumber: number; weekNumber?: number; date: string; day: string; startTime?: string; endTime?: string; durationMinutes?: number; rescheduled?: boolean; slotDate?: string; slotDay?: string; skippedHolidays: string[] }>;
  /** The curriculum spine: every delivery slot, open or closed. See `ModuleSessionSlot`. */
  slots?: ModuleSessionSlot[];
  skippedHolidays: string[];
  finalEndDate: string;
  /** Where the run would have ended with nothing closed, stated by the planner. */
  originalEndDate?: string;
  warnings: string[];
}

/** One holiday, exactly as the curriculum already stores and serves it. */
export interface ModuleSlotHoliday {
  id?: string;
  label: string;
  startDate: string;
  endDate: string;
  type?: string;
  notes?: string;
  /** 'gov.uk' for a mirrored bank holiday, 'authored' for one entered here. */
  source?: string;
}

/**
 * One position in the module's curriculum, open or closed.
 *
 * The backend walks the module's own delivery pattern and emits a slot for
 * every delivery day it passes, so this is the curriculum as a reader sees it,
 * not the session list. A `live-session` slot names the session delivered in
 * it. A `reading-week` slot is a delivery day a holiday closed: it keeps its
 * place in the curriculum, it consumes NO session, and it carries the holidays
 * that closed it so the week can say why there is nothing to attend. Every
 * closure therefore makes the spine one slot longer than the session list,
 * which is what moves the delivery end date out by exactly one delivery slot
 * per closure -- in the module's own pattern, never a hardcoded seven days.
 */
export interface ModuleSessionSlot {
  slotNumber: number;
  date: string;
  day: string;
  type: 'live-session' | 'reading-week';
  cause?: string;
  sessionNumber: number | null;
  holidays: ModuleSlotHoliday[];
}

/**
 * How many sessions one authored week delivers.
 *
 * The two counts a module carries are stored apart -- weeks are weeks and
 * sessions are sessions -- so the delivery days a week runs on is the ratio
 * between them: a Mon+Fri module storing 10 weeks and 20 sessions runs two.
 * Never zero, so a module missing one of the counts simply reads as one session
 * a week rather than dividing its weeks away. See
 * backend/curriculum_api/tests_weeks_sessions_split.py, which pins the split.
 */
export function moduleDeliveryDaysPerWeek(module: ModuleCatalogueItem): number {
  const weeks = module.weekStructure.length || module.weeks || 0;
  if (!weeks) return 1;
  return Math.max(1, Math.round((module.sessionsNumber || weeks) / weeks));
}

/** Imported content may have one row per live session, plus reading-only rows. */
export function moduleUsesSessionRows(module: ModuleCatalogueItem, plannedSessionCount = module.sessionsNumber || 0): boolean {
  const counts = module.weekStructure.map(week => week.components.filter(component => component.type === 'live-session').length);
  const days = module.weeklySchedule?.length
    || String(module.deliveryMetadata?.weekDays || '').split(',').filter(day => day.trim()).length
    || moduleDeliveryDaysPerWeek(module);
  return plannedSessionCount > 0
    && counts.reduce((sum, count) => sum + count, 0) === plannedSessionCount
    && counts.every(count => count <= 1)
    && counts.length > Math.ceil(plannedSessionCount / days);
}

/**
 * How many planned session dates each authored week consumes, in week order.
 *
 * This is THE walk. **Authored week N is calendar week N.** Weeks run Monday to
 * Sunday from the module's own starting week, and a week owns the group's
 * delivery days that fall inside its own window -- so a Mon+Fri module gives
 * every full week two dates because the calendar holds two of them, and a
 * module that starts on a Wednesday gives its first week only the delivery days
 * from Wednesday on.
 *
 * That is why the plan is read rather than divided. `sessionsNumber / weeks`
 * can only ever give every week the same number, so it described a mid-week
 * start as a full week and slid the whole run by the days that week never had.
 * The backend stamps each planned session with the `weekNumber` it falls in
 * (`build_module_session_plan`) and this groups by it, which is exactly what
 * `apply_module_session_plan_to_weeks` does on the other side.
 *
 * A week over-authored with more live sessions than its window has delivery
 * days leaves the extras with no date. There is no date to give them -- the
 * group does not deliver again that week -- and inventing one would put a real
 * meeting on a day nobody chose.
 *
 * `plannedSessions` is the dated plan being walked. Callers that hold it pass
 * it; the rest fall back to the module's own delivery-days-per-week ratio,
 * which is the best a screen with no plan can say.
 *
 * Every screen that pairs weeks with dates reads this -- the Course structure
 * rail, the sessions drawer, the module workspace.
 */
export function moduleWeekSessionSlots(
  module: ModuleCatalogueItem | null | undefined,
  plannedSessions?: ModuleWeekSessionPlan['sessions'],
): number[] {
  if (!module) return [];
  const weeks = module.weekStructure;
  const plan = plannedSessions || [];
  // The plan, counted into the Mon-Sun windows the backend numbered it into.
  // A week with no delivery day in its own window gets nothing rather than
  // borrowing the next week's date.
  if (plan.some(session => Number(session.weekNumber) > 0)) {
    const countByWeekNumber = new Map<number, number>();
    plan.forEach(session => {
      const weekNumber = Number(session.weekNumber) || 0;
      if (weekNumber > 0) countByWeekNumber.set(weekNumber, (countByWeekNumber.get(weekNumber) || 0) + 1);
    });
    return weeks.map((_week, index) => countByWeekNumber.get(index + 1) || 0);
  }
  // No week numbers to read: an older payload, or a screen holding no plan at
  // all. The module's own delivery pattern is the only thing left to answer
  // with, so this stays exactly the walk it was before weeks became calendar
  // weeks -- including the imported-content shape, which carries one week per
  // live session plus content-only rows that deliver nothing.
  const perWeek = moduleDeliveryDaysPerWeek(module);
  const total = plan.length || module.sessionsNumber || weeks.length * perWeek;
  if (moduleUsesSessionRows(module, total)) {
    return weeks.map(week => week.components.filter(component => component.type === 'live-session').length);
  }
  let spare = Math.max(0, total - weeks.length * perWeek);
  return weeks.map(week => {
    const authored = (week.components || []).filter(component => component.type === 'live-session').length;
    const extra = Math.min(Math.max(0, authored - perWeek), spare);
    spare -= extra;
    return perWeek + extra;
  });
}

/**
 * How many live sessions one authored week has a planned date for.
 *
 * The group's delivery days are what a week can plan: a Monday group gives
 * each week one, a Mon+Fri group two. The Nth live session of the week takes
 * the Nth of them, and one beyond them runs on a date its author sets by hand
 * or on no date at all.
 *
 * Read from the fetched plan wherever it reaches, so a week the group pauses
 * correctly answers nothing. A week the plan has NOT been recomputed for yet —
 * one just added in the builder, or a template week appended a moment ago —
 * would otherwise read as zero slots and have every live session called
 * unplanned; the module's own delivery pattern is the honest answer there.
 */
export function weekDeliverySlotCapacity(
  module: ModuleCatalogueItem | null | undefined,
  weekIndex: number,
  plannedSessions?: ModuleWeekSessionPlan['sessions'],
): number {
  if (!module || weekIndex < 0) return 0;
  const plan = plannedSessions || [];
  const plannedWeeks = plan.reduce((furthest, session) => Math.max(furthest, Number(session.weekNumber) || 0), 0);
  if (plannedWeeks > 0 && weekIndex + 1 <= plannedWeeks && weekIndex < module.weekStructure.length) {
    return moduleWeekSessionSlots(module, plan)[weekIndex] ?? 0;
  }
  return moduleDeliveryDaysPerWeek(module);
}

/** A week asked to hold more live sessions than it has planned dates for. */
export interface LiveSessionSlotOverflow {
  weekNumber: number;
  /** Planned delivery dates this week owns. */
  capacity: number;
  /** Live sessions the week already holds. */
  existing: number;
  /** Live sessions about to be added to it. */
  adding: number;
  /** How many of them would have no planned date. */
  beyond: number;
}

/**
 * Whether adding live sessions to a week would outrun its delivery days.
 *
 * `null` when they all fit — the caller then adds them with nothing to say.
 * Otherwise the counts behind the warning, so the screen can name the gap
 * rather than refuse the edit: an unplanned live session is allowed, it simply
 * has no date until somebody gives it one, and the author is the one who
 * decides whether that is what they meant.
 */
export function liveSessionSlotOverflow(
  module: ModuleCatalogueItem | null | undefined,
  weekIndex: number,
  adding: number,
  plannedSessions?: ModuleWeekSessionPlan['sessions'],
): LiveSessionSlotOverflow | null {
  if (!module || adding <= 0) return null;
  const week = module.weekStructure[weekIndex];
  if (!week) return null;
  const capacity = weekDeliverySlotCapacity(module, weekIndex, plannedSessions);
  const existing = (week.components || []).filter(component => component.type === 'live-session').length;
  const beyond = Math.max(0, existing + adding - capacity);
  if (!beyond) return null;
  return { weekNumber: Number(week.weekNumber) || weekIndex + 1, capacity, existing, adding, beyond };
}

/**
 * What to tell the author before adding a live session the week cannot date.
 *
 * Says what the group delivers, what that leaves unplanned, and what happens
 * if they go ahead — in those words, not in slot counts. Nothing is refused:
 * the buttons decide, and the caller only adds on a yes.
 */
export function liveSessionSlotOverflowNotice(overflow: LiveSessionSlotOverflow): { title: string; text: string } {
  const { weekNumber, capacity, adding, beyond } = overflow;
  const delivers = capacity === 0
    ? 'This group delivers on no day of the week'
    : capacity === 1
      ? 'This group delivers on one day a week'
      : `This group delivers on ${capacity} days a week`;
  const held = capacity === 0
    ? `week ${weekNumber} has no planned date at all`
    : capacity === 1
      ? `week ${weekNumber} has one planned date, and its live session already has it`
      : `week ${weekNumber} has ${capacity} planned dates, and its live sessions already have them`;
  const subject = beyond === 1
    ? (adding === 1 ? 'The live session you are adding' : 'One of the live sessions you are adding')
    : `${beyond} of the live sessions you are adding`;
  const verb = beyond === 1 ? 'has' : 'have';
  return {
    title: beyond === 1 ? 'This live session will have no date' : `${beyond} of these live sessions will have no date`,
    text: `${delivers}, so ${held}. ${subject} ${verb} no date to run on. `
      + `${beyond === 1 ? 'It' : 'They'} will show as "No date yet" until you set one by hand, and `
      + `${beyond === 1 ? 'it is' : 'they are'} left out of the module's Teams calendar until then.`,
  };
}

/**
 * The dates each authored week runs on, in week order.
 *
 * A week owns a run of dates, not one date: `week.sessionDate` is only the first
 * of them. The rail shows the whole run so a Mon+Fri week reads as the two
 * delivery days it actually occupies.
 *
 * These are the week's delivered dates after holidays have pushed closed
 * delivery days forward. `slotDate` remains available on the raw session plan
 * for explaining which date was closed, but the structure rail reads the date
 * the learner actually sees.
 */
export function moduleWeekSessionDates(
  module: ModuleCatalogueItem | null | undefined,
  sessions: ModuleWeekSessionPlan['sessions'] | undefined,
): string[][] {
  return moduleWeekPlanDates(module, sessions, session => session.date);
}

/**
 * Where each authored week's live sessions actually run, in week order.
 *
 * The same delivered dates as `moduleWeekSessionDates`, used separately because
 * live-session components can each take one date inside a multi-session week.
 */
export function moduleWeekLiveSessionDates(
  module: ModuleCatalogueItem | null | undefined,
  sessions: ModuleWeekSessionPlan['sessions'] | undefined,
): string[][] {
  return moduleWeekPlanDates(module, sessions, session => session.date);
}

function moduleWeekPlanDates(
  module: ModuleCatalogueItem | null | undefined,
  sessions: ModuleWeekSessionPlan['sessions'] | undefined,
  dateOf: (session: ModuleWeekSessionPlan['sessions'][number]) => string | undefined,
): string[][] {
  const plan = sessions || [];
  if (!module || !plan.length) return [];
  const slotCounts = moduleWeekSessionSlots(module, plan);
  let sessionIndex = 0;
  return module.weekStructure.map((_week, weekIndex) => {
    const slotCount = slotCounts[weekIndex] ?? 1;
    const dates = plan.slice(sessionIndex, sessionIndex + slotCount).map(session => dateOf(session) || '').filter(Boolean);
    sessionIndex += slotCount;
    return dates;
  });
}

/**
 * Which authored week delivers each planned session number.
 *
 * The same walk `applyModuleWeekSessionPlan` and the backend's
 * `apply_module_session_plan_to_weeks` make: a week owns one planned date per
 * DELIVERY DAY, so a Mon+Fri week owns two session numbers. Shared rather than
 * re-derived per screen, because every off-by-one bug in this area has been a
 * second copy of this walk drifting from the first.
 */
export function moduleWeekIdBySessionNumber(
  module: ModuleCatalogueItem | null | undefined,
  sessions: ModuleWeekSessionPlan['sessions'] | undefined,
): Map<number, string> {
  const byNumber = new Map<number, string>();
  const plan = sessions || [];
  if (!module || !plan.length) return byNumber;
  const slotCounts = moduleWeekSessionSlots(module, plan);
  let cursor = 0;
  module.weekStructure.forEach((week, weekIndex) => {
    const slotCount = slotCounts[weekIndex] ?? 1;
    for (let offset = 0; offset < slotCount; offset += 1) {
      const session = plan[cursor];
      cursor += 1;
      if (session) byNumber.set(Number(session.sessionNumber) || cursor, week.id);
    }
  });
  return byNumber;
}

/** `String(value).trim()`, for the optional text an authored row carries. */
function trimmed(value: unknown): string {
  return String(value ?? '').trim();
}

/** The length a live session falls back to when nothing stored says otherwise. */
const FALLBACK_SESSION_MINUTES = 60;

/** Resolve the delivery slot without changing an already booked occurrence. */
function liveSessionClock(
  module: ModuleCatalogueItem,
  settings: ComponentSettings,
  date: string,
  week?: ModuleWeek,
  planned?: ModuleWeekSessionPlan['sessions'][number],
): { startTime: string; durationMinutes: number } {
  const booked = Boolean(settings.teamsLiveSessionId && Number(settings.teamsSessionNumber || 0) > 0);
  if (!booked && (!settings.sessionRescheduled || planned?.rescheduled)) {
    const day = new Date(`${date}T12:00:00Z`);
    const weekday = !Number.isNaN(day.getTime())
      ? new Intl.DateTimeFormat('en-US', { weekday: 'long', timeZone: 'UTC' }).format(day)
      : '';
    const weekly = module.weeklySchedule?.length ? module.weeklySchedule : module.sourceModule?.weeklySchedule;
    const slot = weekly?.find(item => item.day.toLowerCase() === weekday.toLowerCase());
    const candidates = [
      planned,
      slot,
      { startTime: module.startTime || module.deliveryMetadata?.startTime || module.sourceModule?.startTime,
        endTime: module.endTime || module.deliveryMetadata?.endTime || module.sourceModule?.endTime },
      // A week's header only describes its first delivery day.
      ...(week?.sessionDate === date ? [{ startTime: week.sessionStartTime, durationMinutes: week.sessionDurationMinutes }] : []),
    ];
    for (const candidate of candidates) {
      if (!candidate) continue;
      try {
        const startTime = normalizedClock(candidate.startTime);
        const endTime = 'endTime' in candidate && candidate.endTime ? normalizedClock(candidate.endTime) : '';
        const minutes = (clock: string) => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5));
        const durationMinutes = endTime ? minutes(endTime) - minutes(startTime)
          : ('durationMinutes' in candidate ? Number(candidate.durationMinutes) : 0);
        if (Number.isFinite(durationMinutes) && durationMinutes > 0) return { startTime, durationMinutes };
      } catch { /* An incomplete slot falls back to the next stored source. */ }
    }
  }
  return {
    startTime: trimmed(settings.sessionTime) || trimmed(week?.sessionStartTime),
    durationMinutes: Number(settings.durationMinutes || settings.teamsDurationMinutes || week?.sessionDurationMinutes || 0) || 0,
  };
}

/** `HH:MM` plus a number of minutes, wrapped inside the same day. */
function clockPlusMinutes(startTime: string, minutes: number): string {
  const match = trimmed(startTime).match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return '';
  const total = Number(match[1]) * 60 + Number(match[2]) + Math.max(0, Math.round(minutes));
  const wrapped = ((total % 1440) + 1440) % 1440;
  return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}`;
}

/** One dated live session, in the shape the Teams create form reads. */
export interface ModuleTeamsPlannedSession {
  timeZone?: string;
  componentId: string;
  date: string;
  startTime: string;
  endTime: string;
  skippedHolidays?: string[];
}

/**
 * This module's live sessions as dates a Teams calendar can be built on.
 *
 * Read off the structure the builder is already holding rather than out of the
 * whole curriculum's session collection. The backend stamps each live-session
 * component with its own `sessionDate`, `sessionTime` and `durationMinutes`
 * from `module_session_clock` when it serves the structure, and dates the weeks
 * from the same planner -- so this is that stored schedule, not a second one
 * worked out in the browser. Unbooked sessions use the delivery slot's clock
 * and duration; confirmed bookings retain their own.
 *
 * `plan` is the authority on WHICH DAY each session runs, because it is
 * recomputed from the group's delivery days while a component keeps whatever
 * day it was last stamped with. It also names the holidays landing on a date.
 * A component's own date answers only when there is no plan to read.
 *
 * An undated session is kept with an empty clock rather than dropped:
 * `calendarInputError` is what tells the reader a session has no time, and a
 * session that quietly vanished from the list would read as a shorter module.
 */
export function moduleTeamsPlannedSessions(
  module: ModuleCatalogueItem | null | undefined,
  plan?: ModuleWeekSessionPlan | null,
): ModuleTeamsPlannedSession[] {
  if (!module) return [];
  const liveDatesByWeek = moduleWeekLiveSessionDates(module, plan?.sessions);
  const closuresByDate = new Map<string, string[]>();
  (plan?.sessions || []).forEach(session => {
    const date = trimmed(session.date);
    if (date && session.skippedHolidays?.length) closuresByDate.set(date, session.skippedHolidays);
  });
  const sessions: ModuleTeamsPlannedSession[] = [];
  (module.weekStructure || []).forEach((week, weekIndex) => {
    const weekDates = liveDatesByWeek[weekIndex] || [];
    const live = (week.components || []).filter(component => component.type === 'live-session');
    // A week owns one planned date per delivery day, and its Nth live session
    // takes the Nth of them -- paired by position in the order the server
    // sends the week's components (`display_order, id`), which is the order
    // `live` is already in. The plan decides WHICH DAY, because a component
    // keeps the date it was last stamped with while the plan is recomputed
    // from the group -- so a group moved from Thursday to Wednesday leaves the
    // component holding its Thursday, and taking the day from the plan is what
    // stops this dialog offering Teams the old one while the Course structure
    // beside it reads the new.
    //
    // A live session beyond the week's delivery slots is an ADDITIONAL one: it
    // runs on its own `sessionDate` and nothing else. It must not borrow
    // another session's day -- that would put two meetings on one date and
    // move a session somebody had deliberately placed elsewhere.
    //
    // No re-sorting by stored date: position now decides which slot a session
    // resolves to, so sorting the components BY that date would be circular,
    // and the authored order is the deterministic one both ends already share.
    live.forEach((component, index) => {
      const settings = component.settings || {};
      // The component's own stamp answers for a planned session when the plan
      // could not be read at all -- the caller passes `null` on a failed load
      // -- which is the one case where that stamp is the best answer available.
      const date = weekDates[index] || trimmed(settings.sessionDate);
      const planned = plan?.sessions.find(session => session.date === date);
      const { startTime, durationMinutes } = liveSessionClock(module, settings, date, week, planned);
      sessions.push({
        componentId: component.id,
        timeZone: trimmed(settings.sessionTimeZone),
        date,
        startTime,
        endTime: clockPlusMinutes(startTime, durationMinutes || FALLBACK_SESSION_MINUTES),
        skippedHolidays: closuresByDate.get(date),
      });
    });
  });
  // The calendar is built in date order, and the rail's order is the author's,
  // not the timetable's -- a week dragged up the rail must not reorder the
  // meetings Teams is asked to create.
  return sessions.sort((left, right) => `${left.date}T${left.startTime}`.localeCompare(`${right.date}T${right.startTime}`));
}

/** One authored live session: the component that IS that session. */
export interface AuthoredLiveSession {
  componentId: string;
  weekId: string;
  /** The component's own name, which is what the session is called. */
  title: string;
  /** `YYYY-MM-DD`. Empty for a live session nobody has dated yet. */
  date: string;
  /** `HH:MM` in the business zone, as the component stores it. */
  startTime: string;
  durationMinutes: number;
}

/**
 * Every live session this module delivers, in Course structure order.
 *
 * **One live-session component is one session.** The module drawer says how
 * many WEEKS there are; the live sessions are whatever the author put inside
 * them. So this -- not `sessionsNumber` -- is how many sessions the module
 * runs, and each one's own `sessionDate` is when it runs.
 *
 * `date` is the component's OWN date and nothing else. A live session nobody
 * has dated comes back with an empty `date` and is a gap for the screen to
 * report -- never a date borrowed from its week or from the generated plan.
 * Both stand-ins read as a scheduled session to everyone downstream, so a
 * module that was never finished would quietly get a calendar entry and a Teams
 * meeting on a day no author ever chose.
 *
 * Dates mirror `authoring_session_links_by_catalogue` in curriculum_api/views.py.
 * Clocks follow the same delivery/booking rule as `module_live_session_clock`,
 * so the module, session list and Teams preview agree.
 */
export function moduleAuthoredLiveSessions(
  module: ModuleCatalogueItem | null | undefined,
): AuthoredLiveSession[] {
  if (!module) return [];
  const sessions: AuthoredLiveSession[] = [];
  (module.weekStructure || []).forEach(week => {
    (week.components || []).forEach(component => {
      if (component.type !== 'live-session') return;
      const settings = component.settings || {};
      const clock = liveSessionClock(module, settings, trimmed(settings.sessionDate), week);
      sessions.push({
        componentId: component.id,
        weekId: week.id,
        title: trimmed(component.title) || trimmed(week.title),
        date: trimmed(settings.sessionDate),
        // The week's slot time is the module's own stored value, not a guess,
        // so it may stand in for the time of day. The DATE never may.
        startTime: clock.startTime,
        durationMinutes: clock.durationMinutes,
      });
    });
  });
  return sessions;
}

/**
 * Replace the saved join link for live-session components in selected weeks.
 *
 * The module calendar identity, occurrence ids and attendance metadata remain
 * untouched. Additional one-off meetings also keep their own link because
 * that meeting is owned by the component rather than the module calendar.
 */
export function replaceLiveSessionLinksInWeeks(
  module: ModuleCatalogueItem,
  weekIds: Iterable<string>,
  link: string,
): ModuleCatalogueItem {
  const selectedWeekIds = new Set(Array.from(weekIds, value => String(value || '').trim()).filter(Boolean));
  const nextLink = String(link || '').trim();
  if (!selectedWeekIds.size || !nextLink) return module;

  return {
    ...module,
    weekStructure: module.weekStructure.map(week => {
      if (!selectedWeekIds.has(week.id)) return week;
      return {
        ...week,
        components: week.components.map(component => {
          if (
            component.type !== 'live-session'
            || String(component.settings.extraTeamsMeetingUrl || '').trim()
            || !String(component.settings.liveSessionUrl || component.settings.teamsMeetingUrl || '').trim()
          ) return component;
          return {
            ...component,
            settings: {
              ...component.settings,
              liveSessionUrl: nextLink,
              teamsMeetingUrl: nextLink,
              liveSessionLinkOverride: nextLink,
            },
          };
        }),
      };
    }),
  };
}

/**
 * Put a replaced join link onto exactly the components the server rewrote.
 *
 * By id rather than by re-running the week rule, so the workspace ends up
 * holding what was stored: a live session added in this tab and not saved yet
 * was not touched by the server, and is not touched here either.
 */
export function applyLiveSessionLinkToComponents(
  module: ModuleCatalogueItem,
  componentIds: Iterable<string>,
  link: string,
): ModuleCatalogueItem {
  const ids = new Set(Array.from(componentIds, value => String(value || '').trim()).filter(Boolean));
  const nextLink = String(link || '').trim();
  if (!ids.size || !nextLink) return module;
  return {
    ...module,
    weekStructure: module.weekStructure.map(week => (
      week.components.some(component => ids.has(component.id))
        ? {
          ...week,
          components: week.components.map(component => (
            ids.has(component.id)
              ? { ...component, settings: { ...component.settings, liveSessionUrl: nextLink, teamsMeetingUrl: nextLink, liveSessionLinkOverride: nextLink } }
              : component
          )),
        }
        : week
    )),
  };
}

export interface TeamsLinkReplacementResult {
  updated: number;
  updatedComponentIds: string[];
  link: string;
  structureRevision: string;
  /** True when nothing else had changed the module since `expectedRevision`. */
  revisionWasCurrent: boolean;
  /** Existing Teams evidence is read from this module when the pasted link matched it. */
  recordingsSourceModuleId?: string;
}

/**
 * Save a replaced join link for the chosen weeks, on its own.
 *
 * Not through `saveModuleStructure`: that sends and rewrites the whole module,
 * which on a large one outran the timeout for an edit touching a dozen rows.
 * Stored links only -- nothing is sent to Microsoft, so the Teams meetings and
 * their invitations stay as they are.
 */
export async function replaceModuleTeamsLinks(
  moduleCatalogueId: string,
  input: { weekIds: string[]; link: string; expectedRevision?: string },
) {
  const result = await apiJson<TeamsLinkReplacementResult>(`/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/teams-links/`, {
    method: 'POST',
    body: JSON.stringify({
      weekIds: input.weekIds,
      link: input.link,
      ...(input.expectedRevision ? { expectedRevision: input.expectedRevision } : {}),
    }),
    timeoutMs: 60000,
  });
  // The components the cached module views describe have just changed.
  if (result.updated) invalidateCurriculumCacheByEntity('module');
  return result;
}

/**
 * Which authored weeks have a delivery day a ticked holiday falls on.
 *
 * States the fact, decides nothing: the plan stays exactly as authored, every
 * week keeps its own date, and what a week becomes -- a reading week, a live
 * session that runs anyway, or something else -- is the author's call, made on
 * the week itself. This is only where the rail hangs the warning.
 *
 * Keyed by week id because one week can own more than one delivery day: a
 * Mon+Fri week with only its Monday on a holiday is still a live week that has
 * to say why one of its two days needs a look.
 *
 * Returns an empty map when the plan carries no spine, so a screen talking to
 * an older payload renders exactly as it did before.
 */
export function weeksTouchedByHoliday(
  module: ModuleCatalogueItem | null | undefined,
  plan: Pick<ModuleWeekSessionPlan, 'sessions' | 'slots'> | null | undefined,
): Map<string, ModuleSessionSlot[]> {
  const byWeekId = new Map<string, ModuleSessionSlot[]>();
  const slots = plan?.slots || [];
  if (!module || !slots.length) return byWeekId;
  const weekIdBySessionNumber = moduleWeekIdBySessionNumber(module, plan?.sessions);
  slots.forEach(slot => {
    if (!slot.holidays?.length) return;
    const weekId = weekIdBySessionNumber.get(Number(slot.sessionNumber));
    if (!weekId) return;
    byWeekId.set(weekId, [...(byWeekId.get(weekId) || []), slot]);
  });
  return byWeekId;
}

/** A live session held on a Teams-confirmed date its week no longer runs on. */
export interface LiveSessionDateDrift {
  componentId: string;
  weekId: string;
  /** The date Microsoft has the meeting on, and so the date this session keeps. */
  storedDate: string;
  /** The dates the week is planned to run on; `[0]` is the one its header shows. */
  weekDates: string[];
}

/**
 * Live sessions the planner deliberately left behind, and why.
 *
 * Every live session now follows its week: `applyModuleWeekSessionPlan` and
 * `apply_module_session_plan_to_weeks` re-date it from the plan, so a changed
 * start date or a reordered week moves the session with its week instead of
 * leaving it on the date it was first stamped with.
 *
 * One date is not the planner's to move -- an occurrence Microsoft has
 * confirmed (`teamsLiveSessionId` with a `teamsSessionNumber`). That date is
 * where a meeting real people were invited to actually sits, and it changes
 * through the Teams calendar's Edit session action, which asks Microsoft and
 * tells the people invited, or through a push from the Teams Meetings page. So when such a session's week moves, the two
 * genuinely disagree -- and the screens say so rather than quietly re-dating a
 * booking the calendar would then contradict.
 *
 * States the fact and decides nothing, like `weeksTouchedByHoliday`. Keyed by
 * component id, and empty with no plan loaded: nothing to compare against is an
 * unknown, not a disagreement.
 */
export function moduleLiveSessionDateDrift(
  module: ModuleCatalogueItem | null | undefined,
  plan: Pick<ModuleWeekSessionPlan, 'sessions'> | null | undefined,
): Map<string, LiveSessionDateDrift> {
  const drift = new Map<string, LiveSessionDateDrift>();
  const sessions = plan?.sessions || [];
  if (!module || !sessions.length) return drift;
  const datesByWeek = moduleWeekLiveSessionDates(module, sessions);
  (module.weekStructure || []).forEach((week, weekIndex) => {
    // The week's whole run, not just its first date: a Mon+Fri week delivers on
    // two days, and a session on the Friday is exactly where it belongs.
    const positionalDates = datesByWeek[weekIndex] || [];
    (week.components || []).forEach(component => {
      if (component.type !== 'live-session') return;
      const settings = component.settings || {};
      const storedDate = trimmed(settings.sessionDate);
      // A numbered session is compared with ITS planned date, as
      // `applyModuleWeekSessionPlan` dates it and the week header shows it. By
      // position alone, a module starting after its opening week's delivery day
      // gave week 1 no slot at all, and its booked session lost its warning.
      const explicitNumber = Number(settings.teamsSessionNumber || 0);
      const numbered = explicitNumber > 0
        ? sessions.find(session => session.sessionNumber === explicitNumber)
        : undefined;
      // An explicit occurrence move still needs synchronization even if its
      // former date happens to be another delivery slot in the same week.
      const weekDates = !numbered?.rescheduled && positionalDates.includes(storedDate)
        ? positionalDates : numbered?.date ? [numbered.date] : positionalDates;
      if (!weekDates.length) return;
      // An undated session is a different story with its own mark on the rail.
      if (!storedDate || weekDates.includes(storedDate)) return;
      // Anything else the planner has already moved onto its week's date; only
      // a confirmed booking can still be sitting here.
      if (!(trimmed(settings.teamsLiveSessionId) && explicitNumber > 0)) return;
      drift.set(component.id, { componentId: component.id, weekId: week.id, storedDate, weekDates });
    });
  });
  return drift;
}

/**
 * The module with every week carrying the day it now runs on.
 *
 * The plan is applied by *position*, because week N is session N: a seventh week
 * added to a six-week module takes the seventh delivery day from the start, and
 * the weeks before it keep the dates they already had.
 *
 * A week follows the delivered session date after holidays are skipped. The
 * raw plan still carries `slotDate` -- the date that was closed -- so the UI can
 * explain the shift, but the structure itself shows the replacement date the
 * learner actually receives.
 *
 * With `followEndDate`, the module's end date follows the plan only when it *was*
 * the plan: an end date that is one of the planned session dates was calculated
 * from a shorter run and moves out to the new last session, while a date set by
 * hand in the module form is left exactly where the person put it. Dating the
 * weeks of a module that was just opened passes it off -- nothing about the run
 * changed there, so nothing about its dates should read as an edit.
 *
 * Returns the module unchanged when nothing moved, so applying a plan the weeks
 * already agree with cannot mark a freshly-loaded module as edited.
 */
export function applyModuleWeekSessionPlan(
  module: ModuleCatalogueItem,
  plan: ModuleWeekSessionPlan,
  options: { followEndDate?: boolean } = {},
): ModuleCatalogueItem {
  const sessions = plan?.sessions || [];
  if (!sessions.length) return module;
  // How many planned dates one authored week owns. A Mon+Fri module runs two
  // sessions for every week it authors, so a ten-week module spends twenty
  // planned dates -- and it spends them whether or not the live-session
  // components that will sit on them have been authored yet. Walking the plan a
  // component at a time instead left an authored week holding a single date, so
  // ten weeks consumed ten of the twenty dates and the back half of the run
  // (the months past the halfway point) belonged to no week at all.
  const slotCounts = moduleWeekSessionSlots(module, sessions);
  let weeksMoved = false;
  let sessionIndex = 0;
  const weekStructure = module.weekStructure.map((week, weekIndex) => {
    const liveComponents = week.components.filter(component => component.type === 'live-session');
    const slotCount = slotCounts[weekIndex] ?? 1;
    const slots = sessions.slice(sessionIndex, sessionIndex + slotCount);
    sessionIndex += slotCount;
    const firstSession: ModuleWeekSessionPlan['sessions'][number] | undefined = slots[0];
    // The week runs on the delivered day. The raw plan still carries slotDate
    // for the "this date was closed" explanation, but the structure itself
    // follows the actual session date after holiday shifts.
    let sessionDate = firstSession ? (firstSession.date || '') : '';
    let sessionDay = firstSession ? (firstSession.day || '') : '';
    let components = week.components;
    if (liveComponents.length) {
      const plannedByComponentId = new Map<string, ModuleWeekSessionPlan['sessions'][number] | undefined>();
      // A week owns one planned slot per delivery day its group runs, and its
      // Nth live session takes the Nth of them -- matched by position in the
      // order the server sends the week's components (`display_order, id`),
      // which is the order this array is already in. So a Mon+Fri week moves
      // both of its planned sessions when the week moves, each onto its own
      // new day.
      //
      // A live session beyond those slots is an ADDITIONAL one: the group does
      // not deliver again that week, so `slots[offset]` is undefined, the walk
      // below returns it untouched, and it keeps the date its author gave it.
      //
      // Mirrors `apply_module_session_plan_to_weeks` in curriculum_api/views.py.
      liveComponents.forEach((component, offset) => {
        // A stored session number anchors this component to its existing plan
        // entry even when non-teaching weeks move around it. Match the backend's
        // fallback when that numbered entry is no longer in the plan.
        const explicitNumber = Number(component.settings?.teamsSessionNumber || 0);
        const numberedSession = explicitNumber > 0
          ? sessions.find(session => session.sessionNumber === explicitNumber)
          : undefined;
        plannedByComponentId.set(component.id, numberedSession || slots[offset]);
      });
      let componentsMoved = false;
      const plannedComponents = week.components.map(component => {
      if (component.type !== 'live-session') return component;
        const planned = plannedByComponentId.get(component.id);
        if (!planned?.date) return component;
        const settings = component.settings || {};
        // An additional meeting owns its confirmed schedule independently of
        // this week's main delivery slot, including after a week is inserted.
        if (trimmed(settings.extraTeamsMeetingUrl)) return component;
        const calendarConfirmationPending = ['missing', 'ambiguous'].includes(trimmed(settings.teamsCalendarBinding));
        // A live session runs on the day its week runs on. The date stored on
        // the component is a copy of the plan's, not a second opinion, so it
        // follows the plan whenever the plan moves -- which is what a changed
        // start date, a week dragged up the rail or a week inserted ahead of
        // this one all do. Holding the first-stamped date instead left the week
        // header and the session inside it naming different days, and the
        // session's was the one the calendar and Teams then used.
        //
        // The single exception is an occurrence Microsoft has confirmed: that
        // date is not a copy of anything, it is where a meeting real people
        // have been invited to actually sits, and it moves through
        // the calendar's Edit session action rather than behind the author's back.
        // `sessionDateTimeUtc` deliberately does NOT count -- the planner writes
        // it on every stamp, so treating it as a Teams booking froze every date
        // the planner had ever set.
        const bookedAtMicrosoft = Boolean(
          settings.teamsLiveSessionId && Number(settings.teamsSessionNumber || 0) > 0,
        );
        if (bookedAtMicrosoft) return component;
        const { startTime, durationMinutes } = liveSessionClock(module, settings, planned.date, week, planned);
        const instant = startTime ? zonedNaiveToUtcIso(`${planned.date}T${startTime}`, trimmed(settings.sessionTimeZone) || undefined) : '';
        // Compared as instants, not as strings. The backend writes this stamp
        // with Python's `+00:00` and this writes it with `.000Z`; the same
        // moment spelled two ways would otherwise read as a change on every
        // load, and mark a module nobody has touched as having unsaved work.
        const sameInstant = !instant
          || parseUtcInstant(settings.sessionDateTimeUtc).getTime() === parseUtcInstant(instant).getTime();
        if (
          trimmed(settings.sessionDate) === planned.date
          && trimmed(settings.sessionDay) === (planned.day || '')
          && trimmed(settings.sessionTime) === startTime
          && Number(settings.durationMinutes || 0) === durationMinutes
          && Number(settings.teamsDurationMinutes || durationMinutes) === durationMinutes
          && sameInstant
          && (!calendarConfirmationPending || !trimmed(settings.teamsStartDateTimeUtc))
        ) return component;
        componentsMoved = true;
        weeksMoved = true;
        return {
          ...component,
          settings: {
            ...settings,
            sessionDate: planned.date,
            sessionDay: planned.day || '',
            ...(startTime ? { sessionTime: startTime } : {}),
            ...(durationMinutes ? { durationMinutes, teamsDurationMinutes: durationMinutes } : {}),
            ...(planned.rescheduled ? { sessionRescheduled: true } : {}),
            // Re-derived, never left behind: the learner timeline and the
            // programme calendar read this instant, so a stale one would keep
            // pointing at the old day after the date above had moved.
            ...(instant ? { sessionDateTimeUtc: instant } : {}),
            // An imported date without an occurrence is a plan, not a Teams
            // confirmation. Keep that distinction when the week is moved.
            ...(calendarConfirmationPending
              ? { teamsStartDateTimeUtc: '' }
              : instant ? { teamsStartDateTimeUtc: instant } : {}),
          },
        };
      });
      if (componentsMoved) components = plannedComponents;
      const firstLive = components.find(component => component.type === 'live-session');
      const firstNumber = Number(firstLive?.settings?.teamsSessionNumber || 0);
      const firstNumberedSession = firstNumber > 0
        ? sessions.find(session => session.sessionNumber === firstNumber)
        : undefined;
      if (firstNumberedSession) {
        // Teams numbers can omit orientation and separately booked events.
        // A confirmed date already fitting this authored week's slots wins
        // over that unrelated index. A changed plan still moves the header
        // when the confirmed date no longer fits, preserving its drift notice.
        const confirmedDate = trimmed(firstLive?.settings?.teamsLiveSessionId)
          ? trimmed(firstLive?.settings?.sessionDate) : '';
        const headerSession = slots.find(slot => slot.date === confirmedDate) || firstNumberedSession;
        sessionDate = headerSession.date || '';
        sessionDay = headerSession.day || '';
      }
    }
    if (
      components === week.components
      && (week.sessionDate || '') === sessionDate
      && (week.sessionDay || '') === sessionDay
    ) return week;
    weeksMoved = true;
    return { ...week, components, sessionDate, sessionDay };
  });
  const currentEndDate = String(module.endDate || '').trim();
  const endDateFollowsPlan = options.followEndDate !== false
    && (!currentEndDate || sessions.some(session => session.date === currentEndDate));
  const endDate = endDateFollowsPlan && plan.finalEndDate ? plan.finalEndDate : module.endDate;
  if (!weeksMoved && endDate === module.endDate) return module;
  return { ...module, weekStructure, endDate };
}

/**
 * Every planned session's name, indexed by session number - 1, read from the
 * live-session components the module's weeks hold.
 *
 * A session's name is not a field of its own: it is the title of the live
 * session that runs on that date -- what the Components tab shows and what the
 * Teams series is built from. So the walk here is the one
 * `applyModuleWeekSessionPlan` above and the backend's
 * `apply_module_session_plan_to_weeks` both do: live components consume the flat
 * plan in week-then-display order, and a week with no live session still
 * consumes one date. Reproducing that is what keeps the name against a date the
 * name that date's session carries, rather than an off-by-one from every
 * content-only week above it.
 *
 * `null` marks a date a content-only week consumed, so a caller can say the week
 * holds no live session instead of leaving the row unexplained.
 */
export function liveSessionNamesByNumber(module: ModuleCatalogueItem | null | undefined): Array<string | null> {
  const names: Array<string | null> = [];
  const slotCounts = moduleWeekSessionSlots(module);
  (module?.weekStructure || []).forEach((week, weekIndex) => {
    const liveComponents = (week.components || []).filter(component => component.type === 'live-session');
    const slotCount = slotCounts[weekIndex] ?? 1;
    // One entry per date the week consumes, not per live session it holds. A
    // Mon+Fri week carrying a single live session still delivers on both days,
    // and the second date is a real gap in the authoring -- reported as `null`,
    // the same as a content-only week, so the drawer says the week holds no live
    // session for that date rather than dropping it off the end of the list.
    for (let offset = 0; offset < slotCount; offset += 1) {
      const component = liveComponents[offset];
      names.push(component ? String(component.title || '').trim() : null);
    }
  });
  return names;
}

/**
 * The same weeks, with the dates back in calendar order.
 *
 * Dragging a week to a new place moves what is taught, not when the module
 * meets: the timetable is the module's, so session 1 is still the first date
 * whichever week now sits there. Without this the dates travel with the week and
 * the rail reads as a module that runs backwards until the next save re-derives
 * them.
 */
export function resequenceWeekSessionDates(weeks: ModuleWeek[]): ModuleWeek[] {
  // Earliest first, undated weeks last: the plan generates dates in order, so
  // sorting them is enough to put session 1 back at the top of the rail. A week
  // with no date is one the plan has not reached, which is the end of the run.
  const dates = weeks
    .map(week => ({ sessionDate: week.sessionDate, sessionDay: week.sessionDay }))
    .sort((a, b) => (
      a.sessionDate && b.sessionDate
        ? a.sessionDate.localeCompare(b.sessionDate)
        : (a.sessionDate ? 0 : 1) - (b.sessionDate ? 0 : 1)
    ));
  return weeks.map((week, index) => (
    (week.sessionDate || '') === (dates[index].sessionDate || '')
      ? week
      : { ...week, ...dates[index] }
  ));
}

export interface ModuleCatalogueItem {
  learnerRosterMode?: 'inherited' | 'manual' | string;
  teamsSharedSourceModuleId?: string;
  ksbRemapReport?: KsbRemapReport | null;
  duplicateDestination?: boolean;
  startTime?: string;
  endTime?: string;
  weeklySchedule?: CurriculumModule['weeklySchedule'];
  sessionHolidays?: CurriculumModule['sessionHolidays'];
  deliveryWeeks?: number;
  id: string;
  catalogueId: string;
  programmeId: string;
  programmeName: string;
  programmeStatus?: 'active' | 'draft' | string;
  cohortId?: string;
  cohort?: string;
  groupId?: string;
  group?: string;
  isProgrammeDeleted?: boolean;
  title: string;
  description: string;
  color?: string;
  /**
   * Optional module artwork — a pasted image URL, or a data: URL read off the
   * picked file. The catalogue card shows it in place of the generic icon;
   * empty keeps the icon.
   */
  coverImage?: string;
  /** When the module row was written, and when it was last touched. */
  createdAt?: string;
  lastUpdated?: string;
  status: ModuleStatus;
  authoringStatus?: ModuleStatus;
  sourceType?: string;
  sourceId?: string;
  deliveryStatus?: string;
  deliveryMetadata?: Record<string, ComponentSettingValue>;
  ksbProfileSourceId?: string;
  tutor?: string;
  coach?: string;
  sessionsNumber?: number;
  startDate?: string;
  endDate?: string;
  weeks: number;
  totalOtjh: number;
  declaredTotalOtjh?: number;
  ksbCount: number;
  lessonCount: number;
  quizCount: number;
  qualityScore: number;
  /** Learners currently assigned this module, from the bulk overview count. */
  assignedLearnerCount?: number;
  moduleKsbMappings: KsbMapping[];
  completionCriteria: CompletionCriteria;
  advancedDetails: AdvancedModuleDetails;
  background: string;
  epaRequirements: string[];
  qualificationOutcomes: string[];
  weekStructure: ModuleWeek[];
  sourceModule?: CurriculumModule;
  /**
   * The stored structure this copy was built from, as the backend fingerprints it.
   *
   * A save on this endpoint replaces every week and component, so it is only
   * ever safe against the version the server holds. Sending this back as
   * `expectedRevision` is what lets the server refuse a payload built before
   * somebody else's write instead of performing it. Absent on a local draft
   * that has never been stored.
   */
  structureRevision?: string;
}

export interface KsbOption {
  id: string;
  code: string;
  description: string;
  type?: string;
  title?: string;
  sourceType?: string;
  sourceId?: string;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/curriculum_api';

export const emptyCompletionCriteria = (): CompletionCriteria => ({
  quizzesCompletedRequired: false,
  checkpointsCompletedRequired: false,
  averageScoreRequiredEnabled: false,
  averageScoreRequired: 70,
  totalScoreRequiredEnabled: false,
  totalScoreRequired: 100,
  additionalNotes: '',
});

export const emptyAdvancedDetails = (): AdvancedModuleDetails => ({
  intent: '',
  learnerBenefit: '',
  employerBenefit: '',
  sequencePurpose: '',
});

function authoringIdPrefix(prefix: string) {
  const text = String(prefix || '').toUpperCase();
  if (text.startsWith('MOD')) return 'MOD';
  if (text.startsWith('WEEK')) return 'WEEK';
  if (text.startsWith('COMP')) return 'COMP';
  if (text.startsWith('KSB')) return 'KSBMAP';
  return text.replace(/[^A-Z0-9]+/g, '') || 'ID';
}

export function makeAuthoringId(prefix: string) {
  const timestamp = new Date().toISOString().replace(/\D/g, '');
  const suffix = Math.random().toString(36).slice(2, 8).toUpperCase();
  return `${authoringIdPrefix(prefix)}-${timestamp}${suffix}`;
}

const makeId = makeAuthoringId;

function isCanonicalModuleCatalogueId(value: unknown) {
  return /^MOD-[A-Z0-9][A-Z0-9_-]*$/i.test(String(value || '').trim());
}

function canonicalModuleCatalogueId(module: CurriculumModule) {
  return [module.moduleCatalogueId, module.catalogueId, module.moduleId, module.structureId]
    .map(value => String(value || '').trim())
    .find(isCanonicalModuleCatalogueId) || '';
}

export function createEmptyComponent(weekId: string, type: ModuleComponentType, index: number): ModuleComponent {
  const definition = getComponentDefinition(type);
  return {
    id: makeId('component'),
    weekId,
    type,
    title: `${definition.label} ${index}`,
    description: '',
    expectedOtjh: definition.defaultOtjh,
    points: definition.defaultPoints,
    reflectionRequired: definition.reflectionDefault,
    reflectionQuestion: String(getDefaultComponentSettings(type).reflectionPrompt || ''),
    workplaceEvidenceRequired: definition.workplaceEvidenceDefault,
    tutorValidationRequired: definition.tutorValidationDefault,
    coachValidationRequired: true,
    ksbMappings: [],
    settings: getDefaultComponentSettings(type),
  };
}

export function createEmptyWeek(moduleId: string, weekNumber: number): ModuleWeek {
  return {
    id: makeId('week'),
    moduleId,
    weekNumber,
    title: `Week ${weekNumber}`,
    summary: '',
    learningOutcomes: [],
    components: [],
    ksbMappings: [],
  };
}

function normalisePlaceholderText(value: unknown) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

/**
 * A week's authored title, or '' when the title only repeats its number.
 *
 * `createEmptyWeek` seeds a week's title as "Week N", so an unedited module
 * carries titles that say exactly what the number beside them says. Printed as
 * `Week ${n} · ${title}` that came out as "Week 1 · Week 1". The number and the
 * title are worth showing together only when the title adds something.
 *
 * The placeholder test is the one `isGeneratedWeekPlaceholderComponent` uses, so
 * "Week 1", "week 1" and "WEEK  1" are all recognised as the generated default.
 */
export function weekAuthoredTitle(week: Pick<ModuleWeek, 'weekNumber' | 'title'>): string {
  const title = String(week.title || '').trim();
  if (!title) return '';
  return normalisePlaceholderText(title) === normalisePlaceholderText(`Week ${week.weekNumber}`) ? '' : title;
}

/**
 * What to print beside a week's number badge: the title the Module Builder
 * holds, exactly as its own rail shows it. Falls back to "Week N" for a week
 * with no title at all, so the row is never nameless.
 */
export function weekHeadingTitle(week: Pick<ModuleWeek, 'weekNumber' | 'title'>): string {
  return String(week.title || '').trim() || `Week ${week.weekNumber}`;
}

/**
 * A week named in running text, where there is no number badge to carry the
 * number — a KSB's placement, for instance. "Week 3", or "Week 3 - <title>"
 * when the title says more than the number does.
 */
export function weekPlacementLabel(week: Pick<ModuleWeek, 'weekNumber' | 'title'>, separator = ' - '): string {
  const title = weekAuthoredTitle(week);
  return title ? `Week ${week.weekNumber}${separator}${title}` : `Week ${week.weekNumber}`;
}

function isGeneratedWeekPlaceholderComponent(component: ModuleComponent, week: Pick<ModuleWeek, 'weekNumber' | 'title'>) {
  const titleKey = normalisePlaceholderText(component.title);
  const weekKeys = [week.title, `Week ${week.weekNumber}`].map(normalisePlaceholderText).filter(Boolean);
  const typeKey = normalisePlaceholderText(component.type);
  const hasKsbMappings = Boolean((component.ksbMappings || []).length);
  return !hasKsbMappings && weekKeys.includes(titleKey) && (typeKey.includes('live') || typeKey.includes('session'));
}

/** A week's OTJH is exactly the sum of Expected OTJH on its own components. */
export function weekExpectedOtjhTotal(week: Pick<ModuleWeek, 'components'>): number {
  const totalMinutes = (week.components || []).reduce((total, component) => (
    total + hoursToRoundedMinutes(Number(component.expectedOtjh || 0))
  ), 0);
  return roundedMinutesToHours(totalMinutes);
}

export function createLocalModuleDraft(input: { programme: string; title: string; description: string; weeks: number; status: ModuleStatus; catalogueId?: string; programmeId?: string; programmeStatus?: string; cohortId?: string; cohortName?: string; groupId?: string; groupName?: string; ksbProfileSourceId?: string; sessionsNumber?: number; startDate?: string; endDate?: string; coverImage?: string; learnerRosterMode?: 'inherited' | 'manual'; teamsSharedSourceModuleId?: string }): ModuleCatalogueItem {
  const catalogueId = input.catalogueId || makeAuthoringId('MOD');
  const id = `local-${catalogueId}`;
  const weekCount = Math.max(0, Math.round(Number(input.weeks) || 0));
  const weekStructure = Array.from({ length: weekCount }, (_, index) => createEmptyWeek(id, index + 1));
  return recalculateModule({
    id,
    catalogueId,
    // An unassigned draft carries an EMPTY programmeId, never a placeholder
    // string. 'programme-local' used to be sent here and the backend accepted
    // it as a real identifier, creating junk programme rows. A programme NAME
    // is not an id either, so it is no longer used as a fallback.
    programmeId: input.programmeId || '',
    programmeName: input.programme || 'Unassigned programme',
    programmeStatus: input.programmeStatus || '',
    cohortId: input.cohortId || '',
    cohort: input.cohortName || '',
    groupId: input.groupId || '',
    group: input.groupName || '',
    learnerRosterMode: input.learnerRosterMode || 'inherited',
    teamsSharedSourceModuleId: input.teamsSharedSourceModuleId || '',
    title: input.title,
    description: input.description,
    coverImage: input.coverImage || '',
    status: input.status || 'draft',
    ksbProfileSourceId: input.ksbProfileSourceId || '',
    sessionsNumber: Math.max(0, Math.round(Number(input.sessionsNumber ?? input.weeks) || 0)),
    startDate: input.startDate || '',
    endDate: input.endDate || '',
    weeks: weekStructure.length,
    totalOtjh: 0,
    ksbCount: 0,
    lessonCount: 0,
    quizCount: 0,
    qualityScore: 0,
    moduleKsbMappings: [],
    completionCriteria: emptyCompletionCriteria(),
    advancedDetails: emptyAdvancedDetails(),
    background: '',
    epaRequirements: [],
    qualificationOutcomes: [],
    weekStructure,
    deliveryMetadata: {
      cohortId: input.cohortId || '',
      cohort: input.cohortName || '',
      groupId: input.groupId || '',
      group: input.groupName || '',
    },
  });
}

export async function createNewModule(input: { programme: string; title: string; description: string; weeks: number; status: ModuleStatus; programmeId?: string; programmeStatus?: string; cohortId?: string; cohortName?: string; groupId?: string; groupName?: string; ksbProfileSourceId?: string; sessionsNumber?: number; startDate?: string; endDate?: string; coverImage?: string; learnerRosterMode?: 'inherited' | 'manual'; teamsSharedSourceModuleId?: string; ksbRemapReport?: KsbRemapReport | null; moduleKsbMappings?: KsbMapping[]; weekStructure?: ModuleWeek[]; duplicateDestination?: boolean }) {
  const draft = createLocalModuleDraft(input);
  try {
    const response = await apiJson<{ created: boolean; moduleCatalogueId?: string; module?: ModuleCatalogueItem }>('/curriculum/modules/', {
      method: 'POST',
      body: JSON.stringify({
        title: draft.title,
        moduleType: 'authoring',
        sourceType: 'authoring',
        description: draft.description,
        coverImage: draft.coverImage || '',
        programmeId: draft.programmeId,
        programmeStatus: draft.programmeStatus,
        programme: draft.programmeName,
        programmeName: draft.programmeName,
        cohortId: input.cohortId || '',
        cohortName: input.cohortName || '',
        groupId: input.groupId || '',
        groupName: input.groupName || '',
        learnerRosterMode: input.learnerRosterMode || 'inherited',
        teamsSharedSourceModuleId: input.teamsSharedSourceModuleId || '',
        ksbRemapReport: input.ksbRemapReport || null,
        duplicateDestination: Boolean(input.duplicateDestination),
        status: draft.status || 'draft',
        sessionsNumber: draft.sessionsNumber ?? draft.weeks,
        // The authored week count, sent apart from the calendar session count.
        weeksNumber: draft.weeks,
        startDate: draft.startDate || '',
        endDate: draft.endDate || '',
        weekStructure: input.weekStructure || draft.weekStructure,
        completionCriteria: draft.completionCriteria,
        advancedDetails: draft.advancedDetails,
        moduleKsbMappings: input.moduleKsbMappings || draft.moduleKsbMappings,
        ksbProfileSourceId: draft.ksbProfileSourceId || '',
        background: draft.background,
        epaRequirements: draft.epaRequirements,
        qualificationOutcomes: draft.qualificationOutcomes,
      }),
    });
    return recalculateModule(response.module || { ...draft, catalogueId: response.moduleCatalogueId || draft.catalogueId });
  } catch (err) {
    throw err;
  }
}

export interface DuplicateModuleStructureOptions {
  /**
   * Cloning a cohort or group keeps the source's dates and delivery day on the
   * copy — there is no "own start date" to redate against, since the new
   * group/cohort is meant to run in parallel with the one it was cloned from.
   * The module-builder's own "Duplicate module" button leaves this false: a
   * standalone copy has nothing scheduling it yet, so it is dated from its own
   * module's start date instead (see the note this used to always carry).
   */
  keepDates?: boolean;
  /** Append " copy" to the title. Off when the group/cohort it lands in already carries that suffix. */
  renameCopy?: boolean;
  /** Attach the copy to a different group/cohort than the source (a cohort/group clone target) instead of the source's own. */
  cohortId?: string;
  cohortName?: string;
  groupId?: string;
  groupName?: string;
  programmeId?: string;
  programmeName?: string;
  learnerRosterMode?: 'inherited' | 'manual';
  teamsCopyMode?: 'shared' | 'independent';
  /** Independent copies use this date to regenerate their calendar plan. */
  startDate?: string;
  /** The destination Programme's authoritative KSB source, when it changed. */
  ksbSource?: DuplicateKsbSource;
  ksbRemapReport?: KsbRemapReport | null;
  deliveryMetadata?: Record<string, ComponentSettingValue>;
}

function normaliseDuplicateKsbCode(value: unknown) {
  return String(value || '').trim().toUpperCase().replace(/\s+/g, '');
}

function remapMapping(mapping: KsbMapping, source: DuplicateKsbSource | undefined, location: KsbRemapReportEntry['location'], path: string) {
  const code = normaliseDuplicateKsbCode(mapping.code);
  const target = source?.entries.find(entry => normaliseDuplicateKsbCode(entry.code) === code);
  if (!source || !source.sourceType || !source.sourceId) {
    return { mapping: null, unmatched: { location, path, code: mapping.code || code, description: mapping.description, reason: 'The destination Programme has no valid KSB source.' } };
  }
  if (!target) {
    return { mapping: null, unmatched: { location, path, code: mapping.code || code, description: mapping.description, reason: 'The KSB code does not exist in the destination source.' } };
  }
  return {
    mapping: {
      ...mapping,
      id: makeAuthoringId('ksb'),
      ksbId: String(target.id ?? mapping.ksbId ?? ''),
      code: target.code,
      description: target.description || mapping.description,
      sourceType: source.sourceType,
      sourceId: source.sourceId,
    },
    unmatched: null,
  };
}

/**
 * Repoint every module/week/component mapping to a destination Programme's
 * source. Unmatched mappings are intentionally omitted from the saved payload
 * so backend source validation cannot reject the otherwise valid duplicate;
 * their exact location is retained in the report for review.
 */
export function remapModuleKsbMappings(
  module: ModuleCatalogueItem,
  source: DuplicateKsbSource | undefined,
  reportBase: Omit<KsbRemapReport, 'matchedCount' | 'unmatched' | 'createdAt' | 'status'>,
) {
  let matchedCount = 0;
  const unmatched: KsbRemapReportEntry[] = [];
  const mapList = (mappings: KsbMapping[], location: KsbRemapReportEntry['location'], path: string) => mappings.flatMap((mapping, index) => {
    const result = remapMapping(mapping, source, location, `${path}[${index}]`);
    if (result.mapping) {
      matchedCount += 1;
      return [result.mapping];
    }
    if (result.unmatched) unmatched.push(result.unmatched);
    return [];
  });
  const next = recalculateModule({
    ...module,
    moduleKsbMappings: mapList(module.moduleKsbMappings || [], 'module', 'moduleKsbMappings'),
    weekStructure: module.weekStructure.map((week, weekIndex) => ({
      ...week,
      ksbMappings: mapList(week.ksbMappings || [], 'week', `weekStructure[${weekIndex}].ksbMappings`),
      components: week.components.map((component, componentIndex) => ({
        ...component,
        ksbMappings: mapList(component.ksbMappings || [], 'component', `weekStructure[${weekIndex}].components[${componentIndex}].ksbMappings`),
      })),
    })),
    ksbProfileSourceId: source?.sourceId || '',
  });
  const report: KsbRemapReport = {
    ...reportBase,
    status: !source?.sourceId ? 'no-source' : unmatched.length ? 'partial' : 'complete',
    matchedCount,
    unmatched,
    createdAt: new Date().toISOString(),
  };
  return { module: { ...next, ksbRemapReport: report }, report };
}

export async function duplicateModuleStructure(
  source: ModuleCatalogueItem,
  options: DuplicateModuleStructureOptions = {},
) {
  const { keepDates = false, renameCopy = true } = options;
  const learnerRosterMode = options.learnerRosterMode || (keepDates ? 'inherited' : 'manual');
  const teamsCopyMode = options.teamsCopyMode || 'independent';
  const copyId = `copy-${Date.now().toString(36)}`;
  const cloneMappings = (mappings: KsbMapping[] = [], scope: string) => mappings.map((mapping, index) => ({
    ...mapping,
    id: makeId(`ksb-${scope}-${index + 1}`),
  }));
  const duplicate = recalculateModule({
    ...source,
    id: copyId,
    catalogueId: makeAuthoringId('MOD'),
    title: renameCopy ? `${source.title} copy` : source.title,
    status: 'draft',
    sourceModule: undefined,
    // The copy is an authored module in its own right, never a second claim on
    // whatever the original was made from. `source_id` is how a training-plan
    // row finds its module (`matching_authoring_module_for_training_row`), so a
    // copy carrying the original's made two modules answer to one row and which
    // one answered was whichever the candidate scan reached first. `sourceId` is
    // re-pointed at the copy's OWN catalogue id below, once the row exists --
    // which is how an authored module names itself.
    sourceType: 'module_authoring',
    // The fingerprint of the STORED structure this object was read from -- the
    // original's. The copy has not been stored yet, and carrying a revision that
    // belongs to another module's row is only ever wrong.
    structureRevision: undefined,
    // Read back off the original's own weeks by the structure endpoint, so it
    // arrives holding the original's `teamsLiveSessionId` even though every
    // component here is about to be stripped of it. The backend does not treat
    // this as description: `link_live_session_series_to_module` takes every
    // live-session id the payload mentions -- components AND this object -- and
    // points those `curriculum.live_sessions` rows at the module being saved. So
    // saving the copy MOVED the original's real meeting onto the copy: the
    // original kept the ids in its components but no longer owned the row, and
    // everything that reads the table by module (the Teams Meetings page,
    // attendance, recordings, the sync) followed the meeting to the copy.
    // Stripped the same way the components are, and for the same reason.
    deliveryMetadata: (options.deliveryMetadata || source.deliveryMetadata)
      ? independentCopySettings(structuredClone(options.deliveryMetadata || source.deliveryMetadata), { keepDates })
      : source.deliveryMetadata,
    // The run the source is on is the source's, not the copy's. `createNewModule`
    // below already withholds these, but the structure save right after it sends
    // the whole module -- so leaving them here put the original's start date back
    // on the copy's row, and every date the copy has is generated from that row:
    // `module_session_plan_for_count` walks forward from `start_date`, so the
    // copy's weeks and live sessions were re-dated onto the original's days the
    // moment they were planned. Assigning the copy to another group did not save
    // it either -- that group supplies its own delivery days, not a start date,
    // so the sessions simply landed on the old run's dates spelled in new days.
    // Undated is the honest state: the module drawer asks for a start date before
    // it will save, and the plan fills the weeks from the answer.
    startDate: keepDates ? source.startDate : options.startDate || '',
    endDate: keepDates ? source.endDate : '',
    cohortId: options.cohortId ?? source.cohortId,
    cohort: options.cohortName ?? source.cohort,
    groupId: options.groupId ?? source.groupId,
    group: options.groupName ?? source.group,
    programmeId: options.programmeId ?? source.programmeId,
    programmeName: options.programmeName ?? source.programmeName,
    learnerRosterMode,
    teamsSharedSourceModuleId: teamsCopyMode === 'shared'
      ? (source.catalogueId || source.id)
      : '',
    ksbRemapReport: options.ksbRemapReport || null,
    duplicateDestination: true,
    moduleKsbMappings: cloneMappings(source.moduleKsbMappings, 'module'),
    completionCriteria: { ...source.completionCriteria },
    advancedDetails: { ...source.advancedDetails },
    epaRequirements: [...(source.epaRequirements || [])],
    qualificationOutcomes: [...(source.qualificationOutcomes || [])],
    weekStructure: source.weekStructure.map((week, weekIndex) => {
      const weekId = makeId(`week-copy-${weekIndex + 1}`);
      return {
        ...week,
        id: weekId,
        moduleId: copyId,
        // The copy is dated by its own module's start date, not the source's,
        // unless `keepDates` says this copy IS meant to run on the source's own
        // dates (a cohort/group clone). `applyModuleWeekSessionPlan` fills these
        // from the plan when left blank; carrying them over otherwise left the
        // new module's rail showing the dates the original runs on.
        sessionDate: keepDates ? week.sessionDate : '',
        sessionDay: keepDates ? week.sessionDay : '',
        learningOutcomes: [...(week.learningOutcomes || [])],
        // The text travels with the week it was written on; whether it is shown
        // does not. A copy runs on its own dates, so whether it clashes at all
        // is a fresh question -- unless this copy IS the source's dates.
        holidayNoteEnabled: keepDates ? week.holidayNoteEnabled : false,
        ksbMappings: cloneMappings(week.ksbMappings, `week-${weekIndex + 1}`),
        components: relinkCopiedComponents(week.components, week.components.map((component, componentIndex) => ({
          ...component,
          id: makeId(`component-copy-${weekIndex + 1}-${componentIndex + 1}`),
          weekId,
          ksbMappings: cloneMappings(component.ksbMappings, `component-${weekIndex + 1}-${componentIndex + 1}`),
          // Cloned before it is stripped, like `duplicateWeekInModule` does:
          // `independentCopySettings` spreads one level, so the copy would go on
          // sharing the source's ARRAYS -- its attendees, presenters and KSB
          // lists. The source here is the object `loadModuleStructure` cached,
          // so an edit through the copy would reach both the original on screen
          // and everything else reading that cache entry.
          settings: independentCopySettings(structuredClone(component.settings || {}), { keepDates }),
        }))),
      };
    }),
  });

  let preparedDuplicate = duplicate;
  if (options.ksbSource) {
    preparedDuplicate = remapModuleKsbMappings(preparedDuplicate, options.ksbSource, options.ksbRemapReport || {
      sourceProgrammeId: source.programmeId,
      sourceProgrammeName: source.programmeName,
      sourceType: source.ksbProfileSourceId ? 'framework' : '',
      sourceId: source.ksbProfileSourceId || '',
      targetProgrammeId: preparedDuplicate.programmeId,
      targetProgrammeName: preparedDuplicate.programmeName,
      targetType: options.ksbSource.sourceType,
      targetId: options.ksbSource.sourceId,
    }).module;
  }

  try {
    const created = await createNewModule({
      programme: preparedDuplicate.programmeName,
      title: preparedDuplicate.title,
      description: preparedDuplicate.description,
      weeks: Math.max(1, preparedDuplicate.weekStructure.length),
      status: 'draft',
      programmeId: preparedDuplicate.programmeId,
      ksbProfileSourceId: preparedDuplicate.ksbProfileSourceId,
      cohortId: preparedDuplicate.cohortId,
      cohortName: preparedDuplicate.cohort,
      groupId: preparedDuplicate.groupId,
      groupName: preparedDuplicate.group,
      startDate: preparedDuplicate.startDate || undefined,
      endDate: keepDates ? preparedDuplicate.endDate : undefined,
      learnerRosterMode,
      teamsSharedSourceModuleId: teamsCopyMode === 'shared'
        ? (source.catalogueId || source.id)
        : '',
      ksbRemapReport: preparedDuplicate.ksbRemapReport,
      moduleKsbMappings: preparedDuplicate.moduleKsbMappings,
      weekStructure: preparedDuplicate.weekStructure,
      duplicateDestination: true,
    });
    let payload = recalculateModule({
      ...preparedDuplicate,
      catalogueId: created.catalogueId,
      id: created.id || duplicate.id,
      // An authored module's `source_id` is its own catalogue id -- that is what
      // the estate holds for the modules the builder made. The copy names
      // itself here rather than the module it was copied from.
      sourceId: created.catalogueId,
      learnerRosterMode,
      teamsSharedSourceModuleId: teamsCopyMode === 'shared'
        ? (source.catalogueId || source.id)
        : '',
      ksbRemapReport: preparedDuplicate.ksbRemapReport || null,
      duplicateDestination: true,
    });
    const saved = await saveModuleStructure(payload.catalogueId, payload);
    return saved;
  } catch (err) {
    throw err;
  }
}

// Keys that describe a component's relationship to *its own* week/module — a
// copy lands in a different one, so carrying these forward is always wrong:
// stale group ids from the source's context, and (worse) placedCopy* pointers
// that would make the clone's "Assigned groups" cascade-delete reach into
// whatever module/week the *source* had placed a copy in, not its own. The
// clone starts with a clean slate; the target's own AssignedGroupsSection
// re-derives its locked group on first render.
const GROUP_ASSIGNMENT_SETTING_KEYS = [
  'selectedGroupKeys',
  'selectedGroupNames',
  'placedCopyGroupKeys',
  'placedCopyModuleCatalogueIds',
  'placedCopyWeekIds',
  'placedCopyComponentIds',
] as const;

function withoutGroupAssignmentSettings(settings: ComponentSettings): ComponentSettings {
  const next = { ...settings };
  GROUP_ASSIGNMENT_SETTING_KEYS.forEach(key => { delete next[key]; });
  return next;
}

// What a live-session component holds about ONE delivery of itself: the date it
// runs on, and the Microsoft records that date produced. A duplicate is a
// different module — its own weeks, its own components, its own place in the
// calendar — so none of this belongs to it.
//
// Carrying them forward is what let a copy open on the original's dates: the
// plan only ever fills a component with no date and no tracked occurrence
// (`applyModuleWeekSessionPlan`), so a copy holding the source's `sessionDate`
// could never be re-dated from its own module's start date, and the Teams
// create-form — which reads the components' stored dates — offered to book the
// original's calendar under the copy's name.
//
// The Microsoft ids are the more serious half. `teamsLiveSessionId` is a row id
// in `curriculum.live_sessions`; two modules sharing it share one meeting, its
// join link, its occurrences, its attendance and its recordings, so editing the
// copy reaches into the original's calendar. Duplicating is not the feature
// that shares a meeting across cohorts — that is "Assigned groups"
// (`placedCopy*` above), which places a copy deliberately and is stripped here
// for the same reason.
export const SESSION_DATE_SETTING_KEYS = [
  'sessionDate',
  'sessionDay',
  'sessionDateTimeUtc',
  // The instant Microsoft holds the booked occurrence at. It is the same date
  // said a second way, and the week builder falls back to it when reading a
  // session's calendar instant, so a copy that kept it still pointed at the
  // original's day after `sessionDate` had been cleared.
  'teamsStartDateTimeUtc',
] as const;

export const TEAMS_MEETING_SETTING_KEYS = [
  'teamsLiveSessionId',
  'teamsSessionNumber',
  'teamsEventId',
  'teamsOnlineMeetingId',
  'teamsCalendarSeries',
  'teamsMeetingOptionsUrl',
  'teamsMeetingUrl',
  'liveSessionUrl',
  'teamsProvider',
  // The rest of what the Teams attachment path stamps per occurrence
  // (`LIVE_SESSION_TRACKING_SETTING_KEYS` in componentAuthoringModel.ts). Every
  // one of them names the ORIGINAL's meeting: `teamsOccurrenceId` is its Graph
  // instance, `teamsWebLink` opens its calendar entry, `teamsDurationMinutes`
  // is the length Microsoft booked it for, and `sessionRescheduled` records
  // that its occurrence was moved. `durationMinutes` -- what the author chose
  // -- is a different key and survives the copy.
  'teamsOccurrenceId',
  'teamsWebLink',
  'teamsDurationMinutes',
  'sessionRescheduled',
  // A bulk link replacement belongs to the source module's saved calendar;
  // an independent copy keeps its join URL only through the explicit copy
  // path below and must not inherit the override marker.
  'liveSessionLinkOverride',
  'teamsCalendarBinding',
  'teamsMainLinkInherited',
  // The one-off week meeting is the original's too: its organiser invited its
  // own guests to one date, and a copy placed in another week must arrive with
  // no link at all rather than pointing people at somebody else's meeting.
  ...ADDITIONAL_TEAMS_MEETING_SETTING_KEYS,
] as const;

/**
 * A copied component's settings, with everything that belonged to the original's
 * own delivery removed.
 *
 * What the author wrote survives — the session's purpose and preparation, its
 * clock and duration, the organizer mailbox and the meeting options — because
 * that is authoring, and a copy that dropped it would only have to be typed
 * again. What does not survive is the meeting it runs in — the Microsoft ids
 * are a row in `curriculum.live_sessions`; a copy that kept `teamsLiveSessionId`
 * would not be a second meeting, it would be the SAME meeting claimed twice.
 *
 * The date it runs on drops too, by default — `keepDates: true` is for a
 * cohort/group clone, whose copy is meant to run in parallel with the source
 * on the same delivery day, not be redated from its own module's start date.
 */
export function independentCopySettings(
  settings: ComponentSettings,
  options: { keepDates?: boolean } = {},
): ComponentSettings {
  const next = withoutGroupAssignmentSettings(settings);
  TEAMS_MEETING_SETTING_KEYS.forEach(key => { delete next[key]; });
  if (!options.keepDates) SESSION_DATE_SETTING_KEYS.forEach(key => { delete next[key]; });
  return next;
}

/**
 * Copy a component out of the reuse library into a week, as a snapshot.
 *
 * Every id is regenerated so the copy is fully independent: editing or deleting
 * it never touches the source, and vice versa. The one thing deliberately
 * *shared* is `settings.linkedQuizId` - quizzes are standalone rows that carry
 * learner attempts, so duplicating one would fragment reporting.
 *
 * The copy is applied to client state and persisted by the normal
 * full-structure save. A per-component write would not survive it:
 * `save_module_authoring_structure` soft-deletes every component of the module
 * and re-upserts from the payload it is given.
 */
export function copyComponentIntoWeek(
  source: LibraryComponent,
  targetWeekId: string,
  targetModuleId: string,
): ModuleComponent {
  // `componentType` is the authoring type; `type` is a shared human label.
  const type = (source.componentType || 'reading') as ModuleComponentType;
  const definition = getComponentDefinition(type);
  return {
    id: makeAuthoringId('component'),
    copiedFromId: source.id,
    moduleId: targetModuleId,
    weekId: targetWeekId,
    type,
    title: source.title || definition.label,
    description: source.description || '',
    expectedOtjh: Number(source.expectedOtjh ?? definition.defaultOtjh) || 0,
    points: Number(source.points ?? definition.defaultPoints) || 0,
    reflectionRequired: Boolean(source.reflectionRequired ?? definition.reflectionDefault),
    reflectionQuestion: String(
      source.reflectionQuestion
      ?? (source.settings as ComponentSettings | undefined)?.reflectionPrompt
      ?? getDefaultComponentSettings(type).reflectionPrompt
      ?? '',
    ),
    workplaceEvidenceRequired: Boolean(source.workplaceEvidenceRequired ?? definition.workplaceEvidenceDefault),
    tutorValidationRequired: Boolean(source.tutorValidationRequired ?? definition.tutorValidationDefault),
    coachValidationRequired: source.coachValidationRequired !== false,
    ksbMappings: (source.ksbMappings || []).map(mapping => {
      const type = (mapping.type || mapping.classification || 'secondary') as KsbMappingType;
      const weightClass = (mapping.weightClass || mapping.weight_class || 'soft') as KsbWeightClass;
      return {
        ...mapping,
        id: makeAuthoringId('ksb'),
        type,
        // The API widens these to string; narrow both spellings together so the
        // copy carries the same classification the source had.
        classification: type,
        weightClass,
        weight_class: weightClass,
      };
    }),
    settings: normaliseComponentSettings(type, withoutRecordingLink(withoutGroupAssignmentSettings({
      ...getDefaultComponentSettings(type),
      ...((source.settings || {}) as ComponentSettings),
    }))),
  };
}

/**
 * Copy a component that's being edited into a *different* week — possibly in
 * another group's module entirely — as an independent snapshot.
 *
 * Same id-regeneration + `copiedFromId` provenance convention as
 * `copyComponentIntoWeek`: the placed copy never reaches back into the
 * component it came from, and vice versa.
 */
export function copyComponentToWeek(
  source: ModuleComponent,
  targetWeekId: string,
  targetModuleId: string,
): ModuleComponent {
  // A Teams booking belongs to the source module. Carrying its
  // `teamsLiveSessionId` into another module either moves the source calendar
  // or collides with the target module's one-active-calendar constraint. Keep
  // the join URL, however: Assigned Groups intentionally lets another group
  // open the same meeting. Only the local calendar identity/date are removed.
  const sourceSettings = structuredClone(source.settings || {});
  const copiedSettings = source.type === 'live-session'
    ? independentCopySettings(sourceSettings)
    : structuredClone(withoutGroupAssignmentSettings(sourceSettings));
  if (source.type === 'live-session') {
    const teamsLink = String(sourceSettings.liveSessionUrl || sourceSettings.teamsMeetingUrl || '').trim();
    if (teamsLink) {
      // Populate both supported spellings so the Week Builder and legacy
      // learner/calendar readers resolve the same link after the copy.
      copiedSettings.liveSessionUrl = teamsLink;
      copiedSettings.teamsMeetingUrl = teamsLink;
    }
  }
  return {
    ...source,
    id: makeAuthoringId('component'),
    copiedFromId: source.id,
    moduleId: targetModuleId,
    weekId: targetWeekId,
    // The meeting URL belongs to the Teams meeting-link settings, not the
    // learner-facing session title. Keeping the source title unchanged also
    // avoids turning every placement into a different session name.
    title: source.title,
    settings: withoutRecordingLink(copiedSettings),
    ksbMappings: source.ksbMappings.map(mapping => ({ ...mapping, id: makeAuthoringId('ksb') })),
  };
}

/**
 * An independent copy of a complete week for a different delivery module.
 *
 * The target module owns its own timetable, so the copy deliberately has no
 * date and every live-session component has its Microsoft booking removed.
 * The authoring settings, KSB mappings, outcomes and all non-booking component
 * details remain intact.
 */
export function copyWeekToModule(
  source: ModuleWeek,
  targetModuleId: string,
  targetWeekNumber: number,
): ModuleWeek {
  const copyId = makeAuthoringId('WEEK');
  return {
    ...source,
    id: copyId,
    copiedFromId: source.id,
    moduleId: targetModuleId,
    weekNumber: targetWeekNumber,
    sessionDate: '',
    sessionDay: '',
    sessionStartTime: '',
    sessionDurationMinutes: undefined,
    // Undated, so whether this copy clashes with anything is a fresh question.
    // The text comes across; publishing it is the new module's own decision.
    holidayNoteEnabled: false,
    summary: source.summary || '',
    learningOutcomes: [...(source.learningOutcomes || [])],
    ksbMappings: (source.ksbMappings || []).map(mapping => ({ ...mapping, id: makeAuthoringId('KSB') })),
    components: relinkCopiedComponents(source.components || [], (source.components || []).map(component => ({
      ...component,
      id: makeAuthoringId('COMP'),
      copiedFromId: component.id,
      moduleId: targetModuleId,
      weekId: copyId,
      ksbMappings: (component.ksbMappings || []).map(mapping => ({ ...mapping, id: makeAuthoringId('KSB') })),
      settings: independentCopySettings(structuredClone(component.settings || {})),
    }))),
  };
}

/**
 * A week's twin, inserted directly beneath it in the same module.
 *
 * Everything the author wrote comes across in full — title, summary, learning
 * outcomes, week KSBs, and every component with its own description, settings,
 * KSB mappings, OTJH, points and assurance flags — as an independent snapshot.
 * Every id is regenerated and every settings object is deep-copied, so editing
 * or deleting the twin never reaches back into the week it came from, and vice
 * versa (the same convention as `copyComponentToWeek`, which is why the
 * components carry `copiedFromId` for provenance).
 *
 * The live session comes across too, but as authoring only: its purpose, its
 * clock, its duration, its organizer mailbox and its meeting options are things
 * somebody typed, and a copy that dropped them would only have to be typed
 * again. What it does NOT bring is the booking — the meeting ids and the join
 * link, stripped by `independentCopySettings`. A copy holding
 * `teamsLiveSessionId` is not a second meeting, it is the SAME meeting named
 * twice: one join link, one set of occurrences, one attendance record and one
 * recording, now claimed by two weeks, so editing the copy would reach into the
 * calendar of the week it was copied from. The twin's session is therefore
 * planned but not booked, and `isUnbookedCopiedLiveSession` is how the rail says
 * so until somebody books it.
 *
 * Dates do not come across either. They belong to the module's dated session
 * plan, not to the week being copied, so the twin arrives undated and
 * `applyModuleWeekSessionPlan` re-dates the run around it — exactly what already
 * happens when a week is dragged up the rail.
 *
 * Weeks after the insertion point are renumbered, and a title that only ever
 * repeated its own number ("Week 5") is renumbered with it; an authored title is
 * left alone apart from the copy's own " copy" suffix.
 */
export function duplicateWeekInModule(module: ModuleCatalogueItem, weekId: string): ModuleCatalogueItem {
  const sourceIndex = module.weekStructure.findIndex(week => week.id === weekId);
  if (sourceIndex < 0) return module;
  const source = module.weekStructure[sourceIndex];
  const copyId = makeAuthoringId('WEEK');
  const copy: ModuleWeek = {
    ...source,
    id: copyId,
    copiedFromId: source.id,
    moduleId: source.moduleId || module.id,
    title: weekAuthoredTitle(source) ? `${String(source.title).trim()} copy` : source.title,
    sessionDate: '',
    sessionDay: '',
    holidayNoteEnabled: false,
    summary: source.summary || '',
    learningOutcomes: [...(source.learningOutcomes || [])],
    ksbMappings: (source.ksbMappings || []).map(mapping => ({ ...mapping, id: makeAuthoringId('KSB') })),
    components: relinkCopiedComponents(source.components || [], (source.components || []).map(component => ({
      ...component,
      id: makeAuthoringId('COMP'),
      copiedFromId: component.id,
      moduleId: module.id,
      weekId: copyId,
      ksbMappings: (component.ksbMappings || []).map(mapping => ({ ...mapping, id: makeAuthoringId('KSB') })),
      settings: independentCopySettings(structuredClone(component.settings || {})),
    })), { sameModule: true }),
  };
  const weekStructure = [
    ...module.weekStructure.slice(0, sourceIndex + 1),
    copy,
    ...module.weekStructure.slice(sourceIndex + 1),
  ].map((week, index) => ({
    ...week,
    weekNumber: index + 1,
    // Only a title that IS its own number gets renumbered. A week whose title
    // the author cleared stays cleared — filling one in here would write over a
    // decision, not follow one.
    title: String(week.title || '').trim() && !weekAuthoredTitle(week) ? `Week ${index + 1}` : week.title,
  }));
  // `weeks`, `sessionsNumber` and the derived totals are `recalculateModule`'s
  // to fill in -- the twin's own live session is one more authored session, and
  // the reloaded plan dates it from the module's own start date.
  return { ...module, weekStructure };
}

/**
 * Copy one component from its week into a different week of the SAME module —
 * the clone button's "copy to another week" option. Every id is regenerated so
 * the copy is independent of its source, exactly like `duplicateWeekInModule`
 * treats each of the components it carries into a cloned week. A live
 * session's settings drop the Teams meeting identity and its own date via
 * `independentCopySettings`: sharing a booking across two weeks would point
 * both at the same meeting, and the target week's date is the twin's to take,
 * not the source's to keep. `copiedFromId` is what `isUnbookedCopiedLiveSession`
 * reads to print the "not booked yet" notice on it, same as a copied week.
 *
 * Named apart from `copyComponentToWeek` below, which serves a different
 * placement flow (into another group's module entirely) and does not carry
 * this same-module Teams-safety.
 */
export function cloneComponentToWeek(component: ModuleComponent, targetWeekId: string, targetModuleId: string): ModuleComponent {
  return {
    ...component,
    id: makeAuthoringId('COMP'),
    copiedFromId: component.id,
    moduleId: targetModuleId,
    weekId: targetWeekId,
    ksbMappings: (component.ksbMappings || []).map(mapping => ({ ...mapping, id: makeAuthoringId('KSB') })),
    settings: independentCopySettings(structuredClone(component.settings || {})),
  };
}

/**
 * A live session that was copied and has not been booked in its own right.
 *
 * `duplicateWeekInModule` hands the twin a live session with everything the
 * author wrote and nothing Microsoft made: no meeting id, no join link. That is
 * the only safe copy — sharing the booking would share one meeting between two
 * weeks — but it leaves a session that looks completely ordinary while having
 * nowhere for anybody to join, which is not something a reader can see. This is
 * the test the Course structure rail prints its note from, and it stops being
 * true the moment the week's meeting is created.
 */
export function isUnbookedCopiedLiveSession(component: ModuleComponent): boolean {
  if (component.type !== 'live-session') return false;
  if (!String(component.copiedFromId || '').trim()) return false;
  const settings = component.settings || {};
  return !String(settings.teamsLiveSessionId || '').trim()
    && !String(settings.teamsMeetingUrl || '').trim()
    && !String(settings.liveSessionUrl || '').trim();
}

export async function deleteModuleStructure(moduleCatalogueId: string) {
  await apiJson<{ deleted?: boolean; archived?: boolean; deletedAuthoring?: boolean; id?: string }>(`/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/`, {
    method: 'DELETE',
  });
  // As in `saveModuleStructure`: this write bypasses the shared cache, so the
  // cached structure of a module that no longer exists has to be dropped here.
  invalidateCurriculumCacheByEntity('module');
}

/**
 * A module's authored weeks and components.
 *
 * Read through the shared curriculum cache rather than this file's bare
 * `apiJson`, because several places want the same module at the same moment --
 * the Module Builder, the module workspace, the module drawer's session preview,
 * the week builder's place-component drawer -- and StrictMode mounts each of
 * them twice in development. Uncached, that was four to six identical requests
 * for one payload, queued behind each other on the server until their timeout
 * aborted them; shared, it is one request and a two-minute answer.
 *
 * Opening an editor uses `revalidate`: the server cache is invalidated on writes
 * and carries the revision belonging to its payload. PATCH refuses a superseded
 * revision. Conflict recovery can still force a rebuild using `skipCache`.
 */
export async function loadModuleStructure(
  catalogueId: string,
  options: { skipCache?: boolean; revalidate?: boolean } = {},
): Promise<ModuleCatalogueItem | null> {
  try {
    return adoptServerModule(await fetchCurriculumJson<ModuleCatalogueItem>(
      `/curriculum/modules/${encodeURIComponent(catalogueId)}/structure/`,
      // Full authored content can take longer on a cold remote-database read.
      { timeoutMs: 90000, skipCache: options.skipCache, revalidate: options.revalidate },
    ));
  } catch (err) {
    const status = err instanceof ApiError || err instanceof CurriculumApiError ? err.status : 0;
    if (status === 404) return null;
    throw err;
  }
}

/**
 * Where this module's weeks would run if it had `weeks` of them.
 *
 * Adding or removing a week in the builder changes the timetable before anything
 * is saved, and the dates are the backend's to generate -- it is the only place
 * that knows the delivery day, the cohort's ticked holidays and the shifts they
 * cause. Asking for them keeps the rail showing the dates the save will store
 * rather than a second schedule worked out in the browser.
 *
 * Always asked for by `weeks`, never `sessions`: a week is a calendar week
 * whether or not a live session has been authored into it yet, so a module with
 * fewer authored live sessions than weeks (a reading-only tail, a copied week
 * whose session is not booked yet) still gets every week dated. Asking by
 * `sessions` instead stops the plan dead at the last authored session and
 * leaves every week after it undated, which is exactly the bug this avoids.
 *
 * Returns null for a module the backend has never stored (a local draft), where
 * there is no schedule to plan from yet.
 */
export async function loadModuleWeekSessionPlan(moduleCatalogueId: string, weeks: number): Promise<ModuleWeekSessionPlan | null> {
  const catalogueId = String(moduleCatalogueId || '').trim();
  const count = Math.max(0, Math.round(Number(weeks) || 0));
  if (!catalogueId || !count) return null;
  try {
    return await apiJson<ModuleWeekSessionPlan>(
      `/curriculum/modules/${encodeURIComponent(catalogueId)}/session-plan/?weeks=${count}`,
    );
  } catch (err) {
    // A module with no stored schedule simply has no dates to show. Failing the
    // add-a-week the person just made would be the worse answer.
    console.warn('No session plan could be generated for this module.', err);
    return null;
  }
}

/**
 * The same plan, for a caller that must fail loudly instead of showing nothing.
 *
 * Sending the plan to a Teams calendar is the one use that cannot treat "no
 * plan" as "no dates yet": the reader would be told the invitations moved when
 * nothing was sent. `weeks` is optional here because a caller acting on someone
 * else's module (a group save, say) knows the module only by id -- the server
 * falls back to the module's own stored count.
 */
export function fetchModuleSessionPlan(
  moduleCatalogueId: string,
  weeks?: number,
  options: { timeoutMs?: number } = {},
): Promise<ModuleWeekSessionPlan> {
  const count = Math.max(0, Math.round(Number(weeks) || 0));
  // Teams reconciliation must never be satisfied by a browser or shared
  // curriculum cache: a Group day edit can land while this module builder is
  // still open. `skipCache` also sends no-store/no-cache headers through the
  // shared transport, so the plan is the server's current delivery pattern.
  return fetchCurriculumJson<ModuleWeekSessionPlan>(
    `/curriculum/modules/${encodeURIComponent(String(moduleCatalogueId || '').trim())}/session-plan/${count ? `?weeks=${count}` : ''}`,
    // A caller rendering a spinner needs a budget: without one a slow backend
    // leaves it spinning on the browser's own default, which is minutes.
    { ...(options.timeoutMs ? { timeoutMs: options.timeoutMs } : {}), skipCache: true },
  );
}

export interface ModuleStructureResolveRequest {
  requestId: string;
  identifier: string;
  identifiers?: string[];
}

export interface ModuleStructureResolveResult {
  requestId: string;
  identifier: string;
  catalogueId: string;
  found: boolean;
  missing?: boolean;
  // Set when several Module Builder modules share the linked title and no id
  // identified which one. Distinct from `missing`: the content exists but cannot
  // be attributed, so it must never be rendered as an empty module.
  ambiguous?: boolean;
  ambiguousCatalogueIds?: string[];
  componentCount?: number;
  hasComponents?: boolean;
  message?: string;
  module?: ModuleCatalogueItem;
}

export async function loadModuleStructuresBatch(modules: ModuleStructureResolveRequest[]): Promise<ModuleStructureResolveResult[]> {
  const response = await apiJson<{ results: ModuleStructureResolveResult[] }>('/curriculum/modules/resolve-structures/', {
    method: 'POST',
    body: JSON.stringify({ modules }),
    timeoutMs: 8000,
  });
  return response.results.map(result => ({
    ...result,
    module: result.module ? recalculateModule(result.module) : undefined,
  }));
}

export async function updateModuleSettings(moduleCatalogueId: string, payload: Partial<ModuleCatalogueItem>) {
  const response = await apiJson<{ updated: boolean; module: ModuleCatalogueItem }>(`/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/settings/`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
  // Same reason as `saveModuleStructure`: an `apiJson` write invalidates nothing.
  invalidateCurriculumCacheByEntity('module');
  return recalculateModule(response.module);
}

export function createLegacyLocalModule(input: { programme: string; title: string; description: string; weeks: number; status: ModuleStatus }): ModuleCatalogueItem {
  const catalogueId = makeAuthoringId('MOD');
  const id = `local-${catalogueId}`;
  const weekStructure = Array.from({ length: Math.max(1, input.weeks || 1) }, (_, index) => createEmptyWeek(id, index + 1));
  return recalculateModule({
    id,
    catalogueId,
    // Never derive an id from the programme NAME, and never persist a
    // placeholder. An unassigned legacy draft has no programme id.
    programmeId: '',
    programmeName: input.programme || 'Unassigned programme',
    title: input.title,
    description: input.description,
    status: input.status || 'draft',
    sessionsNumber: weekStructure.length,
    startDate: '',
    endDate: '',
    weeks: weekStructure.length,
    totalOtjh: 0,
    ksbCount: 0,
    lessonCount: 0,
    quizCount: 0,
    qualityScore: 0,
    moduleKsbMappings: [],
    completionCriteria: emptyCompletionCriteria(),
    advancedDetails: emptyAdvancedDetails(),
    background: '',
    epaRequirements: [],
    qualificationOutcomes: [],
    weekStructure,
  });
}

/**
 * How many authored week/live-session titles the module has.
 *
 * A `?compact=true` list omits `sessionNames` outright -- the strings are the
 * bulk of that response and nothing here renders them -- and sends
 * `sessionNamesCount` in their place. The full response still carries the array,
 * so both shapes have to answer.
 */
function sessionNameCount(module: CurriculumModule): number {
  return module.sessionNames?.length || module.sessionNamesCount || 0;
}

export function curriculumModuleToCatalogue(module: CurriculumModule): ModuleCatalogueItem {
  const canonicalId = canonicalModuleCatalogueId(module);
  // Temporary legacy fallback: unlinked delivery rows still open through their delivery identifier.
  const catalogueId = canonicalId || String(module.deliveryModuleId || module.id || module.sourceId);
  const id = `module-${catalogueId}`;
  const title = cleanUserFacingText(module.name) || `Module ${catalogueId}`;
  const description = cleanUserFacingText(module.notes || '');
  const tutor = String(module.tutor || '').trim();
  const coach = String(module.coach || '').trim();
  const totalOtjh = Number(module.declaredTotalOtjh ?? module.totalOtjh ?? 0) || 0;
  const weekStructure = (module.weekStructure || []).map((week, index): ModuleWeek => {
    const weekId = String(week.id || makeAuthoringId('WEEK'));
    return {
      id: weekId,
      moduleId: catalogueId,
      weekNumber: week.weekNumber || index + 1,
      title: week.title || `Week ${index + 1}`,
      summary: '',
      learningOutcomes: [],
      components: (week.components || []).map((component, componentIndex): ModuleComponent => ({
        id: String(component.id || makeAuthoringId('COMP')),
        sourceId: component.id,
        moduleId: catalogueId,
        weekId,
        type: component.type as ModuleComponentType,
        title: component.title || `Component ${componentIndex + 1}`,
        description: '',
        expectedOtjh: Number(component.expectedOtjh ?? component.duration ?? 0) || 0,
        points: Number(component.points ?? 0) || 0,
        reflectionRequired: Boolean(component.reflectionRequired),
        reflectionQuestion: String(component.reflectionQuestion || ''),
        workplaceEvidenceRequired: Boolean(component.workplaceEvidenceRequired),
        tutorValidationRequired: Boolean(component.tutorValidationRequired),
        coachValidationRequired: component.coachValidationRequired !== false,
        ksbMappings: (component.ksbMappings || []) as KsbMapping[],
        settings: normaliseComponentSettings(component.type as ModuleComponentType, (component.settings || {}) as ComponentSettings),
      })),
      ksbMappings: [],
    };
  });
  return recalculateModule({
    id,
    catalogueId,
    programmeId: module.programmeId || module.programme || 'programme',
    programmeName: module.programme || 'Unassigned programme',
    cohortId: module.cohortId || '',
    cohort: module.cohort || '',
    groupId: module.groupId || '',
    group: module.group || '',
    learnerRosterMode: module.learnerRosterMode || 'inherited',
    teamsSharedSourceModuleId: module.teamsSharedSourceModuleId || '',
    ksbRemapReport: (module.ksbRemapReport || null) as unknown as KsbRemapReport | null,
    isProgrammeDeleted: Boolean(module.isProgrammeDeleted),
    title,
    description,
    coverImage: String(module.coverImage || '').trim(),
    createdAt: module.createdAt || '',
    lastUpdated: module.lastUpdated || '',
    status: module.status || 'draft',
    authoringStatus: module.authoringStatus || module.status || 'draft',
    sourceType: undefined,
    sourceId: undefined,
    deliveryStatus: module.deliveryStatus,
    ksbProfileSourceId: module.ksbProfileSourceId || '',
    tutor,
    coach,
    weeklySchedule: module.weeklySchedule || [],
    sessionHolidays: module.sessionHolidays || [],
    deliveryWeeks: module.deliveryWeeks,
    deliveryMetadata: {
      tutor,
      coach,
      weekDays: module.weekDays || '',
      startTime: module.startTime || '',
      endTime: module.endTime || '',
      cohortId: module.cohortId || '',
      cohort: module.cohort || '',
      groupId: module.groupId || '',
      group: module.group || '',
    },
    sessionsNumber: module.sessionsNumber || module.weeks || sessionNameCount(module) || 0,
    startDate: module.startDate || '',
    endDate: module.endDate || '',
    // `sessionsNumber` is a last-resort fallback for payloads predating the
    // weeks/sessions split, where the two were the same number. Current
    // responses always carry `weeks`, so it is normally never reached.
    weeks: module.weeks || sessionNameCount(module) || module.sessionsNumber || 1,
    totalOtjh,
    declaredTotalOtjh: totalOtjh,
    ksbCount: module.ksbCount || module.ksbCodes?.length || 0,
    lessonCount: module.lessons || sessionNameCount(module) || 0,
    quizCount: module.quizzes || 0,
    qualityScore: 0,
    assignedLearnerCount: module.assignments ?? 0,
    moduleKsbMappings: (module.ksbCodes || []).map((code, index) => ({
      id: makeAuthoringId('KSBMAP'),
      ksbId: code,
      code,
      description: `Mapped KSB ${code}`,
      type: index < 3 ? 'main' : 'secondary',
      weight: index < 3 ? 40 : 20,
      weightClass: index < 3 ? 'hard' : 'soft',
    })),
    completionCriteria: emptyCompletionCriteria(),
    advancedDetails: emptyAdvancedDetails(),
    background: '',
    epaRequirements: [],
    qualificationOutcomes: [],
    weekStructure,
    sourceModule: module,
  });
}

export function getDefaultStructure(module: ModuleCatalogueItem): ModuleCatalogueItem {
  if (module.weekStructure.length) return recalculateModule(module);

  const source = module.sourceModule;
  // Weeks first: this builds the week skeleton, and `sessionsNumber` is the
  // delivery-day-multiplied calendar total, which would over-create weeks for
  // any group running more than one session a week.
  const weekCount = Math.max(1, module.weeks || source?.weeks || (source ? sessionNameCount(source) : 0) || module.sessionsNumber || source?.sessionsNumber || 1);
  const weekStructure = Array.from({ length: weekCount }, (_, index) => {
    const week = createEmptyWeek(module.id, index + 1);
    return week;
  });

  return recalculateModule({ ...module, weekStructure });
}

export function recalculateModule(module: ModuleCatalogueItem): ModuleCatalogueItem {
  const moduleId = String(module.catalogueId || module.id);
  const fallbackKsbSource = moduleKsbSourceMetadata(module.ksbProfileSourceId);
  const moduleKsbMappings = normaliseKsbMappings(module.moduleKsbMappings || [], fallbackKsbSource);
  const completionCriteria = {
    ...emptyCompletionCriteria(),
    ...(module.completionCriteria || {}),
  };
  const normalisedWeeks = module.weekStructure.map((week, index) => {
    const weekId = String(week.id || makeAuthoringId('WEEK'));
    return {
      ...week,
      id: weekId,
      moduleId,
      ksbMappings: normaliseKsbMappings(week.ksbMappings || [], fallbackKsbSource),
      weekNumber: index + 1,
      components: (week.components || [])
        .filter(component => !isGeneratedWeekPlaceholderComponent(component, { ...week, weekNumber: index + 1 }))
        .map(component => ({
          ...component,
          moduleId,
          weekId,
          workplaceEvidenceRequired: false,
          // Normalised only when the component actually carries it. Filling in
          // `true` for a component object that had lost the key looked harmless
          // -- absent did mean on -- but this value goes straight into the next
          // structure save, so it did not describe the component, it OVERWROTE
          // it: an author who had turned coach validation off got it switched
          // back on by a save they never made a decision in. What the component
          // does not say, only the stored row knows, and the backend keeps it
          // (see component_assurance_flag).
          ...(component.coachValidationRequired === undefined
            ? {}
            : { coachValidationRequired: component.coachValidationRequired !== false }),
          ksbMappings: normaliseKsbMappings(component.ksbMappings || [], fallbackKsbSource),
          settings: normaliseComponentSettings(component.type, component.settings || {}),
        })),
    };
  });
  const allComponents = normalisedWeeks.flatMap(week => week.components);
  const hasStructure = module.weekStructure.length > 0;
  const componentKsbCodes = new Set(allComponents.flatMap(component => component.ksbMappings.map(mapping => mapping.code)));
  moduleKsbMappings.forEach(mapping => componentKsbCodes.add(mapping.code));
  normalisedWeeks.forEach(week => week.ksbMappings.forEach(mapping => componentKsbCodes.add(mapping.code)));
  const componentTotalOtjh = normalisedWeeks.reduce((total, week) => total + weekExpectedOtjhTotal(week), 0);
  const totalOtjh = hasStructure ? componentTotalOtjh : Number(module.totalOtjh || 0);
  const totalPoints = allComponents.reduce((total, component) => total + Number(component.points || 0), 0);
  const quality = calculateQualityScore({ ...module, completionCriteria, totalOtjh, ksbCount: componentKsbCodes.size, moduleKsbMappings, weekStructure: normalisedWeeks });
  // Transport only: the server's per-item fingerprints travel beside the module
  // (see serverCopyOf), never inside the copy the workspace edits and compares.
  const { itemFingerprints: _itemFingerprints, ...moduleFields } = module as ModuleCatalogueItem & { itemFingerprints?: unknown };

  return {
    ...moduleFields,
    completionCriteria,
    // The authored week count is derived from the structure -- adding a seventh
    // week makes this a seven-week module, and a save has to carry that rather
    // than the stale stored number.
    weeks: hasStructure ? normalisedWeeks.length : (module.weeks || module.sessionsNumber || 0),
    // Keep planned dates for unfinished weeks, while imported live sessions
    // cannot leave the calendar on the smaller count from the original draft.
    // Reading-only rows add no calendar session.
    sessionsNumber: Math.max(module.sessionsNumber || 0, allComponents.filter(component => component.type === 'live-session').length),
    totalOtjh,
    // The module's OTJH is the sum of every component's Expected OTJH, across
    // every week -- nothing else. `declaredTotalOtjh` is kept only because the
    // API still returns it; it is pinned to the derived sum here so a stale
    // server aggregate can never show up beside a freshly recalculated one.
    declaredTotalOtjh: totalOtjh,
    ksbCount: hasStructure ? componentKsbCodes.size : module.ksbCount,
    lessonCount: hasStructure ? allComponents.length : module.lessonCount,
    quizCount: hasStructure ? allComponents.filter(component => ['quiz', 'checkpoint', 'monthly-ksb-quiz'].includes(component.type)).length : module.quizCount,
    qualityScore: quality,
    moduleKsbMappings,
    weekStructure: normalisedWeeks,
    description: cleanUserFacingText(module.description || module.sourceModule?.notes || ''),
    background: module.background || '',
    epaRequirements: module.epaRequirements || [],
    qualificationOutcomes: module.qualificationOutcomes || [],
  };
}

function normaliseKsbMappings(mappings: KsbMapping[], fallbackSource?: Pick<KsbMapping, 'sourceType' | 'sourceId'>) {
  return mappings.map(mapping => {
    const type = normaliseKsbMappingType(mapping.type || mapping.classification);
    const classification = normaliseKsbMappingType(mapping.classification || mapping.type);
    const weightClass = normaliseKsbWeightClass(mapping.weightClass || mapping.weight_class, classification);
    const weight = clampKsbWeight(mapping.weight);
    return {
      ...mapping,
      sourceType: mapping.sourceType || fallbackSource?.sourceType,
      sourceId: mapping.sourceId || fallbackSource?.sourceId,
      type,
      classification,
      weightClass,
      weight_class: weightClass,
      weight: weight > 0 ? weight : defaultKsbWeight(classification),
    };
  });
}

function moduleKsbSourceMetadata(sourceId?: string) {
  const id = String(sourceId || '').trim();
  if (!id) return undefined;
  return {
    sourceType: id.startsWith('standard:') ? 'standard' : 'framework',
    sourceId: id,
  };
}

function defaultKsbWeight(type: KsbMappingType) {
  if (type === 'main') return 40;
  if (type === 'secondary') return 20;
  return 10;
}

function clampKsbWeight(value: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.round(parsed * 100) / 100);
}

function normaliseKsbMappingType(value?: string): KsbMappingType {
  const raw = String(value || '').trim().toLowerCase();
  if (raw === 'main' || raw === 'secondary' || raw === 'possible') return raw;
  if (raw === 'practice') return 'possible';
  return 'secondary';
}

function normaliseKsbWeightClass(value?: string, fallbackClassification?: KsbMappingType): KsbWeightClass {
  const raw = String(value || '').trim().toLowerCase();
  if (raw === 'hard' || raw === 'soft' || raw === 'possible') return raw;
  const legacy = normaliseKsbMappingType(fallbackClassification);
  if (legacy === 'main') return 'hard';
  if (legacy === 'possible') return 'possible';
  return 'soft';
}

export function calculateQualityChecklist(module: ModuleCatalogueItem) {
  const allComponents = module.weekStructure.flatMap(week => week.components);
  const liveSessions = allComponents.filter(component => component.type === 'live-session');
  const criteriaConfigured =
    module.completionCriteria.quizzesCompletedRequired ||
    module.completionCriteria.checkpointsCompletedRequired ||
    module.completionCriteria.averageScoreRequiredEnabled ||
    module.completionCriteria.totalScoreRequiredEnabled ||
    Boolean(module.completionCriteria.additionalNotes.trim());
  const expectedTotal = module.weekStructure.reduce((total, week) => total + weekExpectedOtjhTotal(week), 0);
  const declaredTotal = typeof module.declaredTotalOtjh === 'number' && module.declaredTotalOtjh > 0 ? module.declaredTotalOtjh : module.totalOtjh;

  return [
    { label: 'Number of weeks defined', passed: module.weekStructure.length > 0 },
    { label: 'Each week has a title', passed: module.weekStructure.every(week => week.title.trim()) },
    { label: 'Each week has at least one component', passed: module.weekStructure.every(week => week.components.length > 0) },
    { label: 'Teams session placeholder where expected', passed: liveSessions.every(component => component.title.trim()) },
    { label: 'Recording placeholder for live sessions', passed: liveSessions.every(component => typeof component.settings.recordingExpected === 'boolean') },
    { label: 'All components have estimated OTJH greater than 0', passed: allComponents.length > 0 && allComponents.every(component => Number(component.expectedOtjh) > 0) },
    { label: 'All components have at least one KSB mapping', passed: allComponents.length > 0 && allComponents.every(component => component.ksbMappings.length > 0) },
    { label: 'All mapped KSBs have a weight', passed: allMappedKsbs(module).every(mapping => Number(mapping.weight || 0) > 0) },
    { label: 'Every mapped KSB is classified', passed: allMappedKsbs(module).every(mapping => ['main', 'secondary', 'possible'].includes(normaliseKsbMappingType(mapping.type))) },
    { label: 'Completion criteria configured', passed: criteriaConfigured },
    { label: 'Total module OTJH matches component sum', passed: Math.abs(declaredTotal - expectedTotal) < 0.01 },
  ];
}

export function calculateQualityScore(module: ModuleCatalogueItem) {
  const checklist = calculateQualityChecklist(module);
  const passed = checklist.filter(item => item.passed).length;
  return Math.round((passed / checklist.length) * 100);
}

export function allMappedKsbs(module: ModuleCatalogueItem) {
  return [
    ...module.moduleKsbMappings,
    ...module.weekStructure.flatMap(week => week.ksbMappings),
    ...module.weekStructure.flatMap(week => week.components.flatMap(component => component.ksbMappings)),
  ];
}

export function flattenKsbEntries(entries: CurriculumKsbEntry[] = []): KsbOption[] {
  return entries.map(entry => ({
    id: String(entry.id || entry.code),
    code: entry.code || entry.fullCode || entry.rawCode || String(entry.id),
    description: entry.description || entry.title || '',
    type: entry.type,
    title: entry.title,
  }));
}

/**
 * A save refused because the module moved on after this copy of it was read.
 *
 * Its own type rather than a status code the caller has to remember to check,
 * because the two outcomes need opposite handling: an ordinary failure is worth
 * retrying with the same payload, and this one is never worth retrying -- the
 * payload replaces every week and component in the module, so re-sending it is
 * precisely the destruction the refusal prevented.
 */
/** One place a refused save and the stored module disagree, as the server found it. */
export interface ModuleSaveConflict {
  /** `weekStructure[W1].components[C1].title`, the same paths the browser merge uses. */
  path: string;
  kind: 'value' | 'local-deleted' | 'remote-deleted' | 'reorder';
  local?: unknown;
  remote?: unknown;
}

export class ModuleStructureConflictError extends Error {
  /** What the server holds now, so the workspace can show it or re-open it. */
  currentRevision: string;
  serverModule: ModuleCatalogueItem | null;
  /**
   * Set when the server merged the save item by item and still found the same
   * thing changed on both sides. Empty for the older whole-module refusal.
   */
  conflicts: ModuleSaveConflict[];

  constructor(message: string, currentRevision: string, serverModule: ModuleCatalogueItem | null, conflicts: ModuleSaveConflict[] = []) {
    super(message);
    this.name = 'ModuleStructureConflictError';
    this.currentRevision = currentRevision;
    this.serverModule = serverModule;
    this.conflicts = conflicts;
    Object.setPrototypeOf(this, ModuleStructureConflictError.prototype);
  }
}

/**
 * The server's request-body ceiling (`DATA_UPLOAD_MAX_MEMORY_SIZE` in
 * backend/config/settings.py). Past it Django refuses the PATCH with its own
 * HTML 400 page before the view runs, so the reader would only ever see
 * "returned 400". Kept here so the refusal can be explained instead.
 */
export const MODULE_STRUCTURE_SAVE_LIMIT_BYTES = 30 * 1024 * 1024;
// Django's own default ceiling. A non-JSON 400 on a body smaller than this is
// some other refusal, and is not blamed on size.
const DJANGO_DEFAULT_UPLOAD_LIMIT_BYTES = Math.floor(2.5 * 1024 * 1024);

function formatMegabytes(bytes: number) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A save refused because the module is bigger than the server accepts in one request. */
export class ModuleStructureTooLargeError extends Error {
  sizeBytes: number;

  constructor(sizeBytes: number, componentCount: number, limitKnown: boolean) {
    const limit = limitKnown ? ` The most one save can carry is ${formatMegabytes(MODULE_STRUCTURE_SAVE_LIMIT_BYTES)}.` : '';
    super(
      `This module is too large to save in one go: it is ${formatMegabytes(sizeBytes)} across ${componentCount} components.${limit}`
      + ' Your changes are still here and nothing was lost.'
      + ' Large content pasted straight into components, such as images, is the usual cause:'
      + ' remove or replace it with a link or uploaded file, or split the module, then save again.',
    );
    this.name = 'ModuleStructureTooLargeError';
    this.sizeBytes = sizeBytes;
    Object.setPrototypeOf(this, ModuleStructureTooLargeError.prototype);
  }
}

/**
 * Write a module's whole structure back.
 *
 * `expectedRevision` is the `structureRevision` that came with the copy being
 * saved. The backend refuses the write if the stored material has moved since,
 * which is the only thing standing between two open tabs and one of them
 * silently replacing the other's weeks and components. Omitting it restores
 * last-write-wins, so only a caller with nothing to have read -- a first save,
 * an import -- should leave it out.
 *
 * `source` says which kind of save this is, for the audit trail. It is metadata
 * and nothing more: the backend decides the actor, the action and the values
 * itself, and ignores any label it does not recognise. Auto-save is a *source*,
 * never an action -- the recorded event is still "component updated", so a
 * module left open all afternoon produces one entry per real edit rather than
 * one per timer tick.
 */
function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>)
      .filter(key => (value as Record<string, unknown>)[key] !== undefined)
      .sort()
      .map(key => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

export interface ModuleStructureDelta {
  /** Every week id, in order. Removing or reordering a week needs nothing else. */
  weekOrder: string[];
  /** Every week's component ids, in order -- so moves and removals carry no rows. */
  componentOrder: Record<string, string[]>;
  /** Weeks that are new or whose own fields changed, without their components. */
  weeks: Record<string, Omit<ModuleWeek, 'components'>>;
  /** Components that are new or changed. Everything else is the stored copy. */
  components: Record<string, ModuleComponent>;
}

/**
 * What changed between the last saved copy and this one.
 *
 * Compared with sorted keys, so two copies that differ only in the order their
 * fields were written in read as the same; a false "changed" would only send
 * a row the server then finds unchanged.
 */
export function moduleStructureDelta(base: ModuleCatalogueItem, current: ModuleCatalogueItem): ModuleStructureDelta {
  const baseWeeks = new Map(base.weekStructure.map(week => [week.id, week]));
  const baseComponents = new Map(base.weekStructure.flatMap(week => week.components.map(component => [component.id, component] as const)));
  const delta: ModuleStructureDelta = { weekOrder: [], componentOrder: {}, weeks: {}, components: {} };
  for (const week of current.weekStructure) {
    const { components, ...fields } = week;
    delta.weekOrder.push(week.id);
    delta.componentOrder[week.id] = components.map(component => component.id);
    const baseWeek = baseWeeks.get(week.id);
    if (!baseWeek) {
      delta.weeks[week.id] = fields;
    } else {
      const { components: _baseComponents, ...baseFields } = baseWeek;
      if (stableStringify(fields) !== stableStringify(baseFields)) delta.weeks[week.id] = fields;
    }
    for (const component of components) {
      const baseComponent = baseComponents.get(component.id);
      if (!baseComponent || stableStringify(component) !== stableStringify(baseComponent)) delta.components[component.id] = component;
    }
  }
  return delta;
}

export async function saveModuleStructure(
  moduleCatalogueId: string,
  payload: ModuleCatalogueItem,
  options: {
    expectedRevision?: string;
    source?: 'auto-save' | 'manual';
    /**
     * The copy last agreed with the server at `expectedRevision`. With both,
     * only what changed since is sent and written -- see moduleStructureDelta.
     * Without either, the whole structure is sent, as every other caller does.
     */
    base?: ModuleCatalogueItem | null;
    /**
     * The server's own copy of `base` (serverCopyOf). With it, a save that lands
     * after someone else's is merged item by item on the server instead of being
     * refused -- see backend/curriculum_api/module_merge.py.
     */
    serverCopy?: ServerStructureCopy | null;
    /**
     * Ids of deleted weeks or components this save may bring back: ones this
     * workspace deleted itself, or the author chose to keep. Anything else the
     * server finds deleted is a conflict, never a silent restore.
     */
    restoreIds?: string[];
  } = {},
) {
  const recalculated = recalculateModule(payload);
  // `weeksNumber` states the authored week count outright. `weeks` cannot carry
  // it: on this endpoint that name is the legacy alias for the week *list*, and
  // the backend rejects a number there.
  const partial = Boolean(options.base && options.expectedRevision && options.base.catalogueId === recalculated.catalogueId);
  const { weekStructure: _weekStructure, ...moduleFields } = recalculated;
  const delta = partial ? moduleStructureDelta(recalculateModule(options.base as ModuleCatalogueItem), recalculated) : null;
  const body = partial && delta
    ? {
      ...moduleFields,
      weeksNumber: recalculated.weekStructure.length || recalculated.weeks,
      expectedRevision: options.expectedRevision,
      saveMode: 'partial',
      structureDelta: delta,
      ...(options.serverCopy && options.serverCopy.revision === options.expectedRevision
        ? { structureBase: moduleStructureBase(options.serverCopy, delta, recalculateModule(options.base as ModuleCatalogueItem)) }
        : {}),
      ...(options.restoreIds?.length ? { restoreIds: options.restoreIds } : {}),
    }
    : {
      ...recalculated,
      weeksNumber: recalculated.weekStructure.length || recalculated.weeks,
      ...(options.expectedRevision ? { expectedRevision: options.expectedRevision } : {}),
    };
  const serialized = JSON.stringify(body);
  const sizeBytes = new Blob([serialized]).size;
  const componentCount = recalculated.weekStructure.reduce((total, week) => total + week.components.length, 0);
  // Refused here rather than sent: the server would only answer with an HTML
  // 400 after the whole body had been uploaded.
  if (sizeBytes > MODULE_STRUCTURE_SAVE_LIMIT_BYTES) {
    throw new ModuleStructureTooLargeError(sizeBytes, componentCount, true);
  }
  const saved = adoptServerModule(await apiJson<ModuleCatalogueItem>(`/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/structure/`, {
    method: 'PATCH',
    body: serialized,
    headers: { 'X-Curriculum-Save-Source': options.source || 'manual' },
    timeoutMs: 90000,
  }).catch((err: unknown) => {
    // A deployment can set a lower ceiling than the one mirrored above. The
    // view always answers in JSON, so a body-less 400 on a large payload (or a
    // proxy's 413) is the size refusal.
    if (err instanceof ApiError && !err.data && (
      err.status === 413 || (err.status === 400 && sizeBytes > DJANGO_DEFAULT_UPLOAD_LIMIT_BYTES)
    )) {
      throw new ModuleStructureTooLargeError(sizeBytes, componentCount, false);
    }
    if (!(err instanceof ApiError) || err.status !== 409 || !err.data?.conflict) throw err;
    const server = err.data.module as ModuleCatalogueItem | undefined;
    throw new ModuleStructureConflictError(
      typeof err.data.error === 'string' ? err.data.error : err.message,
      String(err.data.currentRevision || ''),
      server ? adoptServerModule(server) : null,
      Array.isArray(err.data.mergeConflicts) ? err.data.mergeConflicts as ModuleSaveConflict[] : [],
    );
  }));
  // `apiJson` writes go straight to the network, so nothing above invalidates
  // the read this save just made stale. `loadModuleStructure` is cached now, and
  // a save that left the previous weeks in the cache would have the builder
  // re-read what it had just replaced.
  invalidateCurriculumCacheByEntity('module');
  return saved;
}

// ---------------------------------------------------------------------------
// The server's own copy of an open module.
//
// The workspace edits a normalised module: defaults filled in, KSB sources
// inferred, placeholders dropped. That is the right thing to edit and the wrong
// thing to merge against -- every default the browser added would read, on the
// server, as a change somebody made. So beside it the workspace keeps exactly
// what the server sent: each week and component as received, with the
// fingerprint the server computed for it. A save sends the server's copies of
// the items it touches, and the server can then tell precisely what anyone
// else changed since. The changes feed rebuilds this copy from a few changed
// items rather than re-reading the whole module.
// ---------------------------------------------------------------------------

export interface StructureFingerprints {
  weeks: Record<string, string>;
  components: Record<string, string>;
}

type RawRecord = Record<string, unknown>;

export interface ServerStructureCopy {
  revision: string;
  fingerprints: StructureFingerprints;
  /** Module fields as the server sent them, without the weeks. */
  module: RawRecord;
  /** Week fields as sent, without their components. */
  weeks: Record<string, RawRecord>;
  components: Record<string, RawRecord>;
  weekOrder: string[];
  componentOrder: Record<string, string[]>;
}

const serverCopies = new WeakMap<object, ServerStructureCopy>();

/** The server's copy a module was read or saved as, when the server sent one. */
export function serverCopyOf(module: object | null | undefined): ServerStructureCopy | null {
  return module ? serverCopies.get(module) || null : null;
}

function rawStructureCopy(raw: unknown): ServerStructureCopy | null {
  if (!raw || typeof raw !== 'object') return null;
  const payload = raw as RawRecord & { weekStructure?: unknown; itemFingerprints?: StructureFingerprints };
  const prints = payload.itemFingerprints;
  if (!prints || typeof prints !== 'object' || !prints.weeks || !prints.components) return null;
  const { weekStructure, itemFingerprints: _prints, ...module } = payload;
  const copy: ServerStructureCopy = {
    revision: String(payload.structureRevision || ''),
    fingerprints: { weeks: { ...prints.weeks }, components: { ...prints.components } },
    module,
    weeks: {},
    components: {},
    weekOrder: [],
    componentOrder: {},
  };
  for (const week of Array.isArray(weekStructure) ? weekStructure as RawRecord[] : []) {
    const weekId = String(week.id || '');
    if (!weekId) continue;
    const { components, ...fields } = week;
    copy.weekOrder.push(weekId);
    copy.weeks[weekId] = fields;
    copy.componentOrder[weekId] = [];
    for (const component of Array.isArray(components) ? components as RawRecord[] : []) {
      const componentId = String(component.id || '');
      if (!componentId) continue;
      copy.componentOrder[weekId].push(componentId);
      copy.components[componentId] = component;
    }
  }
  return copy;
}

function rawModuleFromCopy(copy: ServerStructureCopy): RawRecord {
  return {
    ...copy.module,
    structureRevision: copy.revision,
    weekStructure: copy.weekOrder.map(weekId => ({
      ...copy.weeks[weekId],
      components: (copy.componentOrder[weekId] || []).map(id => copy.components[id]).filter(Boolean),
    })),
  };
}

/** A module from the server: normalised for the workspace, its server copy kept beside it. */
function adoptServerModule(raw: unknown): ModuleCatalogueItem {
  const copy = rawStructureCopy(raw);
  const module = recalculateModule(raw as ModuleCatalogueItem);
  if (copy) serverCopies.set(module, copy);
  return module;
}

/**
 * The server's copies of the items a save touches, and the copy's orders.
 *
 * Only the items changed or removed travel in full; everything else is named
 * by its fingerprint, which is all the server needs to know it is untouched.
 * Beside each server copy goes the same item as this workspace holds it
 * (`local*`, normalised by the browser), so the server measures "did this
 * author change it?" against the author's own copy and "did someone else?"
 * against its own -- the browser's filled-in defaults never read as either.
 */
export function moduleStructureBase(copy: ServerStructureCopy, delta: ModuleStructureDelta, localBase?: ModuleCatalogueItem) {
  const localWeeks = new Set(delta.weekOrder);
  const localComponents = new Set(Object.values(delta.componentOrder).flat());
  const weekIds = new Set([...Object.keys(delta.weeks), ...copy.weekOrder.filter(id => !localWeeks.has(id))]);
  const componentIds = new Set([
    ...Object.keys(delta.components),
    ...Object.keys(copy.components).filter(id => !localComponents.has(id)),
  ]);
  const pick = (source: Record<string, RawRecord>, ids: Set<string>) => Object.fromEntries(
    [...ids].filter(id => source[id]).map(id => [id, source[id]]),
  );
  const browserWeeks: Record<string, RawRecord> = {};
  const browserComponents: Record<string, RawRecord> = {};
  localBase?.weekStructure.forEach(week => {
    const { components, ...fields } = week;
    if (weekIds.has(week.id)) browserWeeks[week.id] = fields as unknown as RawRecord;
    components.forEach(component => {
      if (componentIds.has(component.id)) browserComponents[component.id] = component as unknown as RawRecord;
    });
  });
  const localModule = localBase
    ? (({ weekStructure: _weeks, ...fields }) => fields)(localBase) as unknown as RawRecord
    : undefined;
  return {
    fingerprints: copy.fingerprints,
    module: copy.module,
    weekOrder: copy.weekOrder,
    componentOrder: copy.componentOrder,
    weeks: pick(copy.weeks, weekIds),
    components: pick(copy.components, componentIds),
    ...(localModule ? { localModule, localWeeks: browserWeeks, localComponents: browserComponents } : {}),
  };
}

interface ModuleStructureChangesResponse {
  revision: string;
  unchanged?: boolean;
  module?: RawRecord;
  weekOrder?: string[];
  componentOrder?: Record<string, string[]>;
  weeks?: Record<string, RawRecord>;
  components?: Record<string, RawRecord>;
  itemFingerprints?: StructureFingerprints;
}

export type ModuleStructureChanges =
  | { unchanged: true; revision: string }
  | { unchanged: false; module: ModuleCatalogueItem };

/**
 * The stored module, rebuilt from only what changed since `copy`.
 *
 * Kilobytes instead of the whole structure: the server compares its own
 * fingerprints with the ones this copy holds and sends back the items that
 * differ. The result is exactly what a full read would have returned. `null`
 * when the copy cannot be completed (the caller then reads the whole module).
 */
export async function loadModuleStructureChanges(
  catalogueId: string,
  copy: ServerStructureCopy,
  // Off when the caller needs the module itself even if nothing moved (taking
  // the stored version over unsaved edits): the copy is then rebuilt whole.
  options: { allowUnchanged?: boolean } = {},
): Promise<ModuleStructureChanges | null> {
  const allowUnchanged = options.allowUnchanged !== false;
  const changes = await apiJson<ModuleStructureChangesResponse>(`/curriculum/modules/${encodeURIComponent(catalogueId)}/changes/`, {
    method: 'POST',
    body: JSON.stringify({ revision: allowUnchanged ? copy.revision : '', fingerprints: copy.fingerprints }),
    timeoutMs: 60000,
  });
  if (changes.unchanged) return { unchanged: true, revision: String(changes.revision || copy.revision) };
  if (!changes.module || !changes.weekOrder || !changes.componentOrder || !changes.itemFingerprints) return null;
  const next: ServerStructureCopy = {
    revision: String(changes.revision || ''),
    fingerprints: changes.itemFingerprints,
    module: changes.module,
    weeks: {},
    components: {},
    weekOrder: changes.weekOrder,
    componentOrder: changes.componentOrder,
  };
  for (const weekId of changes.weekOrder) {
    const week = changes.weeks?.[weekId] || copy.weeks[weekId];
    if (!week) return null;
    next.weeks[weekId] = week;
    for (const componentId of changes.componentOrder[weekId] || []) {
      const component = changes.components?.[componentId] || copy.components[componentId];
      if (!component) return null;
      next.components[componentId] = component;
    }
  }
  const module = recalculateModule(rawModuleFromCopy(next) as unknown as ModuleCatalogueItem);
  serverCopies.set(module, next);
  return { unchanged: false, module };
}

export interface ModuleLiveState {
  /** False while live sync is switched off on the server: the editor stops asking. */
  enabled: boolean;
  epochs?: Record<string, number>;
  /** The estate-wide counter, used only as a slow backstop. */
  epoch?: number;
  pollMs?: number;
}

/** The counters for the scopes an open module watches (`module:<id>`, `group:<id>`). */
export function readModuleLiveState(scopes: string[]): Promise<ModuleLiveState> {
  const query = scopes.map(scope => `watch=${encodeURIComponent(scope)}`).join('&');
  return apiJson<ModuleLiveState>(`/curriculum/live/?${query}`, { timeoutMs: 10000 });
}

export interface ComponentUploadResult {
  uploaded: boolean;
  savedToComponent: boolean;
  componentId: string;
  moduleCatalogueId: string;
  file: {
    fileName: string;
    storedPath: string;
    url: string;
    size: number;
    contentType: string;
    componentType: string;
  };
}

export async function uploadComponentResource(input: { moduleCatalogueId: string; componentId: string; componentType: 'podcast' | 'powerpoint' | 'reading' | 'assignment'; file: File; topicResource?: boolean }) {
  assertComponentUploadAllowed(input.file);
  const form = new FormData();
  form.set('file', input.file);
  form.set('moduleCatalogueId', input.moduleCatalogueId);
  form.set('componentType', input.componentType);
  if (input.topicResource) form.set('topicResource', 'true');
  return uploadComponentFile<ComponentUploadResult>(`${API_BASE_URL}/curriculum/components/${encodeURIComponent(input.componentId)}/upload/`, form);
}

/**
 * The AI Material book a module carries: one uploaded file per module, stored
 * in the curriculum Azure container and served back through the same
 * `/curriculum_api/curriculum/uploads/...` route every component upload uses.
 */
export interface AiMaterialRecord {
  fileName: string;
  storedPath: string;
  url: string;
  size: number;
  contentType: string;
  uploadedAt: string;
}

export interface AiMaterialResult {
  moduleCatalogueId: string;
  hasMaterial: boolean;
  material: AiMaterialRecord | null;
  replaced?: boolean;
  uploaded?: boolean;
  removed?: boolean;
}

/** The book formats the upload accepts, kept in step with the backend's list. */
export const AI_MATERIAL_ACCEPT = '.pdf,.epub,.doc,.docx,.txt,.rtf,.odt';

const aiMaterialUrl = (moduleCatalogueId: string) =>
  `${API_BASE_URL}/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/ai-material/`;

export async function loadAiMaterial(moduleCatalogueId: string, signal?: AbortSignal) {
  const response = await fetch(aiMaterialUrl(moduleCatalogueId), { signal });
  if (!response.ok) {
    let message = `Curriculum API returned ${response.status} for the AI material`;
    try {
      const payload = await response.json();
      if (payload?.error) message = payload.error;
    } catch {
      // Keep the status message when a proxy returns a non-JSON body.
    }
    throw new Error(message);
  }
  return response.json() as Promise<AiMaterialResult>;
}

/**
 * Upload the book, or replace the one already there -- the same call either way.
 * The backend only deletes the file it replaced once the new one is stored and
 * recorded, so a failed replace leaves the existing book readable.
 */
export async function uploadAiMaterial(moduleCatalogueId: string, file: File) {
  assertComponentUploadAllowed(file);
  const form = new FormData();
  form.set('file', file);
  form.set('moduleCatalogueId', moduleCatalogueId);
  return uploadComponentFile<AiMaterialResult>(aiMaterialUrl(moduleCatalogueId), form);
}

export async function removeAiMaterial(moduleCatalogueId: string) {
  const response = await fetch(aiMaterialUrl(moduleCatalogueId), { method: 'DELETE' });
  if (!response.ok) throw new Error('The book could not be removed. Please retry.');
  return response.json() as Promise<AiMaterialResult>;
}

export interface TeamsMeetingInput {
  scheduleTimeZone?: 'Africa/Cairo' | 'Europe/London';
  seriesMode?: 'auto' | 'shared' | 'per_day';
  title: string;
  organizerEmail: string;
  attendees: string[];
  presenters?: string[];
  /** Given the Teams `coorganizer` role: they can start and manage the
   *  recording, admit people from the lobby and edit the meeting options. */
  coOrganizers?: string[];
  localStartDateTime: string;
  startDateTimeUtc: string;
  durationMinutes: number;
  repeat: 'none' | 'daily' | 'weekdays' | 'weekly';
  repeatOccurrences: number;
  lobbyBypass: string;
  recording: string;
  spokenLanguage: string;
  meetingType: string;
  details: string;
  requestResponses: boolean;
  allowNewTimeProposals: boolean;
  hideAttendees: boolean;
  transactionId: string;
  moduleDraftId?: string;
  moduleCatalogueId?: string;
  moduleTitle?: string;
  scheduledOccurrences?: Array<{
    sessionNumber: number;
    startDateTimeUtc: string;
    durationMinutes: number;
  }>;
}

export interface TeamsMeetingResult {
  created: boolean;
  meeting: {
    calendarSeries?: Array<{ day: string; eventId: string; joinUrl: string; onlineMeetingId?: string; sessionNumbers: number[] }>;
    liveSessionId: string;
    eventId: string;
    onlineMeetingId: string;
    joinUrl: string;
    webLink: string;
    meetingOptionsUrl: string;
    organizerEmail: string;
    attendees: string[];
    presenters: string[];
    coOrganizers: string[];
    startDateTimeUtc: string;
    durationMinutes: number;
    repeat: string;
    repeatOccurrences: number;
    trackedOccurrences: number;
    provider: string;
    trackingReady: boolean;
    settingsApplied: boolean;
  };
  warnings: string[];
  /**
   * The schedule emails the server sent as part of this create, once Microsoft
   * had verified the calendar and accepted its invitations. Absent when the
   * server did not send them (an older backend, or a recovered create).
   */
  scheduleEmail?: {
    total?: number;
    accepted?: number;
    queued?: number;
    failed?: number;
    uncertain?: number;
    status?: 'complete' | 'pending';
    error?: string;
    code?: string;
  };
}

export interface TeamsMeetingConfiguration {
  configured: boolean;
  defaultOrganizer: string;
  /** Legacy compatibility flag. Current backends return false because the
   * configured organizer is a default and users may choose another mailbox. */
  organizerLocked: boolean;
  timeZone: string;
  timeZoneIana: string;
}

// The calendar's own timezone. A session time typed into the wizard means this zone --
// the college's -- and not the zone of whoever happens to be filling the form in, so
// "09:00 Saturday" is the same instant whether the programme is set up from London or
// from Cairo. Viewers are unaffected either way: the event carries one absolute
// instant, and every calendar renders it in its own reader's local time.
let calendarTimeZone = 'Europe/London';

export function getCalendarTimeZone() {
  return calendarTimeZone;
}

function zoneOffsetMs(instant: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(instant).reduce<Record<string, number>>((acc, part) => {
    if (part.type !== 'literal') acc[part.type] = Number(part.value);
    return acc;
  }, {});
  const wallClock = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour % 24, parts.minute, parts.second);
  return wallClock - instant.getTime();
}

/**
 * A stored timestamp as an instant, reading an offset-less string as UTC.
 *
 * `new Date('2026-12-12T09:00:00')` is the *reader's own* local time by
 * specification, so an API value that omits its offset lands hours away for
 * everyone outside UTC -- the same calendar reads as in sync in London and two
 * hours out in Cairo. Every Teams instant is parsed here instead, so what the
 * page shows depends only on the reader's zone, never on where the string came
 * from.
 */
export function parseUtcInstant(value: unknown): Date {
  const raw = String(value ?? '').trim();
  if (!raw) return new Date(NaN);
  // `YYYY-MM-DDTHH:mm[:ss[.sss]]` with nothing after it: no zone was named.
  const offsetless = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(raw);
  return new Date(offsetless ? `${raw.replace(' ', 'T')}Z` : raw);
}

/** Read a naive `YYYY-MM-DDTHH:mm` wall clock as a time in `timeZone`, as UTC ISO. */
export function zonedNaiveToUtcIso(naiveLocal: string, timeZone = calendarTimeZone) {
  const [datePart, timePart = '00:00'] = (naiveLocal || '').split('T');
  const [year, month, day] = (datePart || '').split('-').map(Number);
  const [hour, minute] = timePart.split(':').map(Number);
  if ([year, month, day, hour, minute].some(value => !Number.isFinite(value))) {
    return new Date(naiveLocal).toISOString();
  }
  const wallClock = Date.UTC(year, month - 1, day, hour, minute);
  // Guess the instant as if the wall clock were UTC, then subtract the offset the zone
  // actually had there. A second pass settles the hour a DST jump adds or removes.
  let instant = wallClock;
  for (let pass = 0; pass < 2; pass += 1) {
    const corrected = wallClock - zoneOffsetMs(new Date(instant), timeZone);
    if (corrected === instant) break;
    instant = corrected;
  }
  return new Date(instant).toISOString();
}

/**
 * A UTC instant as the Microsoft calendar shows it, not as this reader's PC does.
 *
 * Every Teams date arrives as a UTC instant, and a reader sitting in another zone
 * would otherwise see a session that Teams itself lists an hour earlier. The zone
 * is the one the backend's Graph configuration reports, so it is only right once
 * `loadTeamsMeetingConfiguration` has run -- which every page showing these dates
 * does on mount.
 */
export function formatCalendarDateTime(value: unknown, timeZone = calendarTimeZone): string {
  const instant = parseUtcInstant(value);
  if (Number.isNaN(instant.getTime())) return '—';
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour12: false,
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).formatToParts(instant).reduce<Record<string, string>>((accumulator, part) => {
    if (part.type !== 'literal') accumulator[part.type] = part.value;
    return accumulator;
  }, {});
  if (!parts.year || !parts.day) return '—';
  return `${parts.day} ${parts.month} ${parts.year}, ${parts.hour === '24' ? '00' : parts.hour}:${parts.minute}`;
}

/**
 * A UTC instant split into the calendar-zone date and clock a tutor edits — the
 * inverse of `zonedNaiveToUtcIso`, so `zonedNaiveToUtcIso(`${date}T${time}`)`
 * round-trips back to the same instant. Empty parts when it cannot be parsed.
 */
export function utcIsoToCalendarParts(value: unknown, timeZone = calendarTimeZone): { date: string; time: string } {
  const instant = parseUtcInstant(value);
  if (Number.isNaN(instant.getTime())) return { date: '', time: '' };
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone, hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).formatToParts(instant).reduce<Record<string, string>>((accumulator, part) => {
    if (part.type !== 'literal') accumulator[part.type] = part.value;
    return accumulator;
  }, {});
  if (!parts.year || !parts.day) return { date: '', time: '' };
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour === '24' ? '00' : parts.hour}:${parts.minute}` };
}

function clockIn(instant: Date, timeZone: string) {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone, hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(instant);
}

function shortZoneName(timeZone: string) {
  return timeZone.split('/').pop()?.replace(/_/g, ' ') || timeZone;
}

/**
 * How a wizard session time reads in the calendar's zone and in the viewer's own.
 * `viewer` is omitted when both zones show the same clock, so the hint stays quiet
 * for the people it would tell nothing new.
 */
/**
 * The reader's own timezone, and how far it sits from the calendar's.
 *
 * Teams renders one instant in each viewer's own zone, so a page that prints the
 * calendar's clock has to say whose clock that is -- otherwise 09:00 here and
 * 11:00 in the reader's Teams look like two different meetings.
 */
export function viewerZoneOffset(instant: Date | null = null) {
  const viewerZone = Intl.DateTimeFormat().resolvedOptions().timeZone || calendarTimeZone;
  const at = instant && !Number.isNaN(instant.getTime()) ? instant : new Date();
  const differenceMinutes = Math.round(
    (zoneOffsetMs(at, viewerZone) - zoneOffsetMs(at, calendarTimeZone)) / 60000,
  );
  return {
    viewerZone,
    viewerZoneLabel: shortZoneName(viewerZone),
    calendarZoneLabel: shortZoneName(calendarTimeZone),
    /** Zero when both zones show the same clock, so callers can stay quiet. */
    differenceMinutes,
  };
}

export function describeSessionTime(naiveLocal: string) {
  const iso = zonedNaiveToUtcIso(naiveLocal);
  const instant = new Date(iso);
  if (Number.isNaN(instant.getTime())) return null;
  const viewerZone = Intl.DateTimeFormat().resolvedOptions().timeZone || calendarTimeZone;
  const calendarClock = clockIn(instant, calendarTimeZone);
  const viewerClock = clockIn(instant, viewerZone);
  return {
    iso,
    calendarClock,
    calendarZone: shortZoneName(calendarTimeZone),
    viewerClock: viewerClock === calendarClock ? '' : viewerClock,
    viewerZone: shortZoneName(viewerZone),
  };
}

export function loadTeamsMeetingConfiguration() {
  return apiJson<TeamsMeetingConfiguration>('/curriculum/teams-meetings/', { timeoutMs: 15000 })
    .then(configuration => {
      if (configuration.timeZoneIana) calendarTimeZone = configuration.timeZoneIana;
      return configuration;
    });
}

/**
 * Write a module's tracked Teams meeting back onto its live-session components.
 *
 * `createMissingComponents` extends that to the weeks that have no live-session
 * component at all: each one is given its own, on its own session date. It is
 * opt-in because the Module Builder also calls this silently when it opens a
 * module whose join link has gone missing, and a page load must not author
 * anything the person did not ask for.
 */
export function restoreModuleTeamsMeeting(moduleCatalogueId: string, options: { createMissingComponents?: boolean } = {}) {
  return apiJson<{ restored: boolean; updatedComponents: number; createdComponents?: number; meeting: Record<string, unknown>; module: ModuleCatalogueItem }>(`/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/teams-meetings/restore/`, {
    method: 'POST',
    body: JSON.stringify({ createMissingComponents: Boolean(options.createMissingComponents) }),
    timeoutMs: 30000,
  }).then(result => {
    // Re-attaching rewrites the module's live-session components, so the cached
    // module list and Teams views must not keep serving the pre-restore state.
    clearCurriculumGetCache();
    return { ...result, module: recalculateModule(result.module) };
  });
}

export interface SavedModuleTeamsMeeting {
  verificationPending: boolean;
  module: ModuleCatalogueItem;
  meeting: Record<string, unknown>;
  calendar: {
    title: string;
    seriesMode: 'shared' | 'per_day';
    occurrences: Array<{ sessionNumber: number; startDateTimeUtc: string; durationMinutes: number; joinUrl: string; eventId: string }>;
  };
}

/** Read this module's saved calendar without creating or sending invitations. */
export function readModuleTeamsMeeting(moduleCatalogueId: string) {
  return apiJson<SavedModuleTeamsMeeting>(
    `/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/teams-meetings/restore/`,
    // The same 30s the dialog gives its session plan, because the two are read
    // together and the slower of them decides how long the dialog waits.
    // Headroom only: this endpoint used to resolve its module id by summarising
    // every module in the database, so it was aborted before it answered every
    // time and reported "Calendar unavailable" for a calendar that was saved and
    // intact -- with the server still building a reply nobody was left to
    // receive. That resolution is now one indexed lookup; the timeout is here
    // for a slow day, not to paper over a stall.
    { timeoutMs: 30000 },
  );
}

/**
 * How many weeks re-attaching would give a live-session component to.
 *
 * A read-only dry run of the same walk `restoreModuleTeamsMeeting` performs, so
 * the answer can never drift from what pressing re-attach would actually do.
 * Zero means every week already has its session and the action would be a no-op.
 */
export function probeModuleTeamsAttachment(moduleCatalogueId: string) {
  return apiJson<{ pendingComponents?: number }>(
    `/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/teams-meetings/restore/?probe=1`,
    { timeoutMs: 30000 },
  ).then(result => Math.max(0, Number(result.pendingComponents) || 0));
}

export interface ModuleMeetingInvitees {
  moduleCatalogueId: string;
  presenters: string[];
  attendees: string[];
}

/**
 * Suggested Presenters/Attendees for a module's Teams meeting: the module's own
 * tutor email as presenter, and every learner whose training plan carries this
 * module as attendee. A starting point for the form, not a binding assignment —
 * the caller can still edit the lists freely before saving.
 */
export function fetchModuleMeetingInvitees(moduleCatalogueId: string) {
  return apiJson<ModuleMeetingInvitees>(`/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/meeting-invitees/`);
}

/**
 * Where a module's last Create stands on the server, and the calendar it saved.
 *
 * Read after a Create that timed out or was refused as in progress: the server
 * may well have finished, and asking is the only safe way to find out. Sending
 * Create again is not -- see `teams_create_guard.py`.
 */
export interface TeamsCreateStatus {
  state: 'none' | 'creating' | 'uncertain' | 'done';
  claim: { outcomeStatus: number | null; outcomeCode: string; liveSessionId: string; claimedAt: string; leaseUntil: string } | null;
  calendar: { liveSessionId: string; joinUrl: string; organizerEmail: string; warnings: string[]; settingsApplied: boolean } | null;
  /** The saved calendar's schedule-email counts; null when unknown or not started. */
  emails?: { total: number; accepted: number; queued: number; failed: number; uncertain: number } | null;
}

export function fetchTeamsCreateStatus(moduleCatalogueId: string) {
  return apiJson<TeamsCreateStatus>(
    `/curriculum/teams-meetings/create-status/?moduleCatalogueId=${encodeURIComponent(moduleCatalogueId)}`,
    { timeoutMs: 15000 },
  );
}

/**
 * A module's create form, saved without creating anything.
 *
 * Held on the server against the module, so whoever opens Create next starts
 * from it. Saving one never reaches Microsoft and sends no email; a successful
 * Create removes it. See `teams_create_drafts.py`.
 */
export interface TeamsCreateDraft {
  form: Record<string, string>;
  updatedAt: string;
  updatedByEmail: string;
  updatedByName: string;
}

const CREATE_DRAFT_PATH = '/curriculum/teams-meetings/create-draft/';

/** Which form the draft belongs to: the module calendar, or the additional week meeting. */
export type TeamsDraftKind = 'calendar' | 'week';

function createDraftQuery(moduleCatalogueId: string, kind: TeamsDraftKind) {
  return `${CREATE_DRAFT_PATH}?moduleCatalogueId=${encodeURIComponent(moduleCatalogueId)}&kind=${kind}`;
}

export function fetchTeamsCreateDraft(moduleCatalogueId: string, kind: TeamsDraftKind = 'calendar') {
  return apiJson<{ draft: TeamsCreateDraft | null; available: boolean }>(
    createDraftQuery(moduleCatalogueId, kind),
    { timeoutMs: 15000 },
  );
}

export function saveTeamsCreateDraft(moduleCatalogueId: string, form: Record<string, string>, kind: TeamsDraftKind = 'calendar') {
  return apiJson<{ draft: TeamsCreateDraft | null; available: boolean }>(CREATE_DRAFT_PATH, {
    method: 'PUT',
    body: JSON.stringify({ moduleCatalogueId, form, kind }),
    timeoutMs: 15000,
  });
}

export function deleteTeamsCreateDraft(moduleCatalogueId: string, kind: TeamsDraftKind = 'calendar') {
  return apiJson<{ draft: null; available: boolean }>(createDraftQuery(moduleCatalogueId, kind), {
    method: 'DELETE',
    timeoutMs: 15000,
  });
}

/**
 * One extra Teams meeting on a single week, with its own host and guests.
 *
 * A different record from the module's calendar, not a variation of it: its own
 * organiser, its own invitation list, one date, and a join link kept under the
 * component's `extraTeams*` settings. It never creates, updates, supersedes or
 * redirects the module's own meeting — see `backend/curriculum_api/teams_week_meeting.py`
 * for how the two are held apart on both sides.
 *
 * The backend refuses a week with no live-session component, and refuses a live
 * session that already holds a Teams link, so nothing here can replace a link
 * that exists.
 */
export interface WeekTeamsMeetingInput {
  weekId: string;
  componentId: string;
  title: string;
  organizerEmail: string;
  attendees: string[];
  presenters: string[];
  coOrganizers: string[];
  localStartDateTime: string;
  startDateTimeUtc: string;
  durationMinutes: number;
  lobbyBypass: string;
  recording: string;
  spokenLanguage: string;
  details: string;
  requestResponses: boolean;
  allowNewTimeProposals: boolean;
  transactionId: string;
  scheduleTimeZone?: 'Africa/Cairo' | 'Europe/London';
}

export interface WeekTeamsMeetingResult {
  created: boolean;
  /** Whether Microsoft confirmed the invitation write that mails everyone named. */
  invitationsSent: boolean;
  /** Every address Microsoft confirmed on the meeting — organiser's guests, in all roles. */
  invited: string[];
  /**
   * Our own schedule email, sent beside Microsoft's invitation — the same pair
   * the module calendar sends. Null when the caller may not send LMS mail.
   */
  scheduleEmail?: TeamsMeetingResult['scheduleEmail'] | null;
  meeting: {
    liveSessionId: string;
    weekId: string;
    componentId: string;
    eventId: string;
    onlineMeetingId: string;
    joinUrl: string;
    webLink: string;
    meetingOptionsUrl: string;
    organizerEmail: string;
    attendees: string[];
    presenters: string[];
    coOrganizers: string[];
    startDateTimeUtc: string;
    durationMinutes: number;
    subject: string;
    settingsApplied: boolean;
  };
  componentSettings: ComponentSettings;
  warnings: string[];
}

export async function createWeekTeamsMeeting(moduleCatalogueId: string, input: WeekTeamsMeetingInput) {
  return apiJson<WeekTeamsMeetingResult>(
    `/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/week-teams-meetings/`,
    { method: 'POST', body: JSON.stringify(input), timeoutMs: 45000 },
  ).then(result => {
    // Same reason as the module create below: this POST goes through this
    // module's own client, so nothing else invalidates the curriculum GET cache.
    clearCurriculumGetCache();
    return result;
  });
}

/**
 * Change an additional week meeting, or cancel it.
 *
 * Its own endpoint, never `updateTeamsMeetingSchedule`: that one ends by
 * re-attaching its series across every live-session component of the module,
 * which would put one week's private link on all of them. The server refuses a
 * week meeting sent to it for the same reason.
 *
 * `notifyAttendees` decides only whether people ALREADY invited are told.
 * Anyone this save adds is always reached, because Microsoft puts a meeting on
 * someone's calendar only when something is sent to them.
 */
export type WeekTeamsMeetingEdit =
  Omit<WeekTeamsMeetingInput, 'weekId' | 'componentId' | 'organizerEmail' | 'transactionId'>
  & { notifyAttendees: boolean };

export interface WeekTeamsMeetingUpdateResult {
  updated: boolean;
  notifiedExisting: boolean;
  /** Everyone this save added, who were sent the meeting individually. */
  forwardedTo: string[];
  meeting: WeekTeamsMeetingResult['meeting'];
  componentSettings: ComponentSettings;
  warnings: string[];
  /** The LMS change email to everyone already invited, sent by the server when the time moved. */
  scheduleEmail?: WeekTeamsMeetingResult['scheduleEmail'] | null;
  /** The LMS schedule email to the people this save added, sent by the server. */
  addedEmail?: WeekTeamsMeetingResult['scheduleEmail'] | null;
}

export async function updateWeekTeamsMeeting(
  moduleCatalogueId: string,
  liveSessionId: string,
  input: WeekTeamsMeetingEdit,
) {
  return apiJson<WeekTeamsMeetingUpdateResult>(
    `/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/week-teams-meetings/${encodeURIComponent(liveSessionId)}/`,
    { method: 'PATCH', body: JSON.stringify(input), timeoutMs: 45000 },
  ).then(result => {
    clearCurriculumGetCache();
    return result;
  });
}

export async function cancelWeekTeamsMeeting(moduleCatalogueId: string, liveSessionId: string) {
  return apiJson<{ cancelled: boolean; componentId: string; warnings: string[]; scheduleEmail?: WeekTeamsMeetingResult['scheduleEmail'] | null }>(
    `/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/week-teams-meetings/${encodeURIComponent(liveSessionId)}/`,
    { method: 'DELETE', timeoutMs: 45000 },
  ).then(result => {
    clearCurriculumGetCache();
    return result;
  });
}

/**
 * Choose which calendar delivers one live session: the module's own series or an
 * additional meeting. Inherits the main link and matches imported occurrences;
 * nothing is sent to Microsoft.
 * The server refuses a live session either calendar has already booked.
 */
export async function setLiveSessionMeetingScope(
  moduleCatalogueId: string, componentId: string, scope: 'main' | 'additional',
) {
  return apiJson<{ componentId: string; scope: 'main' | 'additional'; calendarStatus?: string; message?: string }>(
    `/curriculum/modules/${encodeURIComponent(moduleCatalogueId)}/live-session-meeting-scope/`,
    { method: 'POST', body: JSON.stringify({ componentId, scope }) },
  ).then(result => {
    clearCurriculumGetCache();
    return result;
  });
}

export async function createTeamsMeeting(
  input: TeamsMeetingInput,
  options: { confirmUncertain?: boolean; onSubmitted?: () => void } = {},
) {
  // Review and send the same snapshot, even if a background refresh changes the form.
  const reviewed: TeamsMeetingInput = JSON.parse(JSON.stringify({ ...input, hideAttendees: true }));
  await reviewCalendar({ ...reviewed, summaryEmail: true }, reviewed.scheduleTimeZone || getCalendarTimeZone());
  // The author has confirmed the review: from here the create is under way,
  // and the caller can start showing its progress.
  options.onSubmitted?.();
  // `confirmUncertain` only ever comes from a person who was told an earlier
  // Create did not report back and chose to create again; never from a retry.
  return apiJson<TeamsMeetingResult>(`/curriculum/teams-meetings/${options.confirmUncertain ? '?confirmUncertain=1' : ''}`, {
    method: 'POST',
    body: JSON.stringify(reviewed),
    timeoutMs: 45000,
  }).then(result => {
    // This POST goes through this module's own client, so it never triggers the
    // curriculum GET cache's mutation invalidation. Clear it here so the module
    // list, the module-workspace Teams tab and the Teams Meetings page all read
    // the new meeting the next time they load, instead of a pre-create snapshot.
    clearCurriculumGetCache();
    return result;
  });
}

/**
 * Who a save puts on the meeting that the saved calendar does not already have.
 *
 * Read against the stored roster rather than the form's starting values, and
 * case-insensitively across every role, so moving somebody from attendee to
 * presenter is not "someone new". The organizer counts as already invited:
 * they own the event.
 */
function peopleAddedBySave(
  invitations: { attendees?: string[]; presenters?: string[]; coOrganizers?: string[] },
  series: { organizer_email?: string; attendees: string[]; presenters: string[]; co_organizers: string[] },
) {
  const keys = (...lists: Array<string[] | undefined>) => lists.flatMap(list => (list || [])
    .map(value => String(value || '').trim().toLowerCase()).filter(Boolean));
  const invited = new Set(keys([series.organizer_email || ''], series.attendees, series.presenters, series.co_organizers));
  return [...new Set(keys(invitations.attendees, invitations.presenters, invitations.coOrganizers))]
    .filter(email => !invited.has(email));
}

/**
 * Send a module's own session dates to its Teams series. `attendees`/`presenters`/
 * `coOrganizers` are optional: omit them to move dates only, pass them to correct
 * who is invited, who presents and who co-runs it without recreating the meeting.
 */
export async function updateTeamsMeetingSchedule(liveSessionId: string, input: Pick<TeamsMeetingInput, 'title' | 'organizerEmail' | 'localStartDateTime' | 'startDateTimeUtc' | 'durationMinutes' | 'repeat' | 'repeatOccurrences' | 'scheduledOccurrences'> & Partial<Pick<TeamsMeetingInput, 'lobbyBypass' | 'recording' | 'spokenLanguage' | 'seriesMode'>> & { eventId?: string; attendees?: string[]; presenters?: string[]; coOrganizers?: string[]; peopleOnly?: boolean; settingsOnly?: boolean; notifyAttendees?: boolean; invitationsOnly?: boolean }, options: { onSubmitted?: () => void } = {}) {
  // `settingsOnly` only labels the review ("meeting settings" rather than
  // "invitations"); the transport is the same people-only update either way.
  const { settingsOnly, ...sent } = input;
  const reviewed = JSON.parse(JSON.stringify(sent)) as typeof sent;
  const { series: rawSeries, occurrences } = await loadTeamsMeetingArtifacts(liveSessionId);
  const series = calendarSeriesForReview(rawSeries);
  if (reviewed.peopleOnly) {
    const held = occurrences.filter(item => item.status !== 'cancelled' && item.status !== 'superseded')
      .sort((a, b) => parseUtcInstant(a.scheduled_start).getTime() - parseUtcInstant(b.scheduled_start).getTime());
    if (!held.length) throw new Error('The saved session dates could not be loaded. Review the calendar dates before changing invitations.');
    reviewed.scheduledOccurrences = held.map(item => ({ sessionNumber: item.session_number,
      startDateTimeUtc: parseUtcInstant(item.scheduled_start).toISOString(),
      durationMinutes: (parseUtcInstant(item.scheduled_end).getTime() - parseUtcInstant(item.scheduled_start).getTime()) / 60000,
    }));
    reviewed.startDateTimeUtc = reviewed.scheduledOccurrences[0].startDateTimeUtc;
    reviewed.durationMinutes = reviewed.scheduledOccurrences[0].durationMinutes;
  }
  // What this save announces, decided once here and read by the review, by the
  // write and by the result. A save that moves the calendar is announced to
  // everyone already invited and carries the LMS change email. A save that only
  // changes who is invited or how the meeting runs announces nothing: the
  // people it adds are invited and emailed on their own, and nobody already on
  // the meeting hears about it. The explicit choice travels with the PATCH so the
  // review, calendar write and result dialog cannot disagree.
  // A date update defaults to the historic behaviour (announce it), while the
  // Teams Meetings workspace can explicitly turn that announcement off. A
  // people/settings-only save is always quiet for everyone already invited;
  // newly added people are handled separately after the calendar is verified.
  const notifyAttendees = reviewed.peopleOnly ? false : reviewed.notifyAttendees !== false;
  const invitations = {
    attendees: reviewed.attendees ?? series.attendees, presenters: reviewed.presenters ?? series.presenters,
    coOrganizers: reviewed.coOrganizers ?? series.co_organizers,
  };
  // The review exists to be the last look before something leaves: a calendar
  // that moves tells everyone already invited, and a person added is forwarded
  // the meeting and sent the schedule email. A people-only save that adds
  // nobody -- taking someone off, moving them between roles, changing the
  // recording or the lobby -- sends no mail at all, to anybody, so a full
  // session-by-session review and a "Save and send" button confirm an act with
  // no outward effect. That save applies on the press instead.
  // "Save invitations" sends nothing to anybody, so there is nothing to review.
  if (!reviewed.invitationsOnly && (!reviewed.peopleOnly || peopleAddedBySave(invitations, series).length)) {
    await reviewCalendar({ ...reviewed, settingsOnly, organizerEmail: series.organizer_email, joinUrl: series.join_url,
      notifyOnUpdate: notifyAttendees, ...invitations,
      recording: reviewed.recording ?? series.recording, lobbyBypass: reviewed.lobbyBypass ?? series.lobby_bypass, spokenLanguage: reviewed.spokenLanguage ?? series.spoken_language,
      calendarSeries: series.calendar_series, previousOccurrences: occurrences,
      seriesMode: series.calendar_series?.length ? 'per_day' : 'shared',
    }, series.timeZoneIana || getCalendarTimeZone());
  }
  // The review is complete. The caller can now replace its form with a
  // progress panel without showing it behind the confirmation dialog.
  options.onSubmitted?.();
  return apiJson<{ updated: boolean; meeting: TeamsMeetingResult['meeting']; warnings?: Array<{ code?: string; message: string; detail?: string }>; changeNotice?: string; leftoverSlots?: TeamsLeftoverSlot[]; scheduleEmail?: TeamsMeetingResult['scheduleEmail']; addedPeople?: string[]; addedEmail?: TeamsMeetingResult['scheduleEmail']; addedEmailKey?: string; announced?: boolean; microsoftUpdate?: TeamsMicrosoftUpdate }>(`/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/schedule/`, {
    method: 'PATCH',
    body: JSON.stringify({ ...reviewed, notifyAttendees }),
    // One update is around ten SERIAL Microsoft Graph round trips -- read the
    // event, patch it, re-read it, list its instances, verify them, publish the
    // attendee list, confirm it, verify again -- and every one of them is a call
    // to Microsoft over the network. A series of eight sessions routinely runs
    // past 45s on that path, and the browser abandoning it there did not stop
    // the server: it left the author staring at a timeout for an update that
    // was still being applied. The budget is the transport's, not Microsoft's.
    // Four minutes: a slow Graph day pushed long series past two.
    timeoutMs: 240000,
  }).then(result => {
    clearCurriculumGetCache();
    // The server has the last word on whether there was a date change to tell
    // anyone about. A retry of an update that had already landed, or a save
    // whose dates did not really move, comes back `announced: false`: nobody
    // is sent a change email for it, from the server or from here.
    return { ...result, notifyAttendees: result.announced === false ? false : notifyAttendees };
  });
}

/**
 * What Microsoft was asked to announce for one update. A request, never proof
 * of delivery: Graph cannot say whether an invitee's mailbox received it.
 *
 * - `sent`: the date change was announced to everyone invited.
 * - `already_attempted`: an earlier attempt at this same change (one whose
 *   answer was lost to a timeout) already asked; it was not asked again.
 * - `silent`: the dates changed without a notice, by the caller's choice.
 * - `not_needed`: no date changed, so there was nothing to announce.
 */
export type TeamsMicrosoftUpdate = 'sent' | 'already_attempted' | 'silent' | 'not_needed';

export interface TeamsArtifactSyncResult {
  synced: {
    attendanceReports: number;
    attendanceRecords: number;
    transcripts: number;
    recordings: number;
  };
  errors: string[];
  partial: boolean;
}

export interface TeamsAttendanceRecord {
  id: string;
  email: string;
  display_name: string;
  role: string;
  total_attendance_seconds: number;
  /** Graph reports a row per invitee; `false` means invited but never joined.
   *  Absent on rows written before attendance tracking, which count as present. */
  attended?: boolean;
  intervals?: Array<{
    joinDateTime?: string;
    leaveDateTime?: string;
  }> | string;
}

export interface TeamsMeetingArtifact {
  id: string;
  artifact_type: 'transcript' | 'recording' | string;
  content_url?: string;
  created_datetime?: string;
  end_datetime?: string;
}

export interface TeamsMeetingOccurrence {
  id: string;
  session_number: number;
  scheduled_start: string;
  scheduled_end: string;
  /** Set only for a session that runs on a meeting of its own; else the series' own link. */
  join_url?: string;
  online_meeting_id?: string;
  actual_start?: string;
  actual_end?: string;
  participant_count: number;
  attendance_report_id?: string;
  status: string;
  attendance: TeamsAttendanceRecord[];
  artifacts: TeamsMeetingArtifact[];
  /** Calls on this link that were not this session, ignored by the attendance sync. */
  off_schedule_runs?: OffScheduleRun[];
}

export interface TeamsMeetingArtifactsResult {
  series: {
    meeting_type?: string;
    timezone?: string;
    timeZoneIana?: string;
    id: string;
    module_title: string;
    organizer_email: string;
    join_url: string;
    online_meeting_id: string;
    attendees?: string[];
    presenters?: string[];
    co_organizers?: string[];
    recording?: string;
    lobby_bypass?: string;
    spoken_language?: string;
    calendar_series?: Array<{ day: string; joinUrl: string; sessionNumbers: number[] }>;
  };
  occurrences: TeamsMeetingOccurrence[];
  /** Slots on Teams that are not module sessions, as the last status check found them. */
  leftoverSlots?: TeamsLeftoverSlot[];
}

/**
 * A slot Microsoft holds that the module plan does not: weekly filler in a gap,
 * a week handed to its own additional meeting, a dropped session, or a weekday
 * series the plan stopped using. No save, sync or background job removes one;
 * only its own Cancel does, because Microsoft emails everyone invited.
 */
export interface TeamsLeftoverSlot {
  kind?: 'occurrence' | 'event' | 'series';
  eventId: string;
  rootId?: string;
  startDateTimeUtc?: string;
  endDateTimeUtc?: string;
  day?: string;
  reason?: string;
}

export function syncTeamsMeetingArtifacts(liveSessionId: string): Promise<TeamsArtifactSyncResult | { state: 'queued'; message: string }> {
  return requestSessionSync(liveSessionId);
}

/** The calls on a calendar's link that were not one of its sessions, by occurrence id. Read-only. */
export interface TeamsOffScheduleRunsResult {
  liveSessionId: string;
  timeZoneIana?: string;
  occurrences: Record<string, OffScheduleRun[]>;
}

export function loadTeamsOffScheduleRuns(liveSessionId: string) {
  return apiJson<TeamsOffScheduleRunsResult>(`/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/off-schedule-runs/`, {
    timeoutMs: 30000,
  });
}

export function loadTeamsMeetingArtifacts(liveSessionId: string) {
  return apiJson<TeamsMeetingArtifactsResult>(`/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/artifacts/`, {
    // Read first by every Update Teams calendar, so it shares that save's patience.
    timeoutMs: 60000,
  });
}

function calendarSeriesForReview(series: TeamsMeetingArtifactsResult['series']) {
  // Raw SQL JSON columns may arrive serialized, depending on the DB driver.
  const list = <T,>(value: unknown): T[] => {
    let decoded = value;
    try { if (typeof value === 'string') decoded = JSON.parse(value); } catch { decoded = undefined; }
    if (decoded === null) return [];
    if (!Array.isArray(decoded)) throw new Error('The saved invitation details could not be loaded. Reload the calendar before saving.');
    return decoded as T[];
  };
  return { ...series, attendees: list<string>(series.attendees), presenters: list<string>(series.presenters),
    co_organizers: list<string>(series.co_organizers),
    calendar_series: list<NonNullable<typeof series.calendar_series>[number]>(series.calendar_series ?? []),
  };
}

export function teamsMeetingArtifactContentUrl(liveSessionId: string, artifactId: string) {
  return `${API_BASE_URL}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/artifacts/${encodeURIComponent(artifactId)}/content/`;
}

export function teamsMeetingArtifactPreviewUrl(liveSessionId: string, artifactId: string) {
  return `${teamsMeetingArtifactContentUrl(liveSessionId, artifactId)}?preview=1`;
}

export function teamsMeetingRecordingEventsUrl(liveSessionId: string, artifactId: string) {
  return `${API_BASE_URL}/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/artifacts/${encodeURIComponent(artifactId)}/recording-events/`;
}

/** One thing a viewer did to a recording: started it, paused, skipped, finished. */
export interface TeamsRecordingEventInput {
  type: 'play' | 'pause' | 'seeked' | 'ended' | 'heartbeat' | 'open' | 'close';
  videoTimeSeconds: number;
  previousVideoTimeSeconds?: number;
  skipFromSeconds?: number;
  skipToSeconds?: number;
  watchedSecondsDelta?: number;
  durationSeconds?: number;
  playbackRate?: number;
  eventTime?: string;
}

/**
 * Record how a recording was watched. Whoever reviews a session in the LMS is
 * doing so instead of attending it, so who watched what — and what they skipped
 * — is part of the session's record rather than a private matter of the player.
 */
export function saveTeamsRecordingEvents(
  liveSessionId: string,
  artifactId: string,
  payload: {
    previewSessionId?: string;
    viewer?: { id?: string; email?: string; name?: string; role?: string };
    browser?: { sessionId?: string; viewportWidth?: number; viewportHeight?: number; userAgent?: string; pageUrl?: string };
    events: TeamsRecordingEventInput[];
  },
) {
  return apiJson<{ saved: number; previewSessionId: string }>(
    `/curriculum/teams-meetings/${encodeURIComponent(liveSessionId)}/artifacts/${encodeURIComponent(artifactId)}/recording-events/`,
    { method: 'POST', body: JSON.stringify(payload), timeoutMs: 20000 },
  );
}

export class ApiError extends Error {
  status: number;
  detail?: string;
  /**
   * The handler's own JSON body, when it sent one.
   *
   * `message` is flattened for display and loses everything structured with
   * it. A refusal a caller has to ACT on rather than only show -- a 409 from
   * the structure PATCH carries the current revision and the current module --
   * needs the body itself.
   */
  data?: Record<string, unknown>;

  constructor(status: number, message: string, detail?: string, data?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.data = data;
  }
}

async function apiJson<T>(path: string, init?: { method?: string; body?: string; headers?: Record<string, string>; timeoutMs?: number }): Promise<T> {
  const controller = init?.timeoutMs ? new AbortController() : undefined;
  const timeout = controller && init?.timeoutMs
    ? window.setTimeout(() => controller.abort(), init.timeoutMs)
    : undefined;
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller?.signal,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers || {}),
      },
    });
    if (!response.ok) {
      let message = `Curriculum API returned ${response.status} for ${path}`;
      let body: Record<string, unknown> | undefined;
      try {
        const payload = await response.json();
        if (payload && typeof payload === 'object') body = payload as Record<string, unknown>;
        const validation = Array.isArray(payload?.validationErrors)
          ? payload.validationErrors.map((item: { message?: string }) => item.message).filter(Boolean).join('; ')
          : '';
        const detail = typeof payload?.detail === 'string' ? payload.detail : '';
        if (validation) message = validation;
        else if (payload?.error) message = detail ? `${payload.error} ${detail}` : payload.error;
      } catch {
        // Ignore body parsing failures so the original status remains visible.
      }
      throw new ApiError(response.status, message, undefined, body);
    }
    return response.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new CurriculumRequestTimeout();
    }
    throw err;
  } finally {
    if (timeout) window.clearTimeout(timeout);
  }
}

export class CurriculumRequestTimeout extends Error {
  constructor() {
    super('The response timed out. The server may still be processing this request. Check its saved status before retrying.');
  }
}

function componentAdvancedDefaults(type: ModuleComponentType): Record<string, string | number | boolean | string[]> {
  const completionRules: Partial<Record<ModuleComponentType, string>> = {
    'live-session': 'Attend or watch recording',
    'recording-placeholder': 'Mark complete after watching',
    video: 'Watch video and mark complete',
    podcast: 'Listen and mark complete',
    reading: 'Read the material and confirm completion',
    powerpoint: 'Review slide deck',
    quiz: 'Submit',
    'monthly-ksb-quiz': 'Submit monthly KSB quiz',
    reflection: 'Submit reflection',
    'workplace-evidence': 'Upload + describe',
    assignment: 'Submit assignment',
    checkpoint: 'Complete checkpoint',
    'coaching-preparation': 'Complete coaching preparation',
  };
  const evidenceRequired: Partial<Record<ModuleComponentType, string>> = {
    'live-session': 'Attendance or recording completion',
    quiz: 'Quiz result',
    'monthly-ksb-quiz': 'Quiz result',
    checkpoint: 'Quiz result',
    reflection: 'Reflection + signature',
    'workplace-evidence': 'File + 100-word description',
    assignment: 'Submission file',
    'coaching-preparation': 'Preparation notes',
  };
  const reflectionPrompt =
    type === 'workplace-evidence'
      ? 'What workplace evidence have you uploaded, and which KSBs does it demonstrate?'
      : ['quiz', 'monthly-ksb-quiz', 'checkpoint'].includes(type)
        ? 'Which questions or topics do you need to revisit after this activity?'
        : 'What did you learn? How will you apply this at work? Which KSBs did this develop?';

  return {
    completionRule: completionRules[type] || 'Mark complete',
    evidenceRequired: evidenceRequired[type] || '-',
    reflectionPrompt,
    contentStatus: 'Draft',
    version: '0.1',
  };
}

// Retained from the pre-registry authoring implementation for reference and
// backwards-compatible migration work. Runtime defaults come from
// componentAuthoringModel.getDefaultComponentSettings above.
export function getLegacyDefaultComponentSettings(type: ModuleComponentType): Record<string, string | number | boolean | string[]> {
  switch (type) {
    case 'live-session':
      return { ...componentAdvancedDefaults(type), sessionPurpose: '', preparationInstructions: '', reflectionQuestions: '', attendanceRequired: true, recordingExpected: true };
    case 'recording-placeholder':
      return { ...componentAdvancedDefaults(type), recordingPurpose: '', source: 'MIS allocation', expectedAvailability: 'After live session' };
    case 'video':
      return { ...componentAdvancedDefaults(type), provider: 'YouTube', videoUrl: '', durationMinutes: 10, learningBrief: '', postWatchTask: '' };
    case 'podcast':
      return { ...componentAdvancedDefaults(type), podcastSource: 'External URL', podcastUrl: '', durationMinutes: 20, listeningFocus: '', podcastReflectionQuestion: '' };
    case 'reading':
      return {
        ...componentAdvancedDefaults(type),
        difficulty: 'Standard',
        requirement: 'Required',
        readingSource: 'Written in LMS',
        resourceUrl: '',
        uploadedFileName: '',
        uploadedFileUrl: '',
        uploadedFileSize: 0,
        uploadedFileContentType: '',
        uploadSource: '',
        readingContent: '',
        mainLearningOutcomes: '',
        ksbEvidenceNotes: '',
        focusSections: '',
        learnerInstruction: '',
        keyPointCount: '0',
        keyPoints: '',
        glossaryTerms: '',
        estimatedReadingTime: 20,
        otjhRationale: '',
        audioEnabled: false,
        audioUrl: '',
        reflectionQuestionCount: '0 qs',
        readingReflectionPrompts: '',
        readingEvidenceRequired: '',
        completionRuleCount: '3 rules',
        completionConfirmationRequired: true,
        linkedActivity: '',
        coachingPrompt: '',
        requiredReading: true,
      };
    case 'powerpoint':
      return { ...componentAdvancedDefaults(type), fileName: '', slideRange: '', speakerNotes: '', downloadAllowed: true };
    case 'quiz':
      return { ...componentAdvancedDefaults(type), buildMode: 'manual', numberOfQuestions: 10, passMarkPercentage: 70, attemptsAllowed: 2, affectsKsbProgression: true, questionsPlaceholder: '', completionFeedback: '' };
    case 'monthly-ksb-quiz':
      return { ...componentAdvancedDefaults(type), buildMode: 'manual', numberOfQuestions: 12, passMarkPercentage: 70, attemptsAllowed: 2, affectsKsbProgression: true, monthFocus: '' };
    case 'reflection':
      return { ...componentAdvancedDefaults(type), minimumWordCount: 250, learnerGuidance: '', tutorReviewGuidance: '' };
    case 'workplace-evidence':
      return { ...componentAdvancedDefaults(type), evidenceInstructions: '', acceptedEvidenceTypes: 'Document, image, video, witness statement', assessmentChecklist: '', minimumDescriptionWords: 100 };
    case 'assignment':
      return { ...componentAdvancedDefaults(type), assignmentBrief: '', submissionInstructions: '', dueTiming: 'End of week', markingRubric: '' };
    case 'checkpoint':
      return { ...componentAdvancedDefaults(type), checkpointTitle: '', checkpointQuestions: '', progressReviewLinked: true, monthlyCoachingReviewLinked: true };
    case 'coaching-preparation':
      return { ...componentAdvancedDefaults(type), preparationPrompt: '', evidenceToBring: '', coachDiscussionPoints: '', coachingReviewLinked: true };
    default:
      return componentAdvancedDefaults(type);
  }
}

function readStore<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) as T : fallback;
  } catch {
    return fallback;
  }
}

function cleanUserFacingText(value: string) {
  return String(value || '')
    .split(/\r?\n/)
    .map(line => line.replace(/(^|\s)__[a-zA-Z0-9_]+:[\s\S]*?(?=\s__[a-zA-Z0-9_]+:|$)/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
    .trim();
}
