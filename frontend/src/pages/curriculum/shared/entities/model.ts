// ============================================================================
// Shared derivation rules for the entity-based Curriculum pages.
//
// The canonical hierarchy is Programme -> Cohort -> Group -> Module -> Week ->
// Component, and nothing here changes it. In particular a Module has no direct
// Cohort relationship: its cohort and programme are *derived through its Group*.
// The denormalised `cohortId` / `programmeId` fields the API returns on a module
// are a read cache the backend maintains, so they are used only as a fallback
// for a module that is not attached to a group yet.
//
// Both the global pages (Curriculum -> Cohorts) and the contextual ones
// (Programme -> its Cohorts) call these helpers against the same fetched
// entities, so the two views cannot drift into separate sources of truth.
// ============================================================================

import type {
  CurriculumCohort,
  CurriculumGroup,
  CurriculumModule,
  CurriculumProgramme,
} from '@/lib/curriculumApi';

export function normaliseKey(value: unknown): string {
  return String(value ?? '').trim().toLowerCase();
}

export function cleanText(value: unknown, fallback = ''): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

/**
 * Resolve the one record an identifier names, spending every id before any name.
 *
 * Names are not unique in this data -- the same module title is authored in two
 * programmes, two cohorts are both called "October 2026" -- so a lookup that
 * tests `id === key || name === key` inside a single `find` returns whichever
 * record happens to sit earlier in the list, and list order is a sort concern
 * (the module catalogue sorts drafts first), not an identity one. That is how a
 * programme row reading 240h30m opened a namesake module totalling 24h32m.
 *
 * An id pass over the whole collection therefore has to fail completely before
 * the name pass starts. Matching by name at all stays deliberate: ids disagree
 * across the delivery and authoring sides, and older records reference their
 * parent by name, so the fallback is what keeps those resolvable.
 */
export function findByIdentifierThenName<T>(
  items: T[],
  identifier: unknown,
  identityValues: (item: T) => unknown[],
  nameValues: (item: T) => unknown[],
  normalise: (value: unknown) => string = normaliseKey,
): T | undefined {
  const key = normalise(identifier);
  if (!key) return undefined;
  const matches = (values: unknown[]) => values.map(normalise).filter(Boolean).includes(key);
  return items.find(item => matches(identityValues(item)))
    || items.find(item => matches(nameValues(item)));
}

/**
 * Link to an entity workspace with its readable name alongside the canonical id.
 * The id remains the API identity; the name lets the destination render useful
 * headings and breadcrumbs immediately while the entity collection is loading.
 */
export function namedCurriculumWorkspacePath(kind: 'groups' | 'modules', identifier: unknown, name: unknown): string {
  const id = cleanText(identifier);
  if (!id) return `/curriculum/${kind}`;
  const params = new URLSearchParams();
  const label = cleanText(name);
  if (label) params.set(kind === 'groups' ? 'groupName' : 'moduleName', label);
  const query = params.toString();
  return `/curriculum/${kind}/${encodeURIComponent(id)}${query ? `?${query}` : ''}`;
}

/** The flag a workspace reads to decide whether to open its own edit drawer. */
export const WORKSPACE_EDIT_PARAM = 'edit';

/**
 * The same workspace path, asking that record's edit drawer to open on arrival.
 *
 * A reader who clicks a cohort or group name somewhere else in the curriculum
 * usually means "change this one", so the link lands on the record's own home
 * with the form already up rather than leaving them to find Edit again. The
 * drawer stays the one the workspace already owns: this only presses its button.
 */
export function workspaceEditPath(path: string): string {
  const target = cleanText(path);
  if (!target) return target;
  return `${target}${target.includes('?') ? '&' : '?'}${WORKSPACE_EDIT_PARAM}=1`;
}

/** Whether the current location asked this workspace to open its edit drawer. */
export function wantsWorkspaceEdit(params: URLSearchParams): boolean {
  return params.get(WORKSPACE_EDIT_PARAM) === '1';
}

/**
 * A record's notes with the API's bookkeeping lines removed.
 *
 * Every module the API returns carries its parent chain appended to `notes` as
 * hidden `__key:value` lines (`__program_id`, `__cohort_id`, `__group_id`,
 * `__group_name`, `__module_catalogue_id`, `__module_color`). They are derived
 * from real columns on every read, so they are never worth showing to a reader
 * and never worth sending back: a form that echoes them saves them into the
 * description, and the next read appends its own copy on top.
 */
export function visibleNotes(value: unknown, fallback = ''): string {
  const text = String(value ?? '')
    .split(/\r?\n/)
    .filter(line => !line.trim().startsWith('__'))
    .join('\n')
    .trim();
  return text || fallback;
}

/** Two identifiers refer to the same record. Empty never matches empty. */
export function sameIdentifier(left: unknown, right: unknown): boolean {
  const a = normaliseKey(left);
  const b = normaliseKey(right);
  return Boolean(a) && a === b;
}

/**
 * Put the record a save just wrote into a collection the page is already
 * showing, matched on id: merged over the existing row when it is an edit,
 * appended when it is new.
 *
 * The pages use this to paint a save before the background refresh returns,
 * which on a remote database is several seconds later. Only ever call it with
 * what an endpoint gave back — the point is to be early, not to invent a row.
 */
export function upsertById<T extends { id: string }>(rows: T[], saved: T): T[] {
  return rows.some(row => sameIdentifier(row.id, saved.id))
    ? rows.map(row => (sameIdentifier(row.id, saved.id) ? { ...row, ...saved } : row))
    : [...rows, saved];
}

/** The other half of `upsertById`: drop a record an archive has just hidden. */
export function removeById<T extends { id: string }>(rows: T[], id: string): T[] {
  return rows.filter(row => !sameIdentifier(row.id, id));
}

/** Any of `values` matches any of `candidates`. */
export function matchesAny(values: unknown[], candidates: unknown[]): boolean {
  const wanted = new Set(candidates.map(normaliseKey).filter(Boolean));
  if (!wanted.size) return false;
  return values.some(value => wanted.has(normaliseKey(value)));
}

export function programmeKeys(programme: Pick<CurriculumProgramme, 'id' | 'sourceId' | 'name'>): string[] {
  return [programme.sourceId, programme.id, programme.name].map(normaliseKey).filter(Boolean);
}

export function programmeIdentity(programme: Pick<CurriculumProgramme, 'id' | 'sourceId'>): string {
  return cleanText(programme.sourceId) || cleanText(programme.id);
}

export function moduleIdentity(module: CurriculumModule): string {
  // moduleCatalogueId is the canonical authoring identity; the others are
  // legacy/delivery aliases kept for records that predate it.
  return (
    cleanText(module.moduleCatalogueId)
    || cleanText(module.catalogueId)
    || cleanText(module.moduleId)
    || cleanText(module.id)
  );
}

export interface EntityIndex {
  programmes: CurriculumProgramme[];
  cohorts: CurriculumCohort[];
  groups: CurriculumGroup[];
  modules: CurriculumModule[];
}

export interface ResolvedContext {
  programme?: CurriculumProgramme;
  cohort?: CurriculumCohort;
  group?: CurriculumGroup;
  programmeId: string;
  programmeName: string;
  cohortId: string;
  cohortName: string;
  groupId: string;
  groupName: string;
  /**
   * False when the parent chain had to fall back to the module's own cached
   * names because it is not attached to a stored group — worth surfacing, since
   * such a module is invisible to the contextual Programme/Cohort views.
   */
  linked: boolean;
}

export function findProgramme(
  programmes: CurriculumProgramme[],
  identifier: unknown,
): CurriculumProgramme | undefined {
  return findByIdentifierThenName(
    programmes,
    identifier,
    programme => [programme.sourceId, programme.id],
    programme => [programme.name],
  );
}

/**
 * The value a programme `<select>` can actually show for `identifier`.
 *
 * A programme option's value is `programmeIdentity` -- the id as the programmes
 * collection spells it -- and a `<select>` matches its value by exact string.
 * Stored records name their programme in whatever shape wrote them: the cohort
 * endpoint upper-cases the id it resolved ("PROG-Mon" -> "PROG-MON"), and older
 * rows carry the programme *name* instead of an id. Either one refers to the
 * right programme and neither equals the option value, so the field came up
 * reading "Select a programme" over a parent that was demonstrably set -- and a
 * save from there had to have it picked again by hand.
 *
 * So resolve the identifier to the programme it names, case- and shape-blind,
 * and answer with that programme's identity. An identifier that matches nothing
 * is handed back untouched: it is still what the record says, and blanking it
 * would throw a parent away rather than fail to display it.
 */
export function programmeSelectValue(
  programmes: CurriculumProgramme[],
  identifier: unknown,
): string {
  const raw = cleanText(identifier);
  if (!raw) return '';
  const programme = findProgramme(programmes, raw);
  return programme ? programmeIdentity(programme) : raw;
}

export function findCohort(cohorts: CurriculumCohort[], identifier: unknown): CurriculumCohort | undefined {
  return findByIdentifierThenName(cohorts, identifier, cohort => [cohort.id], cohort => [cohort.name]);
}

export function findGroup(groups: CurriculumGroup[], identifier: unknown): CurriculumGroup | undefined {
  return findByIdentifierThenName(groups, identifier, group => [group.id], group => [group.name]);
}

export function findModule(modules: CurriculumModule[], identifier: unknown): CurriculumModule | undefined {
  const key = normaliseKey(identifier);
  if (!key) return undefined;
  return modules.find(module => [
    module.moduleCatalogueId,
    module.catalogueId,
    module.moduleId,
    module.id,
    module.structureId,
    module.sourceId,
  ].map(normaliseKey).includes(key));
}

/** A cohort's programme, resolved from its stored programme id (then its name). */
export function cohortProgramme(
  cohort: CurriculumCohort,
  programmes: CurriculumProgramme[],
): CurriculumProgramme | undefined {
  return findProgramme(programmes, cohort.programmeId) || findProgramme(programmes, cohort.programme);
}

/** A group's parent chain: Cohort first, and the Programme *through* the cohort. */
export function resolveGroupContext(
  group: CurriculumGroup,
  cohorts: CurriculumCohort[],
  programmes: CurriculumProgramme[],
): ResolvedContext {
  const cohort = findCohort(cohorts, group.cohortId) || findCohort(cohorts, group.cohort);
  const programme = cohort
    ? cohortProgramme(cohort, programmes)
    : findProgramme(programmes, group.programmeId) || findProgramme(programmes, group.programme);
  return {
    programme,
    cohort,
    group,
    programmeId: programmeIdentity(programme || ({} as CurriculumProgramme)) || cleanText(cohort?.programmeId) || cleanText(group.programmeId),
    programmeName: cleanText(programme?.name) || cleanText(cohort?.programme) || cleanText(group.programme, 'Unassigned programme'),
    cohortId: cleanText(cohort?.id) || cleanText(group.cohortId),
    cohortName: cleanText(cohort?.name) || cleanText(group.cohort, 'Unassigned cohort'),
    groupId: cleanText(group.id),
    groupName: cleanText(group.name, 'Unnamed group'),
    linked: Boolean(cohort),
  };
}

/**
 * A module's parent chain. Group is the only real parent — cohort and programme
 * are read off the group, exactly as the persisted model says they should be.
 * A module with no resolvable group falls back to its own cached names and is
 * reported as `linked: false`.
 */
export function resolveModuleContext(
  module: CurriculumModule,
  groups: CurriculumGroup[],
  cohorts: CurriculumCohort[],
  programmes: CurriculumProgramme[],
): ResolvedContext {
  const group = findGroup(groups, module.groupId) || findGroup(groups, module.group);
  if (group) {
    const context = resolveGroupContext(group, cohorts, programmes);
    return { ...context, linked: true };
  }
  const cohort = findCohort(cohorts, module.cohortId) || findCohort(cohorts, module.cohort);
  const programme = cohort
    ? cohortProgramme(cohort, programmes)
    : findProgramme(programmes, module.programmeId) || findProgramme(programmes, module.programme);
  return {
    programme,
    cohort,
    group: undefined,
    programmeId: programmeIdentity(programme || ({} as CurriculumProgramme)) || cleanText(module.programmeId),
    programmeName: cleanText(programme?.name) || cleanText(module.programme, 'Unassigned programme'),
    cohortId: cleanText(cohort?.id) || cleanText(module.cohortId),
    cohortName: cleanText(cohort?.name) || cleanText(module.cohort, 'Unassigned cohort'),
    groupId: cleanText(module.groupId),
    groupName: cleanText(module.group, 'Unassigned group'),
    linked: false,
  };
}

// ---------------------------------------------------------------- cascades

/**
 * Any record carrying its own programme, narrowed to one programme. `''` means
 * "every programme".
 *
 * Generic because the same two fields identify a cohort, a group and an
 * archived one of either, and the archive lists are not `CurriculumCohort`s —
 * an archived row carries the delivery identity and the archive stamp, not the
 * counts and progress a live one does. Matching on the record's own
 * programmeId/programme rather than resolving through a parent matters there:
 * an archived group's cohort is usually archived too, so it is not in the live
 * cohort list to resolve against.
 */
export function recordsForProgramme<T extends { programmeId?: string; programme?: string }>(
  records: T[],
  programmes: CurriculumProgramme[],
  programmeId: string,
): T[] {
  if (!programmeId) return records;
  const programme = findProgramme(programmes, programmeId);
  const keys = programme ? programmeKeys(programme) : [normaliseKey(programmeId)];
  return records.filter(record => matchesAny([record.programmeId, record.programme], keys));
}

/** Cohorts under one programme. `''` means "every programme". */
export function cohortsForProgramme(
  cohorts: CurriculumCohort[],
  programmes: CurriculumProgramme[],
  programmeId: string,
): CurriculumCohort[] {
  return recordsForProgramme(cohorts, programmes, programmeId);
}

/** Groups under one cohort, or under one programme when no cohort is chosen. */
export function groupsForScope(
  groups: CurriculumGroup[],
  cohorts: CurriculumCohort[],
  programmes: CurriculumProgramme[],
  scope: { programmeId?: string; cohortId?: string },
): CurriculumGroup[] {
  const cohortId = cleanText(scope.cohortId);
  if (cohortId) {
    return groups.filter(group => sameIdentifier(group.cohortId, cohortId));
  }
  const programmeId = cleanText(scope.programmeId);
  if (!programmeId) return groups;
  const scoped = new Set(
    cohortsForProgramme(cohorts, programmes, programmeId).map(cohort => normaliseKey(cohort.id)),
  );
  return groups.filter(group => scoped.has(normaliseKey(group.cohortId)));
}

/** Modules under a Programme/Cohort/Group scope, resolved through the group. */
export function modulesForScope(
  modules: CurriculumModule[],
  groups: CurriculumGroup[],
  cohorts: CurriculumCohort[],
  programmes: CurriculumProgramme[],
  scope: { programmeId?: string; cohortId?: string; groupId?: string },
): CurriculumModule[] {
  const groupId = cleanText(scope.groupId);
  if (groupId) return modules.filter(module => sameIdentifier(module.groupId, groupId));

  const inScope = groupsForScope(groups, cohorts, programmes, scope);
  if (inScope.length === groups.length && !scope.cohortId && !scope.programmeId) return modules;
  const groupIds = new Set(inScope.map(group => normaliseKey(group.id)));
  return modules.filter(module => {
    if (groupIds.has(normaliseKey(module.groupId))) return true;
    // A module with no stored group still belongs somewhere: fall back to its
    // cached cohort/programme so it stays findable instead of silently missing.
    if (normaliseKey(module.groupId)) return false;
    if (scope.cohortId) return sameIdentifier(module.cohortId, scope.cohortId);
    const programme = findProgramme(programmes, scope.programmeId);
    const keys = programme ? programmeKeys(programme) : [normaliseKey(scope.programmeId)];
    return matchesAny([module.programmeId, module.programme], keys);
  });
}

// ------------------------------------------------------------------ search

/** Case-insensitive "every word appears somewhere in these fields" search. */
export function matchesSearch(query: string, fields: unknown[]): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const haystack = fields.map(field => String(field ?? '')).join(' ').toLowerCase();
  return terms.every(term => haystack.includes(term));
}

// ------------------------------------------------------------------ counts

export function countsForProgramme(
  programme: CurriculumProgramme,
  index: EntityIndex,
): { cohorts: number; groups: number; modules: number; learners: number } {
  const cohorts = cohortsForProgramme(index.cohorts, index.programmes, programmeIdentity(programme));
  const cohortIds = new Set(cohorts.map(cohort => normaliseKey(cohort.id)));
  const groups = index.groups.filter(group => cohortIds.has(normaliseKey(group.cohortId)));
  const groupIds = new Set(groups.map(group => normaliseKey(group.id)));
  const modules = index.modules.filter(module => groupIds.has(normaliseKey(module.groupId)));
  return {
    // The programme's own counters stay authoritative when they are populated;
    // the derived numbers cover a programme the compact payload has not counted.
    cohorts: cohorts.length || programme.cohorts || 0,
    groups: groups.length || programme.groups || 0,
    modules: modules.length || programme.modules || 0,
    learners: programme.learners || 0,
  };
}

// -------------------------------------------------------------- formatting

/**
 * Programme level, always read back as "Level 4".
 *
 * The programme drawer stores what was typed as `LVL-4`, but rows created
 * before that convention carry a bare `4` or an already-worded `Level 6`, so
 * the catalogue was showing all three spellings side by side. Only the number
 * carries meaning; everything around it is formatting, so the display is
 * derived from the digits rather than from however the row was saved.
 */
export function formatProgrammeLevel(value: unknown, fallback = 'Not set'): string {
  const digits = cleanText(value).match(/\d+/);
  return digits ? `Level ${digits[0]}` : fallback;
}

export function formatDateLabel(value: unknown): string {
  const text = cleanText(value);
  if (!text) return '—';
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return parsed.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function formatDateTimeLabel(value: unknown): string {
  const text = cleanText(value);
  if (!text) return '—';
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return parsed.toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** The calendar year a cohort belongs to, for the Cohorts page year filter. */
/**
 * How many days of holiday a stored cohort's practical end date was extended by.
 *
 * Zero when nothing moved. Read-only screens use this to explain why a 24 month
 * cohort finishes later than 24 months from its start date -- the duration is
 * the contracted figure and stays put, so without this the two read as a
 * contradiction. `baseEndDate` is the duration rule alone; the stored practical
 * end date already has the holidays folded in.
 */
export function cohortHolidayExtensionDays(
  cohort: Partial<Pick<CurriculumCohort, 'baseEndDate' | 'endDate' | 'practicalEndDate'>>,
): number {
  const base = cleanText(cohort.baseEndDate);
  const actual = cleanText(cohort.practicalEndDate) || cleanText(cohort.endDate);
  if (!base || !actual) return 0;
  const from = new Date(base);
  const to = new Date(actual);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) return 0;
  const days = Math.round((to.getTime() - from.getTime()) / 86_400_000);
  return days > 0 ? days : 0;
}

/**
 * The date part of a stored value, so an ISO date and an ISO datetime for the
 * same day compare as equal. `YYYY-MM-DD` sorts as it reads, which is what makes
 * the window comparisons below plain string comparisons.
 */
function dateKey(value: unknown): string {
  return cleanText(value).slice(0, 10);
}

function shiftDateByMonths(value: unknown, deltaMonths: number): string {
  const key = dateKey(value);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  if (!match) return '';
  const year = Number(match[1]);
  const monthIndex = Number(match[2]) - 1 + deltaMonths;
  const day = Number(match[3]);
  if (!Number.isFinite(year) || !Number.isFinite(monthIndex) || !Number.isFinite(day)) return '';
  const lastDay = new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate();
  const shifted = new Date(Date.UTC(year, monthIndex, Math.min(day, lastDay)));
  return Number.isNaN(shifted.getTime()) ? '' : shifted.toISOString().slice(0, 10);
}

export function moduleSoftStartDate(cohortStart: unknown): string {
  return shiftDateByMonths(cohortStart, -1);
}

const DAY_MS = 86_400_000;

/** A date as a whole number of days since the epoch, or null when unparseable. */
function dayNumber(value: unknown): number | null {
  const text = dateKey(value);
  if (!text) return null;
  // Fixed to UTC midnight so the arithmetic below cannot be shifted by the
  // reader's timezone or by a daylight-saving boundary inside the period.
  const parsed = Date.parse(`${text}T00:00:00Z`);
  return Number.isNaN(parsed) ? null : Math.round(parsed / DAY_MS);
}

/** England's weekend, by the weekday numbering `Date.getDay` uses. */
const WEEKEND_DAY_NAMES: Record<number, string> = { 0: 'Sunday', 6: 'Saturday' };

/**
 * The note a Saturday or Sunday date carries, or '' on any weekday.
 *
 * Not a refusal -- a weekend date is still saved as typed -- just a heads-up,
 * since Saturday and Sunday are holidays in England and nothing normally
 * delivers on them. Shared by the module drawer and the cohort drawer so the
 * same date gets the same sentence wherever it is picked.
 */
export function weekendDateNotice(value: unknown): string {
  const days = dayNumber(value);
  if (days === null) return '';
  // Day 0 of the epoch was a Thursday (getDay 4), so shifting by 4 lands Sunday on 0.
  const name = WEEKEND_DAY_NAMES[(days + 4) % 7];
  return name ? `${name} is a weekend — no delivery normally runs that day.` : '';
}

export interface CohortWeekCapacity {
  /** Weeks the cohort period holds before any holiday -- the minimum a module needs. */
  totalWeeks: number;
  /** Days the period's holidays cover inside it, deduplicated across overlaps -- e.g. a 3-day and a 5-day holiday is 8, not two separate weeks. */
  holidayDays: number;
  /** holidayDays rounded up to whole weeks -- what actually gets added to the maximum. */
  holidayWeeks: number;
  /** totalWeeks + holidayWeeks -- the ceiling once every holiday has pushed the plan out. */
  maxWeeks: number;
}

/**
 * How many weeks a cohort has to deliver in, before and after its holidays.
 *
 * The contracted period is fixed -- holidays never move a cohort's start or
 * practical end date -- so `totalWeeks` (the minimum) is what the period holds
 * before any of them. Each holiday pushes a module's own plan past the period
 * rather than shrinking it: a session that lands on a holiday is skipped and
 * delivered afterwards, so the days a holiday covers are additional weeks on
 * top, not weeks subtracted -- `maxWeeks` is the minimum plus that extension.
 *
 * Holiday days are deduplicated across overlapping holidays and rounded up to
 * whole weeks as a single count (7 days of holiday is one extra week,
 * however those days happen to fall against the cohort's own week
 * boundaries) rather than counting how many of the period's own weeks the
 * holiday's date range touches, which over- or under-counts short holidays
 * that straddle a week boundary.
 */
export function cohortWeekCapacity(
  periodStart: unknown,
  periodEnd: unknown,
  holidays: Array<{ startDate?: unknown; endDate?: unknown }> = [],
): CohortWeekCapacity {
  const start = dayNumber(periodStart);
  const end = dayNumber(periodEnd);
  if (start == null || end == null || end < start) {
    return { totalWeeks: 0, holidayDays: 0, holidayWeeks: 0, maxWeeks: 0 };
  }
  const totalWeeks = Math.ceil((end - start + 1) / 7);
  const touchedDays = new Set<number>();
  holidays.forEach(holiday => {
    const from = dayNumber(holiday.startDate);
    if (from == null) return;
    const to = dayNumber(holiday.endDate) ?? from;
    // Clamped to the period, so a holiday that only overlaps it costs the days
    // it actually reaches into and nothing else.
    for (let day = Math.max(from, start); day <= Math.min(to, end); day += 1) {
      touchedDays.add(day);
    }
  });
  const holidayDays = touchedDays.size;
  const holidayWeeks = Math.ceil(holidayDays / 7);
  return {
    totalWeeks,
    holidayDays,
    holidayWeeks,
    maxWeeks: totalWeeks + holidayWeeks,
  };
}

/**
 * The bank holidays a module under this cohort has to step over.
 *
 * Every one from the cohort's start date onwards, with no end bound -- the same
 * rule the backend applies in `scheduling_holidays_from`. Bounding it at the
 * cohort's practical end date would let the sessions that overshoot it land
 * straight back on a bank holiday, and overshooting is precisely what a holiday
 * does to a plan: each one it steps over pushes the plan a delivery slot later.
 *
 * A holiday after the last session is inert -- the set is only ever tested for
 * membership against a session date -- so a cohort with no start date yet gets
 * the whole calendar rather than none of it.
 *
 * `excludedIds` narrows the set further: the holidays a human unticked in the
 * cohort drawer. A holiday outside the cohort's own period was never offered
 * as a checkbox, so it cannot appear here, and stays skipped either way.
 *
 * This is NOT the cohort's own holiday list. That one is bounded to the
 * cohort's period, is what the cohort drawer shows and the cohort row records,
 * and is what `cohortWeekCapacity` above measures.
 */
export function holidaysForScheduling<H extends { id?: string | number; startDate?: string; endDate?: string }>(
  holidays: H[],
  cohortStart?: string,
  excludedIds?: Array<string | number>,
): H[] {
  const start = cleanText(cohortStart);
  let result = holidays;
  if (start) {
    result = result.filter(holiday => (cleanText(holiday.endDate) || cleanText(holiday.startDate)) >= start);
  }
  const excluded = new Set((excludedIds || []).map(normaliseKey).filter(Boolean));
  if (excluded.size) {
    result = result.filter(holiday => !excluded.has(normaliseKey(holiday.id)));
  }
  return result;
}

/**
 * The delivery window a module placed in a cohort has to fit inside.
 *
 * The practical end date is the boundary, not the apprenticeship end: the EPA
 * period after it carries no delivery. The cohort's holidays are drawn from
 * this same window, so a session plan that skips them still lands inside it.
 */
export function cohortDeliveryWindow(
  cohort: Partial<Pick<CurriculumCohort, 'startDate' | 'endDate' | 'practicalEndDate'>> | null | undefined,
): { start: string; end: string } {
  return {
    start: dateKey(cohort?.startDate),
    end: dateKey(cohort?.practicalEndDate) || dateKey(cohort?.endDate),
  };
}

/**
 * Why a module's dates are outside its cohort, or null when they are inside.
 *
 * The same three refusals the backend enforces, worded the same way, so the
 * drawer can say it before the save is attempted: a module can soft-start up
 * to one calendar month before the cohort opens, cannot start after it has
 * finished, and cannot run past the end -- which is the one a generated
 * session plan reaches on its own.
 */
export function moduleCohortDateError(
  cohort: Partial<Pick<CurriculumCohort, 'startDate' | 'endDate' | 'practicalEndDate'>> | null | undefined,
  startDate: unknown,
  endDate: unknown,
): string | null {
  const { start: cohortStart, end: cohortEnd } = cohortDeliveryWindow(cohort);
  const softStart = moduleSoftStartDate(cohortStart) || cohortStart;
  const moduleStart = dateKey(startDate);
  const moduleEnd = dateKey(endDate);

  if (moduleStart && softStart && moduleStart < softStart) {
    return `The module cannot start more than one month before the cohort start date (${formatDateLabel(cohortStart)}).`;
  }
  if (moduleStart && cohortEnd && moduleStart > cohortEnd) {
    return `The module cannot start after the cohort end date (${formatDateLabel(cohortEnd)}).`;
  }
  if (moduleEnd && cohortEnd && moduleEnd > cohortEnd) {
    return `The module cannot finish after the cohort end date (${formatDateLabel(cohortEnd)}). `
      + `It currently finishes ${formatDateLabel(moduleEnd)} - move the start date earlier or reduce the number of sessions.`;
  }
  return null;
}

export function cohortYear(cohort: Pick<CurriculumCohort, 'startDate'>): string {
  const match = /(\d{4})/.exec(cleanText(cohort.startDate));
  return match ? match[1] : '';
}

export function scheduleLabel(group: Pick<CurriculumGroup, 'weekDays' | 'startTime' | 'endTime' | 'schedule'>): string {
  const days = cleanText(group.weekDays);
  const start = cleanText(group.startTime);
  const end = cleanText(group.endTime);
  if (days && start) return `${days} · ${start}${end ? `–${end}` : ''}`;
  return cleanText(group.schedule) || '—';
}

export const CURRICULUM_STATUS_TONES: Record<string, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  published: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  planned: 'border-sky-200 bg-sky-50 text-sky-700',
  // A session that has a Teams join link is 'scheduled' — same "ready, not yet
  // happened" meaning as planned, so it reads with the same tone.
  scheduled: 'border-sky-200 bg-sky-50 text-sky-700',
  draft: 'border-amber-200 bg-amber-50 text-amber-700',
  review: 'border-amber-200 bg-amber-50 text-amber-700',
  completed: 'border-foreground-200 bg-background-100 text-foreground-600',
  archived: 'border-foreground-200 bg-background-100 text-foreground-500',
};

export function statusTone(status: unknown): string {
  return CURRICULUM_STATUS_TONES[normaliseKey(status)] || 'border-foreground-200 bg-background-100 text-foreground-600';
}

/**
 * Two form snapshots hold the same answers. Drawers use it to tell an untouched
 * form from one carrying unsaved edits, so closing the second can ask first.
 *
 * Keys are compared one at a time rather than by stringifying the whole object,
 * so a snapshot assembled in a different key order is not read as a change, and
 * null / undefined / '' are treated alike — for a form field they all mean the
 * user has not answered.
 */
export function sameFormValues(
  left: Record<string, unknown> | null | undefined,
  right: Record<string, unknown> | null | undefined,
): boolean {
  const keys = new Set([...Object.keys(left || {}), ...Object.keys(right || {})]);
  return [...keys].every(key => JSON.stringify(left?.[key] ?? '') === JSON.stringify(right?.[key] ?? ''));
}

// ---------------------------------------------------------------------------
// Sorting
//
// Every entity list is read for a different question -- "what did we just add",
// "which programme is biggest", "which cohort starts next" -- and the server
// order (programme, then name) answers only one of them. These are the orders
// the pages offer, kept here so Programmes, Cohorts, Groups and the Module
// Builder sort by the same rules rather than by four near-identical ones.
//
// The empty value is the list as the endpoint returned it, so "Reset" has an
// order to go back to and the pages open exactly as they always did.
// ---------------------------------------------------------------------------

export interface EntitySortOption<T> {
  value: string;
  label: string;
  compare: (left: T, right: T) => number;
}

/**
 * A copy of `rows` in the chosen order, or `rows` untouched for the default
 * choice -- that is the order the endpoint already returned.
 */
export function sortEntities<S, T extends S>(rows: T[], options: Array<EntitySortOption<S>>, value: string): T[] {
  const chosen = options.find(option => option.value && option.value === value);
  if (!chosen) return rows;
  // Array.prototype.sort is stable, so rows the chosen key cannot separate keep
  // the order the server put them in.
  return [...rows].sort(chosen.compare);
}

/** A-Z on a text field, case- and accent-insensitively, blanks last. */
export function compareText(left: unknown, right: unknown): number {
  const a = cleanText(left);
  const b = cleanText(right);
  if (!a && !b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return a.localeCompare(b, 'en', { sensitivity: 'base', numeric: true });
}

/**
 * Newest first. Dates arrive as ISO strings, which compare correctly as text,
 * and anything undated sorts last rather than reading as the oldest record.
 */
export function compareRecency(left: unknown, right: unknown): number {
  const a = cleanText(left);
  const b = cleanText(right);
  if (!a && !b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return b.localeCompare(a);
}

/** Earliest first. Anything undated sorts last, for the same reason. */
export function compareChronology(left: unknown, right: unknown): number {
  const a = cleanText(left);
  const b = cleanText(right);
  if (!a && !b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return a.localeCompare(b);
}

/** Largest first, with a missing count read as zero. */
export function compareCountDesc(left: unknown, right: unknown): number {
  return (Number(right) || 0) - (Number(left) || 0);
}

/**
 * What "recently added" sorts on. Rows written before the created-at column
 * existed carry no creation date, and fall back to when they were last touched
 * -- the closest thing they hold. Never to the top of the list.
 */
export function recencyKey(row: { createdAt?: unknown; updatedAt?: unknown; lastUpdated?: unknown }): string {
  return cleanText(row.createdAt) || cleanText(row.updatedAt) || cleanText(row.lastUpdated);
}

export const PROGRAMME_SORT_OPTIONS: Array<EntitySortOption<CurriculumProgramme>> = [
  { value: '', label: 'Default order', compare: () => 0 },
  { value: 'recent', label: 'Recently added', compare: (a, b) => compareRecency(recencyKey(a), recencyKey(b)) },
  { value: 'updated', label: 'Recently updated', compare: (a, b) => compareRecency(a.lastUpdated, b.lastUpdated) },
  { value: 'name', label: 'Name (A-Z)', compare: (a, b) => compareText(a.name, b.name) },
  { value: 'name-desc', label: 'Name (Z-A)', compare: (a, b) => compareText(b.name, a.name) },
  { value: 'cohorts', label: 'Most cohorts', compare: (a, b) => compareCountDesc(a.cohorts, b.cohorts) },
  { value: 'modules', label: 'Most modules', compare: (a, b) => compareCountDesc(a.modules, b.modules) },
  { value: 'learners', label: 'Most learners', compare: (a, b) => compareCountDesc(a.learners, b.learners) },
];

export const COHORT_SORT_OPTIONS: Array<EntitySortOption<CurriculumCohort>> = [
  { value: '', label: 'Default order', compare: () => 0 },
  { value: 'recent', label: 'Recently added', compare: (a, b) => compareRecency(recencyKey(a), recencyKey(b)) },
  { value: 'name', label: 'Name (A-Z)', compare: (a, b) => compareText(a.name, b.name) },
  { value: 'name-desc', label: 'Name (Z-A)', compare: (a, b) => compareText(b.name, a.name) },
  { value: 'programme', label: 'Programme (A-Z)', compare: (a, b) => compareText(a.programme, b.programme) || compareText(a.name, b.name) },
  { value: 'start', label: 'Starting soonest', compare: (a, b) => compareChronology(a.startDate, b.startDate) },
  { value: 'start-desc', label: 'Starting latest', compare: (a, b) => compareRecency(a.startDate, b.startDate) },
  { value: 'end', label: 'Ending soonest', compare: (a, b) => compareChronology(a.practicalEndDate || a.endDate, b.practicalEndDate || b.endDate) },
  { value: 'learners', label: 'Most learners', compare: (a, b) => compareCountDesc(a.learners, b.learners) },
];

export const GROUP_SORT_OPTIONS: Array<EntitySortOption<CurriculumGroup>> = [
  { value: '', label: 'Default order', compare: () => 0 },
  { value: 'recent', label: 'Recently added', compare: (a, b) => compareRecency(recencyKey(a), recencyKey(b)) },
  { value: 'name', label: 'Name (A-Z)', compare: (a, b) => compareText(a.name, b.name) },
  { value: 'name-desc', label: 'Name (Z-A)', compare: (a, b) => compareText(b.name, a.name) },
  { value: 'cohort', label: 'Cohort (A-Z)', compare: (a, b) => compareText(a.cohort, b.cohort) || compareText(a.name, b.name) },
  { value: 'start', label: 'Starting soonest', compare: (a, b) => compareChronology(a.startDate, b.startDate) },
  { value: 'learners', label: 'Most learners', compare: (a, b) => compareCountDesc(a.learners, b.learners) },
  { value: 'modules', label: 'Most modules', compare: (a, b) => compareCountDesc((a.moduleIds || a.modules || []).length, (b.moduleIds || b.modules || []).length) },
];

/**
 * The Module Builder holds its rows as authoring catalogue items rather than as
 * `CurriculumModule` -- same records, different field names -- so these options
 * read whichever of the two names each field goes by.
 */
export interface SortableModuleFields {
  title?: string;
  name?: string;
  createdAt?: string;
  updatedAt?: string;
  lastUpdated?: string;
  startDate?: string;
  weeks?: number;
  lessonCount?: number;
  programmeName?: string;
  programme?: string;
}

export const MODULE_SORT_OPTIONS: Array<EntitySortOption<SortableModuleFields>> = [
  { value: '', label: 'Default order', compare: () => 0 },
  { value: 'recent', label: 'Recently added', compare: (a, b) => compareRecency(recencyKey(a), recencyKey(b)) },
  { value: 'name', label: 'Name (A-Z)', compare: (a, b) => compareText(a.title || a.name, b.title || b.name) },
  { value: 'name-desc', label: 'Name (Z-A)', compare: (a, b) => compareText(b.title || b.name, a.title || a.name) },
  { value: 'programme', label: 'Programme (A-Z)', compare: (a, b) => compareText(a.programmeName || a.programme, b.programmeName || b.programme) || compareText(a.title || a.name, b.title || b.name) },
  { value: 'start', label: 'Starting soonest', compare: (a, b) => compareChronology(a.startDate, b.startDate) },
  { value: 'weeks', label: 'Most weeks', compare: (a, b) => compareCountDesc(a.weeks, b.weeks) },
  { value: 'components', label: 'Most components', compare: (a, b) => compareCountDesc(a.lessonCount, b.lessonCount) },
];
