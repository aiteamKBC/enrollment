import { moduleFormTarget, type ModuleFormDeliveryRef, type ModuleFormTarget } from './moduleForm.helpers';
export type { ModuleFormTarget } from './moduleForm.helpers';
// ============================================================================
// The create/edit form for a Module.
//
// It sits apart from `forms.tsx` on purpose: creating a module has to build the
// same authoring skeleton the Module Builder reads, which means importing
// `module-builder/moduleAuthoringData`. Keeping that import out of the shared
// forms file keeps the Programmes, Cohorts and Groups pages off the authoring
// chunk.
//
// One form, two canonical save paths â€” neither of them reimplemented here:
//   * placed in a group -> POST /curriculum/groups/<id>/modules/, which bounds
//     both module dates to the cohort's delivery window, builds the session plan
//     from the group's delivery days and holidays, refuses a tutor
//     double-booking and mirrors the assignment onto the tutor's profile (the
//     notification);
//   * no group yet      -> POST /curriculum/modules/, a catalogue draft that
//     gets its cohort, group, tutor and dates when it is placed later.
// Editing goes through PATCH /curriculum/modules/<id>/, which carries the same
// cohort window, conflict check and tutor notification as the create-in-group
// path.
// ============================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { clockLabel, durationLabel, normalizedClock } from '../../teams-meetings/calendarTime';
import { useNavigate } from 'react-router-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import { formatSystemTimestamp } from '@/lib/format';
import { DatePickerField } from '@/components/feature/DatePickerField';
import { showCurriculumAlert, showCurriculumConfirm } from '@/components/feature/CurriculumSweetAlert';
import { TutorClashNotice } from '@/components/feature/TutorClashNotice';
import { useTutorAvailability } from '@/hooks/useTutorAvailability';
import {
  createGroupModule,
  curriculumErrorMessage,
  isTutorConflictError,
  fetchTutorAssignmentEmailStatus,
  previewModuleSessionPlan,
  sendTutorAssignmentEmail,
  tutorConflictMessage,
  updateCurriculumModule,
  type CurriculumGroup,
  type CurriculumCohort,
  type CurriculumHoliday,
  type CurriculumModule,
  type CurriculumWeeklySession,
  type CurriculumProgramme,
  type CurriculumSessionPlanPreview,
  type TutorAssignmentEmailStatus,
  guardedInput,
} from '@/lib/curriculumApi';
import type { SelectOption } from '@/components/feature/SelectField';
import { createNewModule } from '../../module-builder/moduleAuthoringData';
import {
  cleanText,
  cohortsForProgramme,
  formatDateLabel,
  groupsForScope,
  holidaysForScheduling,
  moduleCohortDateError,
  moduleSoftStartDate,
  normaliseKey,
  programmeIdentity,
  programmeSelectValue,
  sameFormValues,
  sameIdentifier,
  visibleNotes,
  weekendDateNotice,
} from './model';
import { confirmTeamsCalendarUpdate } from './teamsCalendarNotice';
import { weekCountChangeForSave, weekCountChangeText, type StoredWeekStructure } from './weekCountChange';
import {
  CoEditNotice,
  ColorControl,
  CoverImageControl,
  EntityDrawer,
  FormField,
  MultiSelectControl,
  SelectControl,
  TextAreaControl,
  TextControl,
  WeekdayControl,
  type FormChainStep,
  type MultiSelectOption,
} from './ui';
import { useFormFieldMerge, useFormSeedGuard, useRecordGuard, useRecordRebaseRetry } from './useDrawerState';

export interface ModuleFormDefaults {
  programmeId?: string;
  cohortId?: string;
  groupId?: string;
}

/** What a successful save hands back, so the caller can go straight to the module. */
export interface SavedModuleRef {
  catalogueId: string;
  name: string;
  created: boolean;
  /**
   * Where this save actually placed the module. A caller driving a chain (the
   * structure wizard) needs it: the parent may have been picked in this form
   * rather than created by an earlier step, and only the form knows which.
   */
  programmeId?: string;
  programmeName?: string;
  cohortId?: string;
  cohortName?: string;
  /** Every group the module was placed in — one module regularly runs for several. */
  groupIds?: string[];
  groupNames?: string[];
}

// The names these fields go by on screen. A notice that says "you both changed
// the groupStartTime" is our vocabulary, not the reader's.
const MODULE_FIELD_LABELS: Record<string, string> = {
  name: 'module name',
  programmeId: 'programme',
  cohortId: 'cohort',
  groupIds: 'groups',
  sessionsNumber: 'number of weeks',
  tutor: 'tutor',
  status: 'status',
  description: 'description',
  color: 'colour',
  coverImage: 'cover image',
};

const UNASSIGNED = 'unassigned';
// Delivery days and clock controls belong to the Group editor. Keep the old
// controls in this drawer's source for the derived schedule helpers, but never
// render them as editable module fields.
const showInheritedScheduleControls = false;
const TRUE_FLAGS = new Set(['1', 'true', 'yes', 'y', 'on']);
const FALSE_FLAGS = new Set(['0', 'false', 'no', 'n', 'off']);

type ProgrammeVisibilityFields = CurriculumProgramme & {
  is_active?: unknown;
  is_archived?: unknown;
  is_archive?: unknown;
  archived?: unknown;
  deletedAt?: unknown;
  deleted_at?: unknown;
};

function flagIsTrue(value: unknown): boolean {
  return value === true || TRUE_FLAGS.has(normaliseKey(value));
}

function flagIsFalse(value: unknown): boolean {
  return value === false || FALSE_FLAGS.has(normaliseKey(value));
}

function isSelectableProgramme(programme: CurriculumProgramme): boolean {
  const row = programme as ProgrammeVisibilityFields;
  const archived = programme.isArchived === true
    || flagIsTrue(row.is_archived)
    || flagIsTrue(row.is_archive)
    || flagIsTrue(row.archived)
    || Boolean(cleanText(row.deletedAt || row.deleted_at))
    || normaliseKey(programme.status) === 'archived';
  const inactive = programme.isActive === false || flagIsFalse(row.is_active);
  return !archived && !inactive;
}

export function ModuleFormDrawer({
  open,
  module,
  defaults,
  programmes,
  cohorts,
  groups,
  holidays = [],
  tutorNames = [],
  lockGroup = false,
  chain,
  onClose,
  onSaved,
  onSavingChange,
  storedStructure,
}: {
  open: boolean;
  /** Present when editing; absent when creating. */
  module?: ModuleFormTarget | null;
  defaults?: ModuleFormDefaults;
  programmes: CurriculumProgramme[];
  cohorts: CurriculumCohort[];
  groups: CurriculumGroup[];
  /** Used to preview the session dates the backend will generate. */
  holidays?: CurriculumHoliday[];
  tutorNames?: string[];
  /** True inside a Group workspace, where the parent is not up for debate. */
  lockGroup?: boolean;
  /** Set when the structure wizard is driving this form as one step of a chain. */
  chain?: FormChainStep;
  onClose: () => void;
  onSaved: (saved: SavedModuleRef) => unknown | Promise<unknown>;
  /** Lets the owning table keep its progress line visible for the whole save. */
  onSavingChange?: (saving: boolean) => void;
  /**
   * The module as the server last stored it, from a caller that holds it (the
   * Module Builder). With it, a save that resizes the weeks says so first.
   */
  storedStructure?: () => StoredWeekStructure | null;
}) {
  const [name, setName] = useState('');
  const [programmeId, setProgrammeId] = useState('');
  const [cohortId, setCohortId] = useState('');
  // Which groups this save places the module in. The same module is regularly
  // taught to more than one group of a cohort, and each group needs a delivery of
  // its own -- its own session dates off its own delivery days, its own tutor
  // booking -- so this is a list rather than the single choice it used to be.
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [sessionsNumber, setSessionsNumber] = useState('1');
  const [sessionsPerWeek, setSessionsPerWeek] = useState('1');
  const [weekDays, setWeekDays] = useState('');
  const [groupStartTime, setGroupStartTime] = useState('');
  const [groupEndTime, setGroupEndTime] = useState('');
  const [weeklyTimes, setWeeklyTimes] = useState<Record<string, { startTime: string; endTime: string }>>({});
  const [startDate, setStartDate] = useState('');
  const [targetEndDate, setTargetEndDate] = useState('');
  const [tutor, setTutor] = useState('');
  // Whether this save should tell the tutor. This is intent, not memory: the
  // record of who has actually been written to lives in the assignment ledger
  // and is read back below, so nothing here has to be remembered between opens.
  const [notifyTutor, setNotifyTutor] = useState(false);
  // What the ledger says about the selected tutor and this module's deliveries.
  // null while it is being read, or when there is nothing to ask about yet.
  const [tutorEmailStatus, setTutorEmailStatus] = useState<TutorAssignmentEmailStatus | null>(null);
  const [status, setStatus] = useState('draft');
  const [description, setDescription] = useState('');
  const [color, setColor] = useState('#2563eb');
  // Optional module artwork. Either a pasted URL or a data: URL read off the
  // picked file -- the same two shapes a free course's cover takes, stored on
  // the module row rather than uploaded anywhere.
  const [coverImage, setCoverImage] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 'later' lets a brand-new module save with no programme, cohort or group at
  // all -- the same catalogue-draft save `createNewModule` already supports,
  // just no longer blocked by the placement checks below. Only offered on a
  // fresh create: once a module carries a stored placement, editing it goes
  // through the normal required fields (see `initiallyAssigned`).
  const [assignMode, setAssignMode] = useState<'now' | 'later'>('now');
  // Whether the record this drawer opened with already had a placement. Read
  // once at seed time so a person clearing the fields mid-edit cannot make an
  // already-assigned module's save silently skip the requirement.
  const initiallyAssigned = useRef(false);
  // Whether the Programme/Cohort/Group picker (and the Weeks/dates/Tutor that
  // only mean something once one is chosen) are shown at all. "Assign later"
  // does not just relax their validation -- it hides them, so the drawer reads
  // as the small, title-only form it actually is. Defaults to shown for a
  // fresh create (matching "Assign now") and hidden for a module that is
  // already saved unassigned, with a link to bring them back either way.
  const [revealPlacement, setRevealPlacement] = useState(true);
  // Held for the Teams notice on save, which offers to take the reader to the
  // calendar these dates have just left behind.
  const navigate = useNavigate();
  // A group whose attach was refused for a tutor double-booking, offered a
  // one-click override rather than a dead end: two deliveries of the same
  // module really can share a tutor (a co-taught slot), and the backend
  // already accepts `allowTutorConflict` for exactly that -- this is just the
  // first screen that surfaces it instead of stopping at the 409.
  const [tutorConflictGroup, setTutorConflictGroup] = useState<{ groupId: string; groupName: string } | null>(null);
  // Groups already attached (or explicitly overridden) in this submit session,
  // so retrying after "Book anyway" does not re-create the ones that already
  // succeeded before the refusal.
  const attachedThisSession = useRef<Set<string>>(new Set());
  const overrideGroupIds = useRef<Set<string>>(new Set());
  // The first group's catalogue id from a create-path run, kept across a
  // "Book anyway" retry so the later groups attach to the same module rather
  // than each minting their own.
  const createdCatalogueId = useRef('');
  const [plan, setPlan] = useState<CurriculumSessionPlanPreview | null>(null);
  const [planFor, setPlanFor] = useState('');
  const seededPlanKey = useRef('');
  const [planLoading, setPlanLoading] = useState(false);
  // The inputs `plan` was fetched for. A plan whose key no longer matches the
  // form is stale, and the locally projected end date is used instead of it.
  // The record the plan in state belongs to, so re-seeding the same one does not
  // throw it away. See the seed effect below.
  // True while the debounced session-plan preview is in flight. It no longer
  // holds the save back: the end date is projected locally the moment the weeks
  // or the start date move, so a save can never carry the previous value's date.
  // What the drawer opened with, for the unsaved-changes check below.
  const baseline = useRef<Record<string, unknown>>({});
  const selectableProgrammes = useMemo(
    () => programmes.filter(isSelectableProgramme),
    [programmes],
  );

  // The structure wizard drives this same form as the last step of its chain.
  const chained = Boolean(chain?.chained);

  useEffect(() => {
    onSavingChange?.(saving);
  }, [onSavingChange, saving]);

  // The delivery pattern travels as one value as well as five. Merging its
  // halves independently -- a colleague's days with this reader's times -- would
  // produce a timetable neither of them chose, and this form is what the Teams
  // calendar is built from, so that timetable would be booked.
  const deliveryPattern = { sessionsPerWeek, weekDays, groupStartTime, groupEndTime, weeklyTimes };
  const liveValues = {
    name, programmeId, cohortId, groupIds, sessionsNumber, sessionsPerWeek, weekDays,
    groupStartTime, groupEndTime, weeklyTimes, startDate, targetEndDate, tutor, status,
    description, color, coverImage, deliveryPattern,
  };
  const dirty = !sameFormValues(liveValues, baseline.current);
  const liveRef = useRef<Record<string, unknown>>(liveValues);
  liveRef.current = liveValues;
  const coEdit = useFormFieldMerge(baseline, liveRef, MODULE_FIELD_LABELS);
  // The server refuses a save built on a version somebody else has already
  // replaced. The token is the Module Builder's own: this drawer writes through
  // the same structure save, so a Builder save makes an open drawer stale and
  // the other way round.
  const guard = useRecordGuard<ModuleFormTarget>('module', module?.id, open, module);
  // The seeding effect below has to depend on `groups`, `cohorts` and
  // `programmes` to resolve the module's parent chain, and those arrays get a new
  // identity whenever the page refreshes them -- the Module Builder in particular
  // fetches its picker scope *after* this drawer is already open. Re-seeding then
  // wiped whatever had been chosen. See useFormSeedGuard.
  const allowSeed = useFormSeedGuard(dirty);

  // The delivery this drawer owns when editing: the module row's own group. It
  // stays the one a PATCH moves; every other ticked group gets its own delivery.
  const ownGroupId = cleanText(module?.groupId) || cleanText(module?.deliveryUsages?.[0]?.groupId);
  // Groups this module already runs for through another delivery. They are shown
  // ticked and locked -- re-sending them would overwrite that delivery's own
  // tutor and dates with this form's.
  const attachedGroupIds = useMemo(() => {
    const seen = new Set<string>();
    (module?.deliveryUsages || []).forEach(usage => {
      const id = cleanText(usage.groupId);
      if (id && !sameIdentifier(id, ownGroupId)) seen.add(id);
    });
    return Array.from(seen);
  }, [module?.deliveryUsages, ownGroupId]);

  useEffect(() => {
    const recordKey = cleanText(module?.id) || 'new-module';
    // A refused save hands back the stored module; folding that in is the same
    // operation as folding in one a poll brought, so everything below reads
    // whichever version is current under one name.
    const record = guard.stored || module;
    const verdict = allowSeed(open, recordKey);
    if (!verdict) return;
    // On a merge the drawer is already open on this module with edits in it, so
    // the two refusals below would be answering a question nobody asked.
    if (verdict === 'seed') {
      setError(null);
      setSaving(false);
    }
    if (seededPlanKey.current !== recordKey) {
      seededPlanKey.current = recordKey;
      setPlan(null);
      setPlanFor('');
    }
    // Only when the drawer is being pointed at a DIFFERENT record.
    //
    // The plan is derived from the fields seeded below, not one of them, and
    // this effect re-runs whenever `module`, `groups` or `cohorts` arrive as new
    // objects -- which the list behind now does on its own, when the reader
    // returns to the tab and when the epoch poll notices somebody's write. A
    // re-seed of the SAME module puts identical values back, so the plan effect
    // below sees no dependency move and never refetches: clearing it here left
    // "Choose a group delivery day and start date" sitting where a plan had been
    // a moment earlier, with nothing able to bring it back.
    // Only on a real (re)seed. A merge happens while the reader is part-way
    // through this same drawer, and clearing the groups they have just attached
    // or the tutor clash they are reading would be the mid-edit reset the seed
    // guard exists to prevent, arriving by a different door.
    if (verdict === 'seed') {
      setTutorConflictGroup(null);
      attachedThisSession.current = new Set();
      overrideGroupIds.current = new Set();
      createdCatalogueId.current = '';
    }

    const storedDelivery = record?.deliveryUsages?.[0];
    // Id across every row first, name only if that found nothing: group names
    // ("G1") and cohort names ("October 2026") repeat across programmes, so an
    // id-or-name predicate inside one `find` attached the module to whichever
    // namesake happened to be listed first. See findByIdentifierThenName.
    const parentGroupId = record?.groupId || storedDelivery?.groupId || defaults?.groupId;
    const parentGroup = groups.find(group => sameIdentifier(group.id, parentGroupId))
      || groups.find(group => sameIdentifier(group.name, storedDelivery?.group));
    const parentCohortId = record?.cohortId || storedDelivery?.cohortId || parentGroup?.cohortId || defaults?.cohortId;
    const parentCohort = cohorts.find(cohort => sameIdentifier(cohort.id, parentCohortId))
      || cohorts.find(cohort => sameIdentifier(cohort.name, storedDelivery?.cohort));
    // Snapped onto the option values with programmeSelectValue: every source
    // below names the programme in whatever shape wrote it, and a <select>
    // matches by exact string. See the helper for what that used to cost.
    const resolvedProgrammeId = programmeSelectValue(
      selectableProgrammes,
      cleanText(parentCohort?.programmeId)
        || cleanText(parentGroup?.programmeId)
        || cleanText(record?.programmeId)
        || cleanText(storedDelivery?.programmeId)
        || cleanText(record?.programme)
        || cleanText(storedDelivery?.programme)
        || defaults?.programmeId,
    ) || (selectableProgrammes.length === 1 ? programmeIdentity(selectableProgrammes[0]) : '');

    const directTutor = cleanText(record?.tutor);
    const storedTutor = normaliseKey(directTutor) === UNASSIGNED
      ? cleanText(storedDelivery?.tutor)
      : directTutor || cleanText(storedDelivery?.tutor);
    // The group owns its delivery days. One that has been given a slot naming no
    // day delivers on no day, and a day the module kept from whatever it was
    // created or copied from cannot fill that in — the group's own screens show
    // the delivery as empty, so a module quietly running on the copy is a
    // timetable nobody can see, and it books its tutor there. A group that has
    // never been given a slot at all is the other case, and still leaves the
    // module its own pattern.
    const parentGroupPattern = groupDeliveryPattern(parentGroup);
    const initialWeekDays = parentGroupPattern && !parentGroupPattern.days
      ? ''
      : canonicalDeliveryDays(record?.weekDays || storedDelivery?.weekDays || parentGroup?.weekDays || '');
    const initial = {
      name: cleanText(record?.name),
      programmeId: resolvedProgrammeId,
      cohortId: cleanText(parentCohort?.id) || cleanText(record?.cohortId) || cleanText(storedDelivery?.cohortId) || defaults?.cohortId || '',
      // The module's own group first: `primaryGroupId` below reads position 0 as
      // the delivery a PATCH belongs to when nothing more specific matches.
      groupIds: Array.from(new Set([
        cleanText(parentGroup?.id) || cleanText(record?.groupId) || cleanText(storedDelivery?.groupId) || defaults?.groupId || '',
        ...attachedGroupIds,
      ].filter(Boolean))),
      // Seeded from the authored week count only. Seeding from `sessionsNumber`
      // (or the delivery's `sessions`) put a delivery-day-multiplied number in the
      // Weeks box, which then saved back multiplied again on every round-trip.
      sessionsNumber: String(record?.deliveryWeeks || record?.weeks || record?.sessionsNumber || storedDelivery?.sessions || 1),
      sessionsPerWeek: String(Math.max(1, deliveryDayIndexes(initialWeekDays).length)),
      weekDays: initialWeekDays,
      weeklyTimes: Object.fromEntries((record?.weeklySchedule || storedDelivery?.weeklySchedule || []).map(slot => [slot.day, { startTime: slot.startTime, endTime: slot.endTime }])),
      groupStartTime: cleanText(record?.startTime || storedDelivery?.startTime)
        || groupDeliveryPattern(parentGroup)?.startTime
        || '',
      groupEndTime: cleanText(record?.endTime || storedDelivery?.endTime)
        || groupDeliveryPattern(parentGroup)?.endTime
        || '',
      startDate: cleanText(record?.startDate) || cleanText(storedDelivery?.startDate) || (module ? '' : cleanText(parentCohort?.startDate)),
      targetEndDate: cleanText(record?.endDate) || cleanText(storedDelivery?.endDate),
      // The module's own clock first, then the group's. The group's is taken
      // through `groupDeliveryPattern` rather than read raw: rows written before
      // the group schedule was split into columns kept a 12-hour clock, and a
      // "9:00 AM" reaching the time fields fails their `HH:MM` check, which left
      // the plan preview refusing to run on a group that had a perfectly good
      // timetable.
      // A new module inside a cohort starts when the cohort does â€” the same
      // default the backend falls back to when no start date is sent.
      tutor: normaliseKey(storedTutor) === UNASSIGNED ? '' : storedTutor,
      status: cleanText(record?.status) || 'draft',
      description: visibleNotes(record?.notes),
      color: record?.color || parentGroup?.color || '#2563eb',
      coverImage: cleanText(record?.coverImage),
    };
    const storedPattern = {
      sessionsPerWeek: initial.sessionsPerWeek,
      weekDays: initial.weekDays,
      groupStartTime: initial.groupStartTime,
      groupEndTime: initial.groupEndTime,
      weeklyTimes: initial.weeklyTimes,
    };
    if (verdict === 'seed') baseline.current = { ...initial, deliveryPattern: storedPattern };
    // A module counts as already assigned the moment any parent resolved above
    // -- from its own stored fields or from the delivery it was found through --
    // not from `defaults`, which only seeds a fresh create's pickers and is not
    // yet a saved placement.
    initiallyAssigned.current = Boolean(
      parentGroup || parentCohort
      || cleanText(record?.programmeId) || cleanText(record?.programme)
      || cleanText(record?.cohortId) || cleanText(record?.groupId)
      || cleanText(storedDelivery?.programmeId) || cleanText(storedDelivery?.cohortId) || cleanText(storedDelivery?.groupId),
    );
    // Always resets to 'now': the toggle is only offered on a fresh create (see
    // the render below), where 'now' is the pre-existing, regression-safe
    // default. Editing an unassigned module does not read this flag at all --
    // it relaxes the same required fields directly, from `initiallyAssigned`.
    if (verdict === 'seed') setAssignMode('now');
    // Shown by default on a fresh create (nothing to hide yet); collapsed by
    // default when opening a module that was already saved unassigned, so the
    // edit drawer reads as short as the create one did. A module that already
    // has a placement always shows the fields regardless of this flag -- see
    // where it is read below.
    if (verdict === 'seed') setRevealPlacement(!module);
    // `take` decides each field: the stored value where this reader has not
    // touched it, theirs where they have, theirs plus a sentence where both
    // changed it. On a plain seed every one of these is the stored value, which
    // is exactly what it always was.
    setName(coEdit.take(verdict, 'name', initial.name));
    setProgrammeId(coEdit.take(verdict, 'programmeId', initial.programmeId));
    setCohortId(coEdit.take(verdict, 'cohortId', initial.cohortId));
    setGroupIds(coEdit.take(verdict, 'groupIds', initial.groupIds));
    setSessionsNumber(coEdit.take(verdict, 'sessionsNumber', initial.sessionsNumber));
    const mergedPattern = coEdit.take(verdict, 'deliveryPattern', storedPattern);
    setSessionsPerWeek(mergedPattern.sessionsPerWeek);
    setWeekDays(mergedPattern.weekDays);
    setWeeklyTimes(mergedPattern.weeklyTimes);
    setGroupStartTime(mergedPattern.groupStartTime);
    setGroupEndTime(mergedPattern.groupEndTime);
    coEdit.sync(verdict, storedPattern);
    setStartDate(coEdit.take(verdict, 'startDate', initial.startDate));
    setTargetEndDate(coEdit.take(verdict, 'targetEndDate', initial.targetEndDate));
    setTutor(coEdit.take(verdict, 'tutor', initial.tutor));
    setNotifyTutor(false);
    setStatus(coEdit.take(verdict, 'status', initial.status));
    setDescription(coEdit.take(verdict, 'description', initial.description));
    setColor(coEdit.take(verdict, 'color', initial.color));
    setCoverImage(coEdit.take(verdict, 'coverImage', initial.coverImage));
    coEdit.publish();
  }, [allowSeed, attachedGroupIds, coEdit, cohorts, defaults?.cohortId, defaults?.groupId, defaults?.programmeId, groups, guard.stored, module, open, selectableProgrammes]);

  const programmeOptions = useMemo(
    () => selectableProgrammes.map(programme => ({ value: programmeIdentity(programme), label: programme.name })),
    [selectableProgrammes],
  );
  const availableCohorts = useMemo(
    () => cohortsForProgramme(cohorts, programmes, programmeId),
    [cohorts, programmeId, programmes],
  );
  // The cohort is what makes this list answerable: a group carries its cohort's
  // dates and holidays, so the placement only means something once the cohort is
  // named. `groupsForScope` would answer a programme-only scope with every group
  // under it -- right for a filter toolbar reading "All cohorts", wrong for a
  // picker where ticking one of them would place the module in a cohort the form
  // above has not chosen.
  const availableGroups = useMemo(
    () => (cohortId ? groupsForScope(groups, cohorts, programmes, { programmeId, cohortId }) : []),
    [cohortId, cohorts, groups, programmeId, programmes],
  );
  // The delivery the previews, the tutor check and (when editing) the PATCH are
  // about. Every other ticked group gets its own delivery on save, generated from
  // its own delivery days by the backend.
  const primaryGroupId = useMemo(
    () => groupIds.find(id => sameIdentifier(id, ownGroupId)) || groupIds[0] || '',
    [groupIds, ownGroupId],
  );
  const selectedGroups = useMemo(
    () => groupIds
      .map(id => groups.find(group => sameIdentifier(group.id, id)))
      .filter((group): group is CurriculumGroup => Boolean(group)),
    [groupIds, groups],
  );
  const selectedGroup = useMemo(
    () => groups.find(group => sameIdentifier(group.id, primaryGroupId)),
    [groups, primaryGroupId],
  );
  const selectedCohort = useMemo(
    () => cohorts.find(cohort => sameIdentifier(cohort.id, cohortId || selectedGroup?.cohortId)),
    [cohortId, cohorts, selectedGroup],
  );
  // Every cohort the ticked groups belong to. Usually one; the date window is
  // checked against all of them, because a module placed in two cohorts has to
  // fit inside both.
  const selectedCohorts = useMemo(() => {
    const seen = new Map<string, CurriculumCohort>();
    selectedGroups.forEach(group => {
      const cohort = cohorts.find(item => sameIdentifier(item.id, group.cohortId));
      if (cohort) seen.set(normaliseKey(cohort.id), cohort);
    });
    if (!seen.size && selectedCohort) seen.set(normaliseKey(selectedCohort.id), selectedCohort);
    return Array.from(seen.values());
  }, [cohorts, selectedCohort, selectedGroups]);

  // The holidays that move this module's sessions: every England bank holiday
  // from the parent cohort's start date onwards. The same set the backend skips
  // with (`scheduling_holidays_from`), so the end date this drawer projects is
  // the one the save stores.
  //
  // Not `cohort.holidayIds`, which is the narrower set inside the cohort's own
  // period: a plan pushed later by the holidays it stepped over finishes after
  // the cohort's practical end date, and the sessions that overshoot still have
  // to step over the bank holidays they land on.
  const cohortHolidays = useMemo(
    () => [...holidaysForScheduling(holidays, selectedCohort?.startDate, selectedCohort?.excludedHolidayIds), ...(module?.sessionHolidays || [])],
    [holidays, selectedCohort, module?.sessionHolidays],
  );

  // What the group this module is delivered by actually runs to -- which is
  // what the module runs to. The group states when its learners meet, and the
  // schedule reads it ahead of the module's own stored pattern, so this is
  // shown as the group's own value rather than folded silently into the fields
  // below: those are the module's answer for a group that states none.
  const groupPattern = useMemo(() => groupDeliveryPattern(selectedGroup), [selectedGroup]);
  const groupWeeklySchedule = useMemo<CurriculumWeeklySession[]>(() => (
    groupPattern?.days && groupPattern.startTime && groupPattern.endTime
      ? groupPattern.days.split(', ').map(day => ({ day, startTime: groupPattern.startTime, endTime: groupPattern.endTime }))
      : []
  ), [groupPattern]);

  // The end date is the backend's own session-plan calculation, so what the
  // drawer shows cannot drift from what the save stores.
  // Each selected day contributes one session per authored week. The module
  // starts with the group's timetable and can override it for this delivery.
  //
  // The group's own window is the fallback ahead of 09:00-10:00. That pair is a
  // last resort for a group that has no clock stored, not a default worth
  // applying over one that does -- reaching it for a group meeting 09:00-11:00
  // silently halved every session the module delivers.
  const weeklySchedule = useMemo<CurriculumWeeklySession[]>(() => canonicalDeliveryDays(weekDays).split(', ').filter(Boolean).map(day => ({
    day,
    startTime: weeklyTimes[day]?.startTime ?? (groupStartTime || groupPattern?.startTime || '09:00'),
    endTime: weeklyTimes[day]?.endTime ?? (groupEndTime || groupPattern?.endTime || '10:00'),
  })), [weekDays, weeklyTimes, groupStartTime, groupEndTime, groupPattern]);
  const validWeeklyTimes = weeklySchedule.every(slot => /^\d{2}:\d{2}$/.test(slot.startTime) && /^\d{2}:\d{2}$/.test(slot.endTime) && slot.endTime > slot.startTime);
  const deliveryDaysPerWeek = deliveryDayIndexes(weekDays).length;
  // Whether this module is still running to its group's pattern. Compared slot
  // by slot rather than on the day list alone: a module can meet on the group's
  // day and still have been given a different clock for it.
  const matchesGroupPattern = Boolean(
    groupPattern?.days
    && groupPattern.startTime
    && groupPattern.endTime
    && canonicalDeliveryDays(weekDays) === groupPattern.days
    && weeklySchedule.length > 0
    && weeklySchedule.every(slot => slot.startTime === groupPattern.startTime && slot.endTime === groupPattern.endTime),
  );

  /** This module's own timetable, said the way the group's is said above. */
  const modulePatternLabel = weeklySchedule.length
    ? weeklySchedule
      .map(slot => `${slot.day} ${clockLabel(slot.startTime)} - ${clockLabel(slot.endTime)}`)
      .join(' · ')
    : 'no delivery days yet';

  /**
   * Take the group's pattern for this module: its delivery day, its start time
   * and the length that follows from its end time.
   *
   * Deliberately an action rather than something the drawer does on open. The
   * weekday is part of the pattern, so adopting it re-dates every session the
   * module delivers -- and a stored module must never have its dates moved by
   * being looked at. Clearing the target end date hands the finish back to the
   * generated plan, which is what recalculates the dates from the new day and
   * the cohort's holidays.
   */
  const applyGroupPattern = () => {
    if (!groupPattern?.days || !groupPattern.startTime || !groupPattern.endTime) return;
    const days = groupPattern.days;
    setWeekDays(days);
    setSessionsPerWeek(String(Math.max(1, deliveryDayIndexes(days).length)));
    setWeeklyTimes(Object.fromEntries(days.split(', ').filter(Boolean).map(day => [
      day,
      { startTime: groupPattern.startTime, endTime: groupPattern.endTime },
    ])));
    setGroupStartTime(groupPattern.startTime);
    setGroupEndTime(groupPattern.endTime);
    setTargetEndDate('');
  };
  const scheduleComplete = !selectedGroup || deliveryDaysPerWeek === Number(sessionsPerWeek);
  const weeksEntered = Math.max(0, Number(sessionsNumber) || 0);
  const groupStartDate = cleanText(selectedGroup?.startDate) || cleanText(selectedCohort?.startDate);
  const groupTotalSessions = Math.round(weeksEntered * Math.max(1, groupWeeklySchedule.length));
  const totalSessions = Math.round(weeksEntered * Math.max(1, deliveryDaysPerWeek));
  // Everything the plan is built from, in one string: what the preview in state
  // was fetched for is compared against it below, so a plan left over from the
  // previous weeks value is never read as this one's answer.
  const planInputs = useMemo(
    () => JSON.stringify([startDate, totalSessions, weekDays, weeklySchedule, cohortHolidays.map(holiday => holiday.id)]),
    [cohortHolidays, startDate, totalSessions, weekDays, weeklySchedule],
  );
  useEffect(() => {
    if (!open || !startDate || weeksEntered <= 0 || !scheduleComplete || !validWeeklyTimes) { setPlan(null); setPlanFor(''); setPlanLoading(false); return undefined; }
    let active = true;
    setPlanLoading(true);
    const timer = setTimeout(() => {
      previewModuleSessionPlan({
        startDate,
        numberOfSessions: totalSessions,
        weeklySchedule,
        weekDays,
        holidays: cohortHolidays,
      })
        .then(result => { if (active) { setPlan(result); setPlanFor(planInputs); } })
        .catch(() => { if (active) { setPlan(null); setPlanFor(''); } })
        .finally(() => { if (active) setPlanLoading(false); });
    }, 300);
    return () => { active = false; clearTimeout(timer); };
  }, [cohortHolidays, open, planInputs, scheduleComplete, totalSessions, startDate, weekDays, weeklySchedule, validWeeklyTimes, weeksEntered]);

  // The module's own calendar span -- start date plus the whole weeks entered.
  // Independent of the generated session plan, which answers a different
  // question (where sessions land) and must never decide this field.
  const projectedEndDate = useMemo(
    () => projectModuleEndDate(startDate, weeksEntered),
    [startDate, weeksEntered],
  );
  const calculatedEndDate = projectedEndDate || '';
  const manualEndDate = cleanText(targetEndDate);
  // Manual wins: the generated plan can suggest an end date, but the drawer is
  // allowed to store the date the user picked.
  const endDate = manualEndDate || (weeksEntered > 0 ? calculatedEndDate || cleanText(module?.endDate) : '');
  // Either what the date is made of, or the backend's own sentence naming what
  // is still missing before it can be calculated.
  const endDateHelper = endDate
    ? manualEndDate
      ? calculatedEndDate && calculatedEndDate !== manualEndDate
        ? `Set by hand. ${sessionsNumber} week${Number(sessionsNumber) === 1 ? '' : 's'} from the start date would put it at ${formatDateLabel(calculatedEndDate)}.`
        : 'Set by hand. The weeks above were counted from it; change the weeks to recalculate the date instead.'
      : `${sessionsNumber} week${Number(sessionsNumber) === 1 ? '' : 's'} from the start date.`
    : 'Set the start date and the weeks to calculate it.';
  // The cohort's delivery window is the module's boundary, and the end date is
  // usually the generated plan's -- so this is checked on what the drawer will
  // actually send, not only on what was typed. Shown while the form is open and
  // enforced again on submit; the backend refuses the same three cases.
  // Split per field so each message lands on the date it is about; the two are
  // independent checks, so an out-of-window start does not hide a run-past end.
  const startDateError = selectedCohorts.length
    ? selectedCohorts.map(cohort => moduleCohortDateError(cohort, startDate, undefined)).find(Boolean) || null
    : moduleCohortDateError(selectedCohort, startDate, undefined);
  const endDateError = selectedCohorts.length
    ? selectedCohorts.map(cohort => moduleCohortDateError(cohort, undefined, endDate)).find(Boolean) || null
    : moduleCohortDateError(selectedCohort, undefined, endDate);
  const dateWindowError = startDateError || endDateError;
  const cohortSoftStart = moduleSoftStartDate(selectedCohort?.startDate) || cleanText(selectedCohort?.startDate);
  // Not a refusal -- a weekend or bank-holiday start is still saved -- just a
  // heads-up, since the module will not actually deliver on that day.
  const startDateNotice = useMemo(() => describeNonDeliveryDate(startDate, cohortHolidays), [startDate, cohortHolidays]);
  // ==========================================================================
  // Tutor availability, asked while the tutor is still being picked.
  //
  // The save enforces the rule and always has. Finding out *from* the refusal is
  // the worst moment to find out: the form is filled in, Create has been pressed
  // and the choice was never going to work. So the same question is asked of the
  // same rule as soon as the slot can be dated, and the answer annotates the
  // picker rather than replacing the page with a sentence.
  //
  // Check the module's chosen days and times, including its cohort holidays.
  // These are the same fields the save uses.
  // ==========================================================================
  const conflictSlot = useMemo(() => (
    groupWeeklySchedule.length > 0 && groupStartDate && groupTotalSessions > 0
      ? {
        startDate: groupStartDate,
        sessionsNumber: groupTotalSessions,
        weekDays: groupPattern?.days || undefined,
        weeklySchedule: groupWeeklySchedule,
        startTime: groupWeeklySchedule[0]?.startTime || undefined,
        endTime: groupWeeklySchedule[0]?.endTime || undefined,
        cohortId: cohortId || cleanText(selectedGroup?.cohortId) || undefined,
        holidays: cohortHolidays,
        // Editing: the module must not be reported as blocking its own slot.
        moduleCatalogueId: module?.id || undefined,
      }
      : null
  ), [cohortHolidays, cohortId, groupPattern?.days, groupStartDate, groupTotalSessions, groupWeeklySchedule, module?.id, selectedGroup?.cohortId]);
  // Destructured rather than used through the hook's return value, which is a
  // fresh object every render: the memos below key off the parts that actually
  // move, so a keystroke in the name field does not rebuild the picker.
  const { verdictFor, bookable, sessionDates, loading: checkingTutors, failed: tutorCheckFailed } = useTutorAvailability(
    conflictSlot,
    { enabled: open },
  );
  const tutorVerdict = verdictFor(tutor);
  const tutorClash = tutorVerdict && !tutorVerdict.available ? tutorVerdict : null;
  const tutorVerdicts = useMemo(
    () => tutorNames.map(tutorName => ({ tutorName, verdict: verdictFor(tutorName) })),
    // `verdictFor` closes over the roster, so it changes exactly when the answers
    // do -- which is the dependency this list has.
    [tutorNames, verdictFor],
  );
  // Named on the notice as one-click swaps, so the warning carries its own fix.
  const freeTutorNames = useMemo(() => (
    bookable
      ? tutorVerdicts.filter(item => item.verdict?.available).map(item => item.tutorName).slice(0, 4)
      : []
  ), [bookable, tutorVerdicts]);
  const tutorOptions = useMemo<SelectOption[]>(() => {
    const options = tutorVerdicts.map(({ tutorName, verdict }): SelectOption & { rank: number } => {
      if (verdict?.available) {
        return { value: tutorName, label: tutorName, group: 'Free in this slot', rank: 0 };
      }
      if (verdict) {
        const clash = verdict.conflicts[0];
        const also = verdict.conflicts.length - 1;
        return {
          value: tutorName,
          label: tutorName,
          // The reason travels with the row: picking a busy name is still allowed --
          // the delivery day or the time may be what moves instead -- but not by
          // accident.
          description: `${clash.moduleName} ${clash.startTime}-${clash.endTime}${also > 0 ? ` +${also} more` : ''}`,
          meta: `${verdict.conflicts.reduce((count, item) => count + item.dates.length, 0)} clashes`,
          icon: 'ri-error-warning-line',
          group: 'Already teaching then',
          rank: 1,
        };
      }
      // No verdict is not a free verdict. Once there is an answer to group by (or
      // the check itself failed), a name it does not cover is said to be
      // unchecked; before then the list stays ungrouped, since headings over an
      // unchecked list would say "free" about names nobody has checked.
      if (bookable || tutorCheckFailed) {
        return {
          value: tutorName,
          label: tutorName,
          description: tutorCheckFailed ? 'The availability check failed' : 'Not covered by the availability check',
          icon: 'ri-question-line',
          group: 'Not verified',
          rank: 2,
        };
      }
      return { value: tutorName, label: tutorName, rank: 0 };
    });
    // The menu prints a heading whenever the group changes from one row to the
    // next, so the names are kept together by status (alphabetical within it);
    // left in name order the headings repeated down the list.
    return options
      .sort((left, right) => left.rank - right.rank)
      .map(({ rank: _rank, ...option }) => option);
  }, [bookable, tutorCheckFailed, tutorVerdicts]);
  // The field says what it knows: which slot the names were checked against, or
  // what is still missing before anything can be checked. "Checked before it
  // saves" was the old promise, and it is now kept before the save, not by it.
  // The group holds a clock but no weekday, so there is no day for its sessions
  // to fall on: nothing can be dated, nobody can be checked, and a tutor put
  // against it would be teaching on a timetable no screen shows. The day belongs
  // to the group, so this names the group rather than pointing at the Delivery
  // days control above -- that control is the module's answer for a group that
  // states no slot at all, and a grouped module does not save through it.
  const groupDeliveryDayMissing = Boolean(selectedGroup && groupPattern && !groupPattern.days);
  // Whether this drawer is putting a name against the module rather than
  // leaving the one it already had. A create always is.
  const assigningTutor = Boolean(cleanText(tutor)) && (!module || cleanText(tutor) !== cleanText(baseline.current?.tutor ?? ''));

  // ---- "has this tutor been told?" -----------------------------------------
  // The tick below is a status control, not a form field: it reports what the
  // assignment ledger holds for THIS tutor and THIS module's deliveries, and it
  // has to still report it correctly a week later. So it is read from the
  // server on every open rather than remembered here -- a boolean kept in the
  // drawer would be wrong the moment anyone saved from anywhere else, and gone
  // the moment the drawer closed.
  const selectedTutorName = cleanText(tutor);
  // Deliveries that already exist and could therefore already have been
  // emailed: the one this drawer edits, plus the module's other deliveries (one
  // row per group it runs for).
  const existingDeliveryIds = useMemo(() => {
    const ids = new Set<string>();
    const own = cleanText(module?.id);
    if (own) ids.add(own);
    (module?.deliveryUsages || []).forEach(usage => {
      const id = cleanText(usage.deliveryModuleId);
      if (id) ids.add(id);
    });
    return Array.from(ids);
  }, [module?.id, module?.deliveryUsages]);
  // Compared by value, not identity: the parent rebuilds `deliveryUsages` on
  // every refresh, and an identity-keyed effect would re-ask on each one.
  const deliveryIdsKey = existingDeliveryIds.join(',');

  useEffect(() => {
    const ids = deliveryIdsKey ? deliveryIdsKey.split(',') : [];
    if (!open || !selectedTutorName || !ids.length) {
      setTutorEmailStatus(null);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    fetchTutorAssignmentEmailStatus(ids, selectedTutorName, controller.signal)
      .then(status => { if (!cancelled) setTutorEmailStatus(status); })
      // The tick falls back to "not emailed", which is the safe way to be
      // wrong: it offers to send rather than claiming somebody was told.
      .catch(() => { if (!cancelled) setTutorEmailStatus(null); });
    return () => { cancelled = true; controller.abort(); };
  }, [open, selectedTutorName, deliveryIdsKey]);

  // Deliveries this save would create, which cannot have been emailed yet.
  // Counting them is what makes a fully-notified module go back to "2 of 3"
  // the moment another group is ticked.
  const pendingDeliveryCount = module
    // Editing: the groups ticked on top of the ones this module already runs
    // for. Same rule the save uses to decide what to attach.
    ? selectedGroups.filter(group => (
      !sameIdentifier(group.id, primaryGroupId)
        && !attachedGroupIds.some(id => sameIdentifier(id, group.id))
    )).length
    // Creating: every ticked group becomes a delivery.
    : selectedGroups.length;
  const emailedDeliveries = tutorEmailStatus?.emailed ?? 0;
  const totalDeliveries = (tutorEmailStatus?.total ?? 0) + pendingDeliveryCount;
  // Every delivery this tutor could be told about has been. Nothing to send, so
  // the tick stops being a control and simply says so.
  const tutorFullyEmailed = totalDeliveries > 0 && emailedDeliveries === totalDeliveries;
  // Some told, some not -- the drawer must not claim either.
  const tutorPartlyEmailed = emailedDeliveries > 0 && emailedDeliveries < totalDeliveries;
  const tutorEmailBoxRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (tutorEmailBoxRef.current) {
      tutorEmailBoxRef.current.indeterminate = tutorPartlyEmailed && !notifyTutor;
    }
  }, [tutorPartlyEmailed, notifyTutor, selectedTutorName]);
  const groupDeliveryDayMessage = groupDeliveryDayMissing
    ? `${cleanText(selectedGroup?.name) || 'This module’s group'} has no delivery day, so this module has no sessions to place a tutor on. Set the delivery day on the group, then come back and choose the tutor.`
    : '';
  const tutorHint = checkingTutors
    ? 'Checking who is already teaching in this slot...'
    : tutorCheckFailed
      ? 'Could not check who is free in this slot. Saving still checks the tutor for clashes.'
      : bookable
        ? `Checked against ${sessionDates.length} session${sessionDates.length === 1 ? '' : 's'} using each day's scheduled time.`
        : groupDeliveryDayMissing
          ? `${cleanText(selectedGroup?.name) || 'The group'} has no delivery day yet.`
          : 'Select a Group and set the weeks to check who is free.';

  const changeProgramme = (value: string) => {
    setProgrammeId(value);
    setCohortId('');
    changeGroups([]);
  };
  // Weeks and dates are two views of the same span, and either one can be the
  // one the user knows: a module planned as "eight weeks from the 3rd" is typed
  // as weeks, one contracted to finish on a fixed date is typed as dates. So the
  // drawer solves for whichever field was not just touched.
  //
  //   * weeks moved      -> the end date is recalculated (the manual one drops);
  //   * end date moved   -> the weeks are recalculated from start -> end;
  //   * start date moved -> the weeks stay and the end date is recalculated
  //                         from them (the manual one drops). Moving a run is
  //                         not shortening it: an edited module's saved end
  //                         date loads as a manual one, and holding it here
  //                         turned a five-week module into a one-week one.
  const weeksBetweenDates = (from: string, to: string) => projectModuleWeeks(from, to);
  const changeStartDate = (value: string) => {
    setStartDate(value);
    setTargetEndDate('');
  };
  const changeWeeks = (value: string) => {
    setSessionsNumber(value);
    setTargetEndDate('');
  };
  const changeEndDate = (value: string) => {
    setTargetEndDate(value);
    const recalculated = weeksBetweenDates(startDate, value);
    if (recalculated > 0) setSessionsNumber(String(recalculated));
  };
  const changeCohort = (value: string) => {
    setCohortId(value);
    changeGroups([]);
    const cohort = cohorts.find(item => sameIdentifier(item.id, value));
    // Only seeds an empty field: a date the user already picked stays put.
    if (!startDate && cohort?.startDate) changeStartDate(cohort.startDate);
  };
  const changeGroups = (next: string[]) => {
    setGroupIds(next);
    const nextPrimaryId = next.find(id => sameIdentifier(id, ownGroupId)) || next[0] || '';
    const group = groups.find(item => sameIdentifier(item.id, nextPrimaryId));
    if (group?.cohortId && !cohortId) setCohortId(group.cohortId);
    if (!sameIdentifier(nextPrimaryId, primaryGroupId)) {
      const pattern = groupDeliveryPattern(group);
      const nextDays = canonicalDeliveryDays(group?.weekDays || '');
      setWeekDays(nextDays);
      setWeeklyTimes({});
      setSessionsPerWeek(String(Math.max(1, deliveryDayIndexes(nextDays).length)));
      // Normalised, for the same reason the initial seed above normalises it.
      setGroupStartTime(pattern?.startTime || '');
      setGroupEndTime(pattern?.endTime || '');
      setTargetEndDate('');
    }
    // A module saved unassigned carries a planned end date typed by hand -- there
    // were no delivery days to count it from. The moment it gets a group, that
    // group's days and its cohort's ticked holidays decide where the sessions
    // land, so the held date is released and the generated plan takes over.
    // Only for a module that had no delivery days: an end date set by hand
    // against a group that already exists is still the user's to keep.
    if (!weekDays && cleanText(group?.weekDays)) setTargetEndDate('');
  };

  // Attached-but-out-of-scope groups are appended rather than dropped: a module
  // already running for a group in another cohort must still be visible here, or
  // the list would silently claim it does not.
  const groupOptions = useMemo<MultiSelectOption[]>(() => {
    const rows = [...availableGroups];
    [ownGroupId, ...attachedGroupIds].forEach(id => {
      if (!id || rows.some(group => sameIdentifier(group.id, id))) return;
      const group = groups.find(item => sameIdentifier(item.id, id));
      if (group) rows.push(group);
    });
    return rows.map(group => {
      const cohort = cohorts.find(item => sameIdentifier(item.id, group.cohortId));
      const schedule = cleanText(group.weekDays);
      const alreadyRuns = attachedGroupIds.some(id => sameIdentifier(id, group.id));
      return {
        value: group.id,
        label: group.name,
        description: [cleanText(cohort?.name), schedule, cleanText(group.coach)].filter(Boolean).join(' · ') || undefined,
        badge: alreadyRuns ? 'Already runs this' : undefined,
        locked: alreadyRuns,
      };
    });
  }, [attachedGroupIds, availableGroups, cohorts, groups, ownGroupId]);

  // Named so the reader knows the previews below are one group's, not all of them.
  const otherGroupCount = selectedGroups.filter(group => !sameIdentifier(group.id, primaryGroupId)).length;

  // A save is allowed to leave the module unplaced -- no programme, cohort or
  // group -- either because the person just chose "Assign later" on a fresh
  // create, or because they are editing a module that was already saved that
  // way and have not assigned it in this visit either. Once a module carries a
  // stored placement, editing it always requires the full set again: this only
  // relaxes the *first* placement, never removes one that already exists.
  const placementOptional = !lockGroup && (
    (!module && assignMode === 'later')
    || (Boolean(module) && !initiallyAssigned.current)
  );
  // Whether the Programme/Cohort/Group picker is actually on screen. Not
  // rendering it is the point of "Assign later" -- an optional-but-visible
  // field still reads as something to fill in. `revealPlacement` is the one
  // escape hatch: the toggle flips it for a fresh create, and a plain link
  // flips it when editing an already-unassigned module (see the render below).
  const showPlacement = !lockGroup && (!placementOptional || revealPlacement);
  // The Weeks/dates/tutor block is a delivery's fields, not the module's -- it
  // stays hidden for exactly as long as the placement picker above does, since
  // there is nothing to schedule until a group can even be ticked.
  const showSchedule = lockGroup || showPlacement;

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) { setError('Give the module a name.'); return; }
    if (!lockGroup && !placementOptional) {
      if (!programmeId) { setError('Choose the programme this module belongs to.'); return; }
      if (!cohortId) { setError('Choose the cohort this module runs for.'); return; }
      if (!groupIds.length) { setError('Tick at least one group to run this module.'); return; }
    }
    // Weeks, dates and a tutor are a delivery's fields, not the module's -- they
    // only mean something once at least one group is actually being attached.
    // An unplaced module (no group ticked, whichever way it got there) saves
    // with none of them.
    if (selectedGroups.length) {
      if (!(Number(sessionsNumber) >= 1)) { setError('Set how many weeks the module runs for.'); return; }
      if (!startDate) { setError('Choose the module start date.'); return; }
      if (!endDate) { setError('Choose the module end date.'); return; }
      if (!tutor) { setError('Choose the tutor who delivers this module.'); return; }
      // Pre-empted rather than sent, for the same reason the clash below is: the
      // save refuses this and the sentence is already on screen under Tutor.
      // Only when this drawer is actually assigning the tutor — a module that
      // already carries one is not frozen out of being renamed or re-dated,
      // which is the line the save draws too.
      if (groupDeliveryDayMissing && assigningTutor) { setError(groupDeliveryDayMessage); return; }
      // Pre-empted rather than sent: the save enforces this and would refuse, so
      // firing it only trades an instant answer for a round-trip and the same
      // refusal. The clash itself is spelled out under the Tutor field, so this is
      // the pointer to it rather than a second copy of it.
      if (tutorClash) {
        setError(
          `${tutorClash.tutor} is already teaching in this slot. Change the tutor, the delivery day or the time - the clashing sessions are listed under Tutor.`,
        );
        return;
      }
    }
    const weeks = Math.max(1, Math.round(Number(sessionsNumber) || 1));
    const primaryWeekDays = canonicalDeliveryDays(selectedGroup?.weekDays || '');
    const sessions = Math.round(weeks * Math.max(1, deliveryDayIndexes(primaryWeekDays).length));

    const programme = programmes.find(item => sameIdentifier(programmeIdentity(item), programmeId))
      || programmes.find(item => sameIdentifier(item.name, programmeId));

    // Handed back with every save so a chain can report the parents this form
    // resolved, whichever of them the person picked here rather than created.
    const savedParents = () => ({
      programmeId: programme ? programmeIdentity(programme) : cleanText(programmeId),
      programmeName: cleanText(programme?.name),
      cohortId: cleanText(cohortId) || cleanText(selectedCohort?.id),
      cohortName: cleanText(selectedCohort?.name),
      groupIds: selectedGroups.map(group => cleanText(group.id)).filter(Boolean),
      groupNames: selectedGroups.map(group => cleanText(group.name)).filter(Boolean),
    });

    // Every delivery this save put the tutor on, in the order they were made.
    // One save can attach the module to several groups at once, and the tutor
    // should hear about that once rather than three times in the same second --
    // so the ids are collected here and sent together.
    const notifyModuleIds: string[] = [];
    const rememberDelivery = (id: unknown) => {
      const value = cleanText(id);
      if (value && !notifyModuleIds.includes(value)) notifyModuleIds.push(value);
    };
    const deliveryIdFrom = (result: { created?: unknown[]; updatedModules?: unknown[] } | undefined) => {
      const saved = ((result?.created || [])[0] || (result?.updatedModules || [])[0] || {}) as Record<string, unknown>;
      return String(saved.moduleCatalogueId || saved.catalogueId || saved.structureId || saved.id || '');
    };

    /**
     * Tell the tutor, if the person saving ticked the box.
     *
     * Runs after the save has committed and never throws. A module that saved
     * but did not mail is a notification problem, not a lost edit -- the same
     * line the backend's own notifier draws ("nothing here may break a
     * curriculum save") -- so a failure is reported on its own and the save
     * still reads as done.
     *
     * Returns a sentence to append to the save's confirmation, or ''.
     */
    const notifyTutorIfAsked = async () => {
      // `tutorFullyEmailed` can only have been true while the box was ticked if
      // the ledger answered after the tick -- there is nothing left to send.
      if (!notifyTutor || tutorFullyEmailed || !notifyModuleIds.length) return '';
      try {
        const result = await sendTutorAssignmentEmail(notifyModuleIds);
        if (!result?.sent) return '';
        const count = Number(result.modules) || notifyModuleIds.length;
        return count > 1
          ? ` ${result.tutor} was emailed about all ${count} deliveries.`
          : ` ${result.tutor} was emailed about it.`;
      } catch (err) {
        // Offered here rather than pointed somewhere else: the author chose to
        // notify the tutor in this drawer, so this is where they get to try
        // again. `showCurriculumConfirm` keeps the dialog open and reports the
        // reason in place when a retry also fails, so repeated attempts cost
        // one click each and never lose the module that is already saved.
        const reason = err instanceof Error && err.message
          ? err.message
          : 'The email could not be sent.';
        let sentOnRetry = '';
        await showCurriculumConfirm({
          title: 'The tutor was not emailed',
          text: `${reason} ${trimmed} is saved either way — would you like to try sending it again?`,
          icon: 'warning',
          confirmButtonText: 'Try again',
          cancelButtonText: 'Not now',
          onConfirm: async () => {
            const retried = await sendTutorAssignmentEmail(notifyModuleIds);
            const count = Number(retried?.modules) || notifyModuleIds.length;
            sentOnRetry = count > 1
              ? ` ${retried.tutor} was emailed about all ${count} deliveries.`
              : ` ${retried.tutor} was emailed about it.`;
          },
          successTitle: 'Tutor emailed',
          successText: `${selectedTutorName} now has this module's dates and schedule.`,
        });
        return sentOnRetry;
      }
    };

    /**
     * One group's delivery of this module.
     *
     * The primary delivery uses this form's schedule. Additional groups use
     * their own defaults and holidays; only the primary end date was previewed.
     */
    const attachToGroup = (group: CurriculumGroup) => {
      const groupWeekDays = canonicalDeliveryDays(group.weekDays || '');
      const groupDeliveryDays = deliveryDayIndexes(groupWeekDays).length;
      const groupCohort = cohorts.find(item => sameIdentifier(item.id, group.cohortId));
      const pattern = groupDeliveryPattern(group);
      const groupSchedule = pattern?.days && pattern.startTime && pattern.endTime
        ? pattern.days.split(', ').map(day => ({ day, startTime: pattern.startTime, endTime: pattern.endTime }))
        : [];
       return createGroupModule(group.id, {
        moduleName: trimmed,
        programmeId: programme ? programmeIdentity(programme) : programmeId,
        cohortId: cleanText(group.cohortId) || cohortId,
        groupId: group.id,
         // Dates are owned by the module form. A group's day and clock still
         // determine delivery, but changing the group window must not replace
         // this module's explicitly chosen start/end dates.
         startDate: startDate || undefined,
         endDate: endDate || undefined,
        sessionsNumber: Math.round(weeks * Math.max(1, groupDeliveryDays)),
        weeks,
        allowTutorConflict: overrideGroupIds.current.has(group.id) || undefined,
        tutor: tutor || undefined,
        weeklySchedule: groupSchedule,
        weekDays: groupWeekDays || undefined,
        startTime: pattern?.startTime || undefined,
        endTime: pattern?.endTime || undefined,
        color,
        coverImage,
        notes: description,
        holidays: holidaysForScheduling(holidays, groupCohort?.startDate, groupCohort?.excludedHolidayIds),
      }) as Promise<{ created?: Array<Record<string, unknown>>; updatedModules?: Array<Record<string, unknown>> }>;
    };

    setSaving(true);
    setError(null);
    setTutorConflictGroup(null);
    let failingGroup: CurriculumGroup | null = null;
    try {
      if (module) {
        // Only sent when this drawer actually changed it. The Weeks box is
        // seeded from the module's stored week count and falls back to 1 when
        // the module carries none -- an imported module, most often -- and
        // sending that 1 back told the save to resize the module to one week.
        // An untouched box must leave the authored weeks exactly as they are.
        const weeksChanged = String(sessionsNumber).trim() !== String(baseline.current?.sessionsNumber ?? '').trim();
        const primaryPattern = groupDeliveryPattern(selectedGroup);
        const primaryWeekDays = canonicalDeliveryDays(selectedGroup?.weekDays || '');
        const primarySchedule = primaryPattern?.days && primaryPattern.startTime && primaryPattern.endTime
          ? primaryPattern.days.split(', ').map(day => ({ day, startTime: primaryPattern.startTime, endTime: primaryPattern.endTime }))
          : [];
        const patchPayload = {
          name: trimmed,
          notes: description,
          status,
          color,
          coverImage,
          // The two counts are sent apart, because they mean different things and
          // different features read them: `weeks` is what the week builder
          // authors and what the catalogue shows as "N weeks"; `sessionsNumber`
          // is the calendar total (weeks x the group's delivery days) that the
          // session dates, the tutor conflict check and the Teams series run on.
          sessionsNumber: sessions,
          ...(weeksChanged ? { weeks } : {}),
          // Dates belong to the module. The selected group supplies only the
          // delivery day and clock shown above, so changing a group does not
          // silently rewrite this module's own calendar window.
          startDate: startDate || undefined,
          endDate: endDate || undefined,
          tutor,
          programmeId: programme ? programmeIdentity(programme) : programmeId || undefined,
          programmeName: programme?.name || undefined,
          cohortId: cohortId || undefined,
          cohortName: selectedCohort?.name || undefined,
          groupId: primaryGroupId || undefined,
          groupName: selectedGroup?.name || undefined,
          weeklySchedule: primarySchedule,
          weekDays: primaryWeekDays || undefined,
          startTime: primaryPattern?.startTime || undefined,
          endTime: primaryPattern?.endTime || undefined,
        };
        // The server resizes the stored weeks to the count this sends. Said
        // first, so the author decides -- "No" saves nothing at all.
        const weekChange = weekCountChangeForSave(storedStructure?.() ?? null, weeksChanged ? weeks : undefined);
        if (weekChange && !(await showCurriculumConfirm({
          title: `This module will have ${weekChange.after} weeks instead of ${weekChange.before}`,
          text: weekCountChangeText(weekChange, weeksChanged),
          confirmButtonText: 'Yes, save',
          cancelButtonText: 'No, change nothing',
          onConfirm: async () => undefined,
        }))) return;
        // The PATCH merges onto the stored structure, so only what this form
        // owns is sent: the weeks, components and KSB mappings authored in the
        // Module Builder are left exactly as they are.
        const outcome = await guard.save(
          expectedRevision => updateCurriculumModule(module.id, guardedInput(patchPayload, expectedRevision)),
        );
        // Somebody saved first -- in this drawer or in the Module Builder. Their
        // module is merging into this form now and the retry sends the result;
        // nothing has been written, so no group attachment has run either.
        if (!outcome.saved) return;
        const patchResult = outcome.result;
        // Named by the save when the edit moved the dates the module's Teams
        // series was built from. Empty for a module with no calendar, which is
        // most of them.
        const staleTeamsCalendars = patchResult?.teamsCalendarsToUpdate || [];
        // Groups ticked on top of the module's own: each gets a delivery of its
        // own rather than sharing this one, so its dates come from its own
        // delivery days and its tutor booking is checked against its own slot.
        // Sequential on purpose -- the backend compares each attachment against
        // the ones already accepted, which a parallel burst would defeat.
        const newGroups = selectedGroups.filter(group => (
          !sameIdentifier(group.id, primaryGroupId)
          && !attachedGroupIds.some(id => sameIdentifier(id, group.id))
        ));
        // Every delivery of this module, not only the ones this save touched:
        // the tick covers the whole assignment in one message, which is what
        // "2 of 3 emailed" offers to finish.
        existingDeliveryIds.forEach(rememberDelivery);
        for (const group of newGroups) {
          if (attachedThisSession.current.has(group.id)) continue;
          failingGroup = group;
          rememberDelivery(deliveryIdFrom(await attachToGroup(group)));
          attachedThisSession.current.add(group.id);
        }
        // The caller's refresh runs BEFORE the drawer closes, so `saving` keeps the
        // spinner up and the buttons dimmed until the list actually holds the new
        // numbers. Closing first left a window -- as long as the round-trip, which
        // is seconds on a slow connection -- where reopening the drawer offered the
        // pre-save weeks and saving again wrote them straight back.
        await onSaved({ catalogueId: module.id, name: trimmed, created: false, ...savedParents() });
        const editedEmailNote = await notifyTutorIfAsked();
        // In a chain the wizard owns closing and confirming, so that a run of
        // four steps says what it created once rather than four times.
        if (chained) return;
        onClose();
        // See the group drawer: the warning stands in for the confirmation, so a
        // save that has left the calendar behind does not read as finished.
        const warned = await confirmTeamsCalendarUpdate({
          calendars: staleTeamsCalendars,
          savedText: `${trimmed} is saved.`,
          navigate,
          // The group's hour and the weeks just saved, so a push from the dialog
          // sends this form's own plan rather than re-deriving one.
          sessionTimes: { startTime: primaryPattern?.startTime || '', endTime: primaryPattern?.endTime || '' },
          weeks: weeksEntered,
        });
        if (!warned) {
          await showCurriculumAlert({
            title: 'Module updated',
            text: (newGroups.length
              ? `${trimmed} is saved, and now also runs for ${newGroups.map(group => group.name).join(', ')}.`
              : `${trimmed} is saved.`) + editedEmailNote,
            timer: newGroups.length || editedEmailNote ? 2600 : 1800,
          });
        }
        return;
      }

      if (selectedGroups.length) {
        // Attaching to a group is the endpoint that owns the delivery rules:
        // cohort date bounds, the session plan and the tutor conflict check. It
        // is called once per group, in order, so a clash between two of the new
        // deliveries is caught by the same rule as a clash with a stored one.
        const ordered = [
          ...selectedGroups.filter(group => sameIdentifier(group.id, primaryGroupId)),
          ...selectedGroups.filter(group => !sameIdentifier(group.id, primaryGroupId)),
        ];
        // Groups where the save reattached a module that was already there under
        // this name rather than creating a new one. The endpoint dedupes by
        // title within the group, so pressing Create on an existing name
        // rewrites that module's dates, tutor and weeks -- saying "created"
        // there sent people looking for a second module that does not exist.
        const reattached: string[] = [];
        for (const group of ordered) {
          if (attachedThisSession.current.has(group.id)) continue;
          failingGroup = group;
          const result = await attachToGroup(group);
          const saved = (result.created || [])[0] || (result.updatedModules || [])[0] || {};
          if (!(result.created || []).length && (result.updatedModules || []).length) reattached.push(group.name);
          const id = String(saved.moduleCatalogueId || saved.catalogueId || saved.structureId || saved.id || '');
          if (!createdCatalogueId.current) createdCatalogueId.current = id;
          rememberDelivery(id);
          attachedThisSession.current.add(group.id);
        }
        if (!chained) onClose();
        await onSaved({ catalogueId: createdCatalogueId.current, name: trimmed, created: true, ...savedParents() });
        const createdEmailNote = await notifyTutorIfAsked();
        if (!chained && reattached.length) {
          await showCurriculumAlert({
            title: 'Existing module updated',
            text: `${trimmed} already ran for ${reattached.join(', ')}, so that module was updated with these dates and tutor rather than a second one being created. Its weeks and components are untouched.${createdEmailNote}`,
            timer: 4200,
          });
          return;
        }
        if (!chained && ordered.length > 1) {
          // Worth saying out loud: one press produced one delivery per group, and
          // each of them is authored and scheduled separately from here on.
          await showCurriculumAlert({
            title: 'Module created for each group',
            text: `${trimmed} now runs for ${ordered.map(group => group.name).join(', ')}. Each group has its own dates and tutor.${createdEmailNote}`,
            timer: 3000,
          });
        } else if (!chained && createdEmailNote) {
          // A single-group create has no confirmation of its own, so the only
          // place the send can be reported is one of its own.
          await showCurriculumAlert({
            title: 'Module created',
            text: `${trimmed} is saved.${createdEmailNote}`,
            timer: 2600,
          });
        }
        return;
      }

      // No group yet: a catalogue draft. It still carries the programme (and the
      // cohort when one was chosen) so it is findable, and it can be placed in a
      // group from this same drawer later.
      const created = await createNewModule({
        title: trimmed,
        description,
        programme: programme?.name || programmeId || 'Unassigned programme',
        programmeId: programme ? programmeIdentity(programme) : programmeId,
        cohortId: cohortId || '',
        cohortName: selectedCohort?.name || '',
        weeks,
        sessionsNumber: sessions,
        startDate: startDate || '',
        endDate: endDate || '',
        status: 'draft',
        coverImage,
      });
      if (!chained) onClose();
      await onSaved({ catalogueId: created.catalogueId || created.id, name: trimmed, created: true, ...savedParents() });
    } catch (err) {
      // A tutor already booked in that slot is reported by the backend as a
      // sentence worth showing verbatim -- and so is every other refusal, such
      // as a module whose programme has been archived. `curriculumErrorMessage`
      // digs that sentence out of the response; falling through to
      // `err.message` instead showed the reader "Curriculum API returned 400
      // for /curriculum/modules/MOD-…/" with the sentence buried after it.
      setError(
        tutorConflictMessage(err)
        || curriculumErrorMessage(err, err instanceof Error ? err.message : 'The module could not be saved.'),
      );
      if (isTutorConflictError(err) && failingGroup) {
        setTutorConflictGroup({ groupId: failingGroup.id, groupName: failingGroup.name });
      }
    } finally {
      setSaving(false);
    }
  };

  // See the cohort drawer: the retry has to send what the merge produced, which
  // only exists after the render the merge caused.
  const submitRef = useRef(submit);
  submitRef.current = submit;
  useRecordRebaseRetry(guard.rebase, submitRef);

  const bookConflictAnyway = () => {
    if (!tutorConflictGroup) return;
    overrideGroupIds.current.add(tutorConflictGroup.groupId);
    setTutorConflictGroup(null);
    void submit();
  };

  const placementHint = selectedGroups.length > 1
    ? `Tick as many groups as run this module. Each gets its own delivery from its own days and holidays; the dates and tutor below are ${selectedGroup?.name || 'the first group'}'s.`
    : selectedGroups.length === 1
      ? 'Saved against this group, with its delivery days and holidays. Tick another group to run the same module for it too.'
      : cohortId
        ? 'Saved against this cohort. Tick one or more groups to give it delivery dates and a tutor.'
        : 'Groups appear once a cohort is chosen. Tick the ones that run this module.';

  return (
    <EntityDrawer
      open={open}
      title={module ? 'Edit module' : 'Add module'}
      subtitle={module
        ? 'Placement, module dates and tutor. Delivery days and times are read from the selected Group.'
        : 'Where the module lives. Set its dates here; delivery days and times come from the selected Group.'}
      banner={chain?.banner}
      onClose={onClose}
      onSubmit={submit}
      closeConfirm={chain?.closeConfirm}
      submitLabel={chain?.submitLabel || (module ? 'Save module' : 'Create module')}
      cancelLabel={chain?.cancelLabel}
      extraAction={chain?.extraAction}
      backAction={chain?.backAction}
      width={chain?.width || 'w-[760px]'}
      saving={saving}
      error={error}
      dirty={dirty}
    >
      <CoEditNotice notices={coEdit.notices} onDismiss={coEdit.dismiss} />
      {!module && !lockGroup && !chained && (
        <FormField as="group" label="When to assign" hint={
          assignMode === 'later'
            ? 'Creates the module on its own, with its weeks and dates but no programme, cohort or group. Assign it later, from Edit.'
            : 'Choose the programme, cohort and group below now.'
        }>
          <div className="inline-flex rounded-lg border border-background-200 bg-background-50 p-1">
            {(['now', 'later'] as const).map(mode => (
              <button
                key={mode}
                type="button"
                aria-pressed={assignMode === mode}
                onClick={() => { setAssignMode(mode); setRevealPlacement(mode === 'now'); }}
                className={`h-8 rounded-md px-3 text-[12px] font-bold transition-smooth ${
                  assignMode === mode
                    ? 'bg-primary-600 text-white shadow-sm'
                    : 'text-foreground-500 hover:bg-background-100'
                }`}
              >
                {mode === 'now' ? 'Assign now' : 'Assign later'}
              </button>
            ))}
          </div>
        </FormField>
      )}
      {!lockGroup && placementOptional && !showPlacement && (
        <p className="-mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-background-200 bg-background-50 px-3 py-2 text-[11px] leading-5 text-foreground-500">
          <span>
            <AppIcon className="ri-information-line mr-1 text-sm"></AppIcon>
            {module
              ? 'Not assigned yet. Saves on its own until you give it a programme, cohort and group.'
              : 'Saves on its own. Assign it to a programme, cohort and group later, from Edit.'}
          </span>
          {Boolean(module) && (
            <button
              type="button"
              onClick={() => setRevealPlacement(true)}
              className="shrink-0 text-[11px] font-bold text-primary-700 hover:underline"
            >
              Assign it now
            </button>
          )}
        </p>
      )}
      {!lockGroup && showPlacement && (
        <>
          {placementOptional && (
            <p className="-mt-2 rounded-lg border border-background-200 bg-background-50 px-3 py-2 text-[11px] leading-5 text-foreground-500">
              <AppIcon className="ri-information-line mr-1 text-sm"></AppIcon>
              Optional. Pick a programme, cohort and group now, or clear them and save this module unassigned.
            </p>
          )}
          <FormField label="Programme" required={!placementOptional} hint="Filters the cohorts and groups below.">
            <SelectControl
              value={programmeId}
              onChange={changeProgramme}
              options={programmeOptions}
              placeholder="Select a programme"
            />
          </FormField>
          <FormField label="Cohort" required={!placementOptional} hint="Narrows the groups and seeds the start date.">
            <SelectControl
              value={cohortId}
              onChange={changeCohort}
              options={availableCohorts.map(cohort => ({ value: cohort.id, label: cohort.name }))}
              placeholder={availableCohorts.length ? 'Select a cohort' : 'No cohorts for this programme'}
            />
          </FormField>
          <FormField
            as="group"
            label={selectedGroups.length > 1 ? `Groups (${selectedGroups.length})` : 'Groups'}
            required={!placementOptional}
            hint={placementHint}
          >
            <MultiSelectControl
              value={groupIds}
              onChange={changeGroups}
              options={groupOptions}
              selectAllLabel="groups"
              searchable
              searchPlaceholder="Search groups by name, programme or cohort..."
              emptyMessage={
                cohortId
                  ? 'No groups for this cohort.'
                  : programmeId
                    ? 'Select a cohort above to see its groups.'
                    : 'Select a programme and cohort above to see its groups.'
              }
            />
          </FormField>
          {otherGroupCount > 0 && (
            <p className="-mt-2 rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-[11px] leading-5 text-primary-800">
              <AppIcon className="ri-information-line mr-1 text-sm"></AppIcon>
              {module
                ? `Saving updates ${selectedGroup?.name || 'this group'}'s delivery and creates ${otherGroupCount} more, one per other ticked group.`
                : `Saving creates ${selectedGroups.length} deliveries of this module, one per ticked group.`}
              {' '}The session dates and tutor check below are {selectedGroup?.name || 'the first group'}'s; every other group is dated from its own delivery days.
            </p>
          )}
        </>
      )}
      <FormField label="Module name" required>
        <TextControl value={name} onChange={setName} placeholder="e.g. Data Modelling" />
      </FormField>
      {/* Weeks describe the authored module structure. The selected Group owns
          the delivery days and clock; this form owns the module date window. */}
      <FormField
        label="Weeks"
        required={Boolean(selectedGroups.length)}
        hint={
          selectedGroups.length
            ? 'How many authored weeks the module contains. Its sessions use the selected Group delivery pattern.'
            : 'How many authored weeks the module contains. Delivery dates are set when it is assigned to a Group.'
        }
      >
        <TextControl type="number" min={1} max={104} value={sessionsNumber} onChange={changeWeeks} />
      </FormField>
      <p className="-mt-2 rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-[12px] leading-5 text-primary-800">
        <AppIcon className="ri-information-line mr-1 text-sm"></AppIcon>
        The selected Group supplies the delivery days and session times. The module start and end dates belong to this module.
      </p>
      {showSchedule && selectedGroup && (
        <div className="space-y-4 rounded-lg border border-background-200 bg-background-50 p-4">
          {showInheritedScheduleControls && <>
          <FormField
            label="Sessions per week"
            required
            hint={otherGroupCount
              ? `Schedule for ${selectedGroup.name}. Other selected groups use their own delivery days and times.`
              : 'One session per selected day. All dates share one Teams series link when you create the series.'}
          >
            <SelectControl
              value={sessionsPerWeek}
              options={Array.from({ length: 7 }, (_, index) => ({ value: String(index + 1), label: String(index + 1) }))}
              onChange={value => {
                setSessionsPerWeek(value);
                setWeekDays(weekDays.split(',').map(day => day.trim()).filter(Boolean).slice(0, Number(value)).join(', '));
                setTargetEndDate('');
              }}
            />
          </FormField>
          <FormField label="Delivery days" as="group" required hint={`Choose ${sessionsPerWeek} day${sessionsPerWeek === '1' ? '' : 's'} each week (${deliveryDaysPerWeek} selected).`}>
            <WeekdayControl value={weekDays} maxSelections={Number(sessionsPerWeek)} onChange={value => { setWeekDays(value); setTargetEndDate(''); }} />
          </FormField>
          </>}
          {/* The group's own timetable, stated as the group's rather than merged
              into the fields above. A module inherits it and may then differ, and
              "this module runs to its own schedule" is a fact the reader has to
              be able to see before they save dates that follow from it. */}
          <>
            <div className="rounded-lg border border-background-200 bg-background-100 p-3">
              <p className="text-[12px] font-semibold text-foreground-800">
                {selectedGroup.name} delivers {groupPattern?.days || 'on no weekday yet'}
                {groupPattern?.startTime && groupPattern?.endTime
                  ? `, ${clockLabel(groupPattern.startTime)} - ${clockLabel(groupPattern.endTime)}`
                  : ''}
                {groupPattern?.durationMinutes ? ` (${durationLabel(groupPattern.durationMinutes)} a session)` : ''}
              </p>
              <p className="mt-1 text-[12px] text-foreground-600">Coach: {cleanText(selectedGroup.coach) || 'Not assigned'}</p>
              {!groupPattern?.startTime || !groupPattern?.endTime ? (
                <p className="mt-1 text-[12px] text-amber-700">
                  This group has no {groupPattern?.startTime ? 'end' : groupPattern?.endTime ? 'start' : 'start and end'} time
                  set, so the times below start at 09:00 - 10:00. Set the group&apos;s schedule for this module to follow it.
                </p>
              ) : !groupPattern?.days ? (
                <p className="mt-1 text-[12px] text-amber-700">
                  This group has no delivery day set, so the days above are this module&apos;s own.
                  Set the group&apos;s schedule for this module to follow it.
                </p>
              ) : matchesGroupPattern ? (
                <p className="mt-1 text-[12px] text-emerald-700">This module follows it.</p>
              ) : (
                <p className="mt-1 text-[12px] text-emerald-700">This module follows the selected Group&rsquo;s delivery pattern.</p>
              )}
            </div>
          </>
          {showInheritedScheduleControls && <>{weeklySchedule.map(slot => (
            <div key={slot.day} className="rounded-lg border border-background-200 p-3">
              <p className="mb-2 text-[12px] font-semibold">{slot.day}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label={`${slot.day} start time`} required hint={clockLabel(slot.startTime)}><TextControl type="time" value={slot.startTime} onChange={value => setWeeklyTimes(previous => ({ ...previous, [slot.day]: { startTime: value, endTime: slot.endTime } }))} /></FormField>
                <FormField label={`${slot.day} end time`} required hint={clockLabel(slot.endTime)}><TextControl type="time" value={slot.endTime} onChange={value => setWeeklyTimes(previous => ({ ...previous, [slot.day]: { startTime: slot.startTime, endTime: value } }))} /></FormField>
              </div>
            </div>
          ))}</>}
          <p className="text-[12px] text-foreground-500">Each day repeats at its own time every week. {weeksEntered} weeks × {sessionsPerWeek} sessions = {Math.round(weeksEntered * Number(sessionsPerWeek))} sessions. Different times use separate Teams series and links.</p>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <DatePickerField
          label="Start date"
          required={Boolean(selectedGroups.length)}
          value={startDate}
          onChange={changeStartDate}
          min={cohortSoftStart || undefined}
          max={selectedCohort?.practicalEndDate || selectedCohort?.endDate || undefined}
          error={startDateError || undefined}
          warning={startDateNotice || undefined}
          helper={
            selectedCohort
              ? `Soft start allowed from ${formatDateLabel(cohortSoftStart || selectedCohort.startDate)}. Cohort window ${formatDateLabel(selectedCohort.startDate)} - ${formatDateLabel(selectedCohort.practicalEndDate || selectedCohort.endDate)}.`
              : "The module's own planned start. Checked against the cohort's window when you assign it."
          }
        />
        <DatePickerField
          label="End date"
          required={Boolean(selectedGroups.length)}
          value={endDate}
          onChange={changeEndDate}
          min={startDate || cohortSoftStart || undefined}
          max={selectedCohort?.practicalEndDate || selectedCohort?.endDate || undefined}
          placeholder={showSchedule ? 'Calculated or target' : 'Planned finish'}
          error={endDateError || undefined}
          helper={
            showSchedule
              ? endDateHelper
              : manualEndDate
                ? 'Set by hand. The weeks above were counted from it; change the weeks to recalculate the date instead.'
                : `${weeksEntered} week${weeksEntered === 1 ? '' : 's'} from the start date, one session a week. Recalculated from the group's delivery days and holidays when you assign it.`
          }
        />
      </div>
      {showSchedule && (
        <>
          <FormField label="Tutor" required={Boolean(selectedGroups.length)} hint={tutorHint}>
            <SelectControl
              value={tutor}
              onChange={setTutor}
              options={tutorOptions}
              placeholder="Select a tutor"
            />
          </FormField>
          {/*
            Shown whenever the module has a tutor, not only while one is being
            chosen: this is where you see whether that tutor has been told, and
            that question outlives the save that assigned them. Its state is the
            ledger's, read back on open -- so it survives closing the drawer,
            and a tutor swap reads the new tutor's record rather than the old
            one's.
          */}
          {selectedTutorName && (
            <label
              className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-3 ${
                tutorFullyEmailed
                  ? 'border-emerald-200 bg-emerald-50'
                  : 'cursor-pointer border-background-200 bg-background-50'
              }`}
            >
              <input
                ref={tutorEmailBoxRef}
                type="checkbox"
                checked={tutorFullyEmailed || notifyTutor}
                onChange={event => setNotifyTutor(event.target.checked)}
                // Already told about every delivery: there is nothing left to
                // send, and unticking cannot unsend. Re-sending stays where it
                // has always been, on the module's own workspace.
                disabled={saving || tutorFullyEmailed}
                aria-describedby="tutor-email-detail"
                className={`mt-0.5 h-4 w-4 shrink-0 accent-primary-600 ${tutorFullyEmailed ? '' : 'cursor-pointer'}`}
              />
              <span className="min-w-0">
                <span className="block text-[12px] font-bold text-foreground-800">
                  {tutorFullyEmailed
                    ? `${selectedTutorName} has been emailed about this module`
                    : `Email ${selectedTutorName} about this module`}
                </span>
                <span id="tutor-email-detail" className="mt-0.5 block text-[11px] leading-4 text-foreground-500">
                  {tutorFullyEmailed
                    ? tutorEmailStatus?.lastSentAt
                      ? `Last emailed ${formatSystemTimestamp(tutorEmailStatus.lastSentAt, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}. Send it again from the module's workspace.`
                      : "Send it again from the module's workspace."
                    : totalDeliveries > 1
                      ? `One message covering all ${totalDeliveries} deliveries, with each group's dates and schedule. Nothing is sent unless you tick this.`
                      : 'They get the module name, its dates and its schedule. Nothing is sent unless you tick this.'}
                </span>
                {tutorPartlyEmailed && (
                  <span role="status" className="mt-1 block text-[11px] font-semibold leading-4 text-amber-700">
                    {emailedDeliveries} of {totalDeliveries} deliveries emailed
                    {tutorEmailStatus?.lastSentAt
                      ? `, last on ${formatSystemTimestamp(tutorEmailStatus.lastSentAt, { day: 'numeric', month: 'short', year: 'numeric' })}`
                      : ''}
                    . Ticking this covers all of them in one message.
                  </span>
                )}
                {tutorEmailStatus?.tutor && !tutorEmailStatus.tutor.hasEmail && (
                  <span role="status" className="mt-1 block text-[11px] leading-4 text-amber-700">
                    {selectedTutorName} has no email address in the staff directory, so nothing can be sent yet.
                  </span>
                )}
              </span>
            </label>
          )}
          {groupDeliveryDayMissing && (
            <p className={`rounded-xl border px-3.5 py-3 text-[12px] leading-5 ${
              assigningTutor
                ? 'border-red-200 bg-red-50 text-red-700'
                : 'border-amber-200 bg-amber-50 text-amber-800'
            }`}
            >
              {groupDeliveryDayMessage}
            </p>
          )}
          {tutorClash && (
            <TutorClashNotice
              verdict={tutorClash}
              sessionDates={sessionDates}
              freeTutors={freeTutorNames}
              onPickTutor={setTutor}
            />
          )}
          {tutorConflictGroup && (
            <div className="flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-3.5 py-3">
              <p className="text-[12px] leading-5 text-red-700">
                The save was refused because of that clash. If this is a deliberate
                co-taught slot for {tutorConflictGroup.groupName}, you can book the tutor
                anyway and save regardless.
              </p>
              <button
                type="button"
                onClick={bookConflictAnyway}
                disabled={saving}
                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-red-300 bg-white px-3 text-[12px] font-bold text-red-700 transition-smooth hover:bg-red-100 disabled:opacity-50"
              >
                Book anyway
              </button>
            </div>
          )}
        </>
      )}
      <FormField label="Notes">
        <TextAreaControl value={description} onChange={setDescription} placeholder="Optional delivery notes" />
      </FormField>
      <FormField label="Colour">
        <ColorControl value={color} onChange={setColor} />
      </FormField>
      <FormField
        label="Cover image"
        hint="Shown in Module Builder and on learners' subject cards. Upload or change it here, then save the module."
      >
        <CoverImageControl
          value={coverImage}
          onChange={setCoverImage}
          onError={setError}
          alt={`${name || 'Module'} cover`}
        />
      </FormField>
    </EntityDrawer>
  );
}

/**
 * A weekend or a bank holiday isn't refused as a start date -- it's still saved
 * as typed -- but the module won't actually deliver that day, so the picker warns
 * about it instead of staying silent.
 */
function describeNonDeliveryDate(dateValue: string, holidays: CurriculumHoliday[]): string {
  const date = dateFromYmd(dateValue);
  if (!date) return '';
  // The weekend half of this check is the cohort drawer's too, so the sentence
  // lives in `model.ts` and both drawers read the one copy of it.
  const weekend = weekendDateNotice(dateValue);
  if (weekend) return weekend;
  const holiday = holidays.find(item => {
    const start = dateFromYmd(item.startDate);
    const end = dateFromYmd(item.endDate || item.startDate);
    return start && end && start <= date && date <= end;
  });
  if (holiday) {
    return `That date is ${holiday.label || 'a bank holiday'} -- an England bank holiday, so no delivery runs then.`;
  }
  return '';
}

/** Monday-first weekday indexes, the way `parse_delivery_days` reads a group's schedule. */
const WEEKDAY_INDEX: Record<string, number> = {
  monday: 0, mon: 0,
  tuesday: 1, tue: 1,
  wednesday: 2, wed: 2,
  thursday: 3, thu: 3,
  friday: 4, fri: 4,
  saturday: 5, sat: 5,
  sunday: 6, sun: 6,
};

/** `HH:MM` as minutes past midnight, or -1 when it is not a clock. */
function clockMinutes(value: string): number {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  return match ? Number(match[1]) * 60 + Number(match[2]) : -1;
}

/**
 * The delivery pattern the group runs to: the weekday it meets on, the clock it
 * occupies, and therefore how long one session is.
 *
 * This is the parent value a module inherits. The group stores it as three
 * loose columns -- `session_week_day`, `session_start_time`, `session_end_time`
 * -- and rows written before the schedule was split kept a 12-hour clock, so
 * the times are normalised here rather than trusted. A time that cannot be read
 * comes back empty and is reported as missing, never as midnight.
 *
 * Returns null for a group with no pattern at all, which is the difference
 * between "the group says nothing" and "the group says Thursday but has no
 * times" -- the drawer says a different thing for each.
 */
function groupDeliveryPattern(group: CurriculumGroup | undefined | null) {
  if (!group) return null;
  const readClock = (value: unknown) => {
    try {
      return normalizedClock(value);
    } catch {
      return '';
    }
  };
  const days = canonicalDeliveryDays(cleanText(group.weekDays));
  const startTime = readClock(group.startTime);
  const endTime = readClock(group.endTime);
  const span = startTime && endTime ? clockMinutes(endTime) - clockMinutes(startTime) : 0;
  if (!days && !startTime && !endTime) return null;
  return { days, startTime, endTime, durationMinutes: span > 0 ? span : 0 };
}

function canonicalDeliveryDays(value: string): string {
  const names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const days = deliveryDayIndexes(value);
  return names.filter((_, index) => days.includes(index)).join(', ');
}

function deliveryDayIndexes(weekDays: string): number[] {
  const days: number[] = [];
  String(weekDays || '').toLowerCase().split(/[,/|+&\s]+/).forEach(token => {
    const index = WEEKDAY_INDEX[token.trim()];
    if (index !== undefined && !days.includes(index)) days.push(index);
  });
  return days;
}

function holidayDateKeys(holidays: CurriculumHoliday[]): Set<string> {
  const keys = new Set<string>();
  (holidays || []).forEach(holiday => {
    const start = dateFromYmd(holiday.startDate);
    const end = dateFromYmd(holiday.endDate || holiday.startDate) || start;
    if (!start || !end) return;
    const cursor = new Date(start);
    while (cursor <= end) {
      keys.add(ymdOf(cursor));
      cursor.setDate(cursor.getDate() + 1);
    }
  });
  return keys;
}

function ymdOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

/**
 * The date the module's authored duration ends -- start date plus the whole
 * weeks entered, inclusive of both ends. This is the module's calendar span,
 * not a session date: a module authored for 1 week always runs a full week
 * from its start date, even when its only live session falls on day one.
 * Delivery days and holidays never move it -- they decide where sessions
 * land (`build_module_session_plan` on the server), which is a separate
 * question from how long the module itself runs. Returns '' only when there
 * is nothing to count from -- no start date, or no weeks.
 */
function projectModuleEndDate(startDate: string, weeks: number): string {
  const start = dateFromYmd(startDate);
  const weekCount = Math.max(0, Math.round(Number(weeks) || 0));
  if (!start || weekCount <= 0) return '';
  const end = new Date(start);
  end.setDate(end.getDate() + weekCount * 7 - 1);
  return ymdOf(end);
}

/**
 * How many whole weeks a start and an end date span -- `projectModuleEndDate`
 * run backwards, so the Weeks box answers a date the user picked rather than
 * the other way round. Calendar weeks, inclusive of both ends: a part-week is
 * still a week the builder has to author, so the division rounds up. Returns
 * 0 whenever there is nothing to count -- a missing date, or an end date
 * before the start.
 */
function projectModuleWeeks(startDate: string, endDate: string): number {
  const start = dateFromYmd(startDate);
  const end = dateFromYmd(endDate);
  if (!start || !end || end < start) return 0;
  const calendarDays = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
  return clampModuleWeeks(Math.ceil(calendarDays / 7));
}

/** The Weeks input's own bounds, so a computed count is never one it refuses. */
function clampModuleWeeks(weeks: number): number {
  if (!Number.isFinite(weeks) || weeks <= 0) return 0;
  return Math.min(104, Math.max(1, Math.round(weeks)));
}

function dateFromYmd(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || '').trim());
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** One row of the session preview timeline: a normal session, a session a holiday blocked, or that session's replacement date. */
type SessionTimelineEntry =
  | { kind: 'session'; date: string; day: string; sessionNumber: number }
  | { kind: 'blocked'; date: string; sessionNumber: number; holidayName: string; replacementDate: string; replacementIsFinal: boolean }
  | { kind: 'replacement'; date: string; day: string; sessionNumber: number };

function monthKeyOf(value: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(String(value || '').trim());
  return match ? `${match[1]}-${match[2]}` : '';
}

function monthLabelOf(key: string): string {
  const parsed = new Date(`${key}-01T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return key;
  return parsed.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
}

function blockedSessionDetail(entry: Extract<SessionTimelineEntry, { kind: 'blocked' }>): string {
  const holidayName = entry.holidayName || 'a selected holiday';
  if (entry.replacementIsFinal) {
    return `Blocked by ${holidayName}; final replacement scheduled on ${formatDateLabel(entry.replacementDate)}.`;
  }
  return `Blocked by ${holidayName}; shifted to ${formatDateLabel(entry.replacementDate)}, which was also closed.`;
}

/**
 * The module's dated plan, as a timeline: every session, the ones a holiday
 * closed, and the date each of those moved to.
 *
 * Exported because the Course structure rail in the module builder opens the
 * same thing. That rail already holds the plan and the authored weeks, so the
 * alternative was a second timeline that would drift from this one; the reader
 * is looking at the same dates either way, so they get the same view.
 */
export function ModuleSessionPreview({
  moduleName,
  plan,
  holidays,
  sessionNames,
  sessionNamesLoading,
  sessionNamesError,
  onClose,
  variant = 'dialog',
}: {
  moduleName: string;
  plan: CurriculumSessionPlanPreview;
  holidays: CurriculumHoliday[];
  /** The live-session component titles, by session number. See `liveSessionNamesByNumber`. */
  sessionNames: Array<string | null>;
  sessionNamesLoading: boolean;
  sessionNamesError: boolean;
  onClose: () => void;
  /**
   * `dialog` covers the page and has to be dismissed before anything else
   * can be done -- right for the drawer, where the dates are being checked
   * before a save. `panel` opens down the right-hand edge and leaves the
   * page live behind it, which is what the Course structure rail wants: the
   * weeks and the dates they deliver on are read against each other.
   */
  variant?: 'dialog' | 'panel';
}) {
  const panel = variant === 'panel';
  const shifted = plan.sessions.filter(session => session.skippedHolidays?.length);
  // No weeks at all: a module being created has none yet, and cannot have any
  // until it is saved. That is one fact about the module, not ten facts about
  // its dates, so it is said once above the timeline and the rows stay dates.
  const noWeeksAuthored = !sessionNamesLoading && !sessionNamesError && !sessionNames.length;
  // The name of the live session that runs on this date, or the reason this
  // particular date has none -- a week that holds no live session, or one whose
  // live session was never named. Both are gaps the person can go and close.
  const sessionNameOf = (sessionNumber: number): { text: string; authored: boolean } => {
    if (sessionNamesLoading) return { text: 'Reading the weeks...', authored: false };
    if (sessionNamesError) return { text: 'Session names could not be read', authored: false };
    if (noWeeksAuthored) return { text: '', authored: false };
    const name = sessionNames[sessionNumber - 1];
    if (name === undefined) return { text: 'No week authored for this session', authored: false };
    if (name === null) return { text: 'This week holds no live session', authored: false };
    return name ? { text: name, authored: true } : { text: 'Live session not named', authored: false };
  };
  const holidayLabel = (date: string) => {
    const value = new Date(date);
    if (Number.isNaN(value.getTime())) return 'Selected holiday';
    const match = holidays.find(holiday => {
      const start = new Date(holiday.startDate);
      const end = new Date(holiday.endDate || holiday.startDate);
      return !Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && start <= value && value <= end;
    });
    return match?.label || 'Selected holiday';
  };

  const entries: SessionTimelineEntry[] = [];
  plan.sessions.forEach(session => {
    const skippedDates = session.skippedHolidays || [];
    if (skippedDates.length) {
      skippedDates.forEach((date, index) => {
        const nextSkippedDate = skippedDates[index + 1];
        entries.push({
          kind: 'blocked',
          date,
          sessionNumber: session.sessionNumber,
          holidayName: holidayLabel(date),
          replacementDate: nextSkippedDate || session.date,
          replacementIsFinal: !nextSkippedDate,
        });
      });
      entries.push({ kind: 'replacement', date: session.date, day: session.day, sessionNumber: session.sessionNumber });
    } else {
      entries.push({ kind: 'session', date: session.date, day: session.day, sessionNumber: session.sessionNumber });
    }
  });
  entries.sort((a, b) => a.date.localeCompare(b.date));

  const monthGroups: Array<{ key: string; label: string; entries: SessionTimelineEntry[] }> = [];
  entries.forEach(entry => {
    const key = monthKeyOf(entry.date);
    const current = monthGroups[monthGroups.length - 1];
    if (current && current.key === key) {
      current.entries.push(entry);
      return;
    }
    monthGroups.push({ key, label: monthLabelOf(key), entries: [entry] });
  });

  // Only a holiday that actually blocked a session earns a spot here -- one
  // whose window merely overlaps the calendar month but caught no session
  // (a break either side of the delivery day) is not something the reader
  // needs to act on, so it stays out of the list entirely.
  const clashDates = new Set(plan.sessions.flatMap(session => session.skippedHolidays || []));
  const holidaysForMonth = (key: string) => {
    const [yearText, monthText] = key.split('-');
    const year = Number(yearText);
    const month = Number(monthText) - 1;
    if (Number.isNaN(year) || Number.isNaN(month)) return [];
    const monthStart = new Date(year, month, 1);
    const monthEnd = new Date(year, month + 1, 0);
    return holidays.filter(holiday => {
      const start = new Date(holiday.startDate);
      const end = new Date(holiday.endDate || holiday.startDate);
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return false;
      if (start > monthEnd || end < monthStart) return false;
      return Array.from(clashDates).some(date => {
        const clash = new Date(date);
        return !Number.isNaN(clash.getTime()) && clash >= start && clash <= end;
      });
    });
  };

  /**
   * Click anywhere off the panel to close it.
   *
   * The dialog dismisses by clicking its backdrop, and a panel has no backdrop
   * -- it deliberately leaves the page live rather than covering it. So the
   * document is asked instead, and the click does both things: it closes the
   * panel and still lands on whatever was clicked, which is the point of a
   * panel that does not block the page. `mousedown` rather than `click`, so a
   * drag that starts outside and ends inside cannot count as one.
   */
  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!panel) return undefined;
    const dismiss = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (target && !panelRef.current?.contains(target)) onClose();
    };
    document.addEventListener('mousedown', dismiss);
    return () => document.removeEventListener('mousedown', dismiss);
  }, [panel, onClose]);

  return (
    <div
      ref={panelRef}
      className={panel
        ? 'fixed inset-y-0 right-0 z-[120] flex w-full max-w-2xl'
        : 'fixed inset-0 z-[120] flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm'}
      role="dialog"
      /* A panel is not modal: the page behind it stays live, and claiming
         otherwise would tell a screen reader the rest of the screen is inert
         when it is not. It is dismissed by the document listener above rather
         than by a backdrop, since there is no backdrop to click. */
      aria-modal={!panel}
      aria-labelledby="module-session-preview-title"
      onMouseDown={panel ? undefined : event => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div className={panel
        ? 'flex h-full w-full flex-col overflow-hidden border-l border-background-200 bg-background-50 shadow-2xl'
        : 'flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl bg-background-50 shadow-2xl'}>
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-background-200 px-5 py-4">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-wider text-primary-700">Module sessions</p>
            <h2 id="module-session-preview-title" className="mt-1 text-lg font-heading font-bold text-foreground-900">
              {moduleName}
            </h2>
            <p className="mt-1 text-[12px] text-foreground-500">
              {plan.sessions.length} session{plan.sessions.length === 1 ? '' : 's'} planned. Final date {formatDateLabel(plan.finalEndDate)}.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close session preview"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-foreground-500 transition-smooth hover:bg-background-100 hover:text-foreground-900"
          >
            <AppIcon className="ri-close-line text-lg"></AppIcon>
          </button>
        </div>
        {shifted.length > 0 && (
          <div className="flex shrink-0 items-start gap-2 border-b border-amber-200 bg-amber-50 px-5 py-3 text-[12px] font-semibold text-amber-800">
            <AppIcon className="ri-alert-line mt-0.5 shrink-0 text-sm"></AppIcon>
            <span>
              {shifted.length} planned session{shifted.length === 1 ? ' was' : 's were'} skipped on holiday weeks. Counted sessions
              stay at {plan.sessions.length}; delivery now ends on {formatDateLabel(plan.finalEndDate)}.
            </span>
          </div>
        )}
        {noWeeksAuthored && (
          <div className="flex shrink-0 items-start gap-2 border-b border-background-200 bg-background-100 px-5 py-3 text-[12px] text-foreground-600">
            <AppIcon className="ri-information-line mt-0.5 shrink-0 text-sm"></AppIcon>
            <span>
              These are the dates. Each session is named after the live session in its week,
              authored in the Module Builder once this module is saved.
            </span>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {monthGroups.map(group => {
            const delivered = group.entries.filter(entry => entry.kind !== 'blocked').length;
            const skippedCount = group.entries.filter(entry => entry.kind === 'blocked').length;
            const monthHolidays = group.key ? holidaysForMonth(group.key) : [];
            return (
              <div key={group.key || 'unscheduled'} className={`flex flex-col gap-3 border-b border-background-200 py-4 last:border-b-0 ${panel ? '' : 'sm:flex-row'}`}>
                {/* `sm:` asks the window how wide it is, and in panel mode the
                    window is not the constraint -- the panel is. Three columns
                    inside 28rem left the session rows about 5rem wide, which is
                    what broke "07 Aug 2026" across three lines. */}
                <div className={`w-full shrink-0 ${panel ? '' : 'sm:w-32'}`}>
                  <p className="text-[13px] font-heading font-bold text-foreground-900">{group.label}</p>
                  <p className="mt-1 text-[11px] font-semibold uppercase tracking-wider text-foreground-400">
                    {delivered} delivered
                  </p>
                  {skippedCount > 0 && (
                    <p className="text-[11px] font-semibold text-red-600">{skippedCount} skipped</p>
                  )}
                </div>
                <div className="min-w-0 flex-1 space-y-2">
                  {group.entries.map((entry, index) => {
                    const sessionName = sessionNameOf(entry.sessionNumber);
                    if (entry.kind === 'blocked') {
                      return (
                        <div key={`${entry.sessionNumber}-blocked-${index}`} className="overflow-hidden rounded-lg border border-red-200 bg-red-50">
                          <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                            <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]">
                              <AppIcon className="ri-close-circle-fill shrink-0 text-sm text-red-600"></AppIcon>
                              <span className="whitespace-nowrap font-bold text-red-700">{formatDateLabel(entry.date)}</span>
                              {sessionName.text && (
                                <span className={sessionName.authored ? 'font-semibold text-red-700' : 'italic text-foreground-400'}>
                                  {sessionName.text}
                                </span>
                              )}
                            </span>
                            <span className="rounded-full bg-red-100 px-2.5 py-1 text-[11px] font-semibold text-red-700">
                              Shifted to replacement
                            </span>
                          </div>
                          <div className="border-t border-red-100 bg-red-50/70 px-3 py-1.5 text-[11px] font-medium text-red-700">
                            {blockedSessionDetail(entry)}
                          </div>
                        </div>
                      );
                    }
                    const clock = plan.sessions.find(session => session.sessionNumber === entry.sessionNumber);
                    const isReplacement = entry.kind === 'replacement';
                    return (
                      <div
                        key={`${entry.sessionNumber}-${entry.kind}`}
                        className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-[12px] ${
                          isReplacement ? 'border-emerald-200 bg-emerald-50' : 'border-background-200 bg-background-0'
                        }`}
                      >
                        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                          <span className={`whitespace-nowrap font-bold ${isReplacement ? 'text-emerald-700' : 'text-foreground-900'}`}>
                            {formatDateLabel(entry.date)}
                          </span>
                          <span className="whitespace-nowrap text-foreground-400">({entry.day}) {clock?.startTime && `${clock.startTime}–${clock.endTime}`}</span>
                          {sessionName.text && (
                            <span className={sessionName.authored
                              ? `font-semibold ${isReplacement ? 'text-emerald-700' : 'text-foreground-700'}`
                              : 'italic text-foreground-400'}
                            >
                              {sessionName.text}
                            </span>
                          )}
                        </span>
                        {isReplacement && (
                          <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-[11px] font-semibold text-emerald-700">
                            Replacement delivered
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
                <div className={`w-full shrink-0 space-y-1 ${panel ? '' : 'sm:w-56'}`}>
                  {monthHolidays.map(holiday => (
                    <p key={holiday.id} className="text-[11px] leading-5 text-foreground-500">
                      <span className="font-semibold text-foreground-700">{holiday.label}</span>
                      {holiday.type && <span className="text-foreground-400"> ({holiday.type})</span>}
                      <br />
                      {formatDateLabel(holiday.startDate)} - {formatDateLabel(holiday.endDate || holiday.startDate)}
                    </p>
                  ))}
                </div>
              </div>
            );
          })}
          {plan.warnings.length > 0 && (
            <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] font-semibold text-amber-700">
              {plan.warnings.join(' ')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
