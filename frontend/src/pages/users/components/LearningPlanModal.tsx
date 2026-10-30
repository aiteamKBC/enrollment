import { useEffect, useMemo, useRef, useState } from 'react';
import { useToast } from "@/hooks/useToast.helpers";
import {
  fetchLearningPlan,
  saveLearningPlan,
  formatHours,
  formatPlanDate,
  type LearningPlanModule,
  type LearningPlanResponse,
} from '@/api/learningPlan';
import { fetchProgrammes, fetchCohorts, fetchGroups } from '@/api/curriculum';
import { updateEnrolmentUser } from '@/api/enrolmentUsers';
import { fetchFreeProgrammeModules } from '@/lib/curriculumApi';
import type { FreeCourseAssignment } from '@/api/trainingPlan';
import { Modal } from './Modal';
import { btnPrimary, btnSecondary, inputClass } from './ui';
import { RowsSkeleton, SkeletonBlock } from '@/components/feature/Skeletons';

// ============================================================================
// Learning plan — the modules a learner will actually be taught.
//
// Opens pre-filled with the module set attached to the learner's group, so the
// common case is "looks right, close it". Staff can drop a module the learner
// doesn't need, or add any module in the catalogue: the picker opens on the
// learner's own programme and a dropdown switches to any other.
//
// A module from another programme maps to different KSBs and sits under
// different funding, so the picker and the plan both name the programme a
// module came from whenever it is not the learner's own. The combination is
// allowed; it is never silent.
//
// Each module carries its off-the-job hours, and the plan shows a running
// total, because that total is the commitment being agreed.
//
// Each row also shows the module's delivery window, as scheduled in the
// curriculum. Read-only: the plan decides which modules are taught, not when. A
// module with no window shows a dash.
//
// A learner with no programme, cohort or group is asked for those first. Nothing
// here works without them — the preset comes from the group, the picker opens on
// the programme, and a module saved against no programme reads back as "not in
// catalogue" — so the placement is the first step rather than a gap to notice
// later.
// ============================================================================

/** Whether a learner is placed well enough for a plan to mean anything. */
function isPlaced(learner: { programme?: string; cohort?: string; group?: string }) {
  return Boolean(learner.programme?.trim() && learner.cohort?.trim() && learner.group?.trim());
}

// Free courses are authored under one synthetic programme id (see the
// free-courses curriculum editor); their rows share a courseId. Collapse them to
// distinct { freeCourseId, courseName } options so each course is offered once.
const FREE_COURSES_PROGRAMME_ID = 'FREE-COURSES';

interface FreeCourseOption {
  freeCourseId: string;
  courseName: string;
}

function freeCoursesEqual(a: FreeCourseAssignment[], b: FreeCourseAssignment[]) {
  const key = (list: FreeCourseAssignment[]) =>
    list.map((f) => f.freeCourseId).sort().join('|');
  return key(a) === key(b);
}

interface Props {
  learnerId: string;
  learnerName: string;
  onClose: () => void;
  /** Called after a successful save, so the list can refresh. */
  onSaved?: () => void;
  /**
   * View the plan without being able to change it. Used for learners who are
   * past planning — the Users directory only offers editing while they are in
   * Delivery, so the same modal opened from an Active row shows the agreed plan
   * and nothing that could alter it.
   */
  readOnly?: boolean;
  /**
   * The learner is already being taught, so their plan may carry progress.
   * Removing a module is still allowed — plans get corrected mid-programme —
   * but it is confirmed first, because the progress recorded against that
   * module is no longer reachable from the plan afterwards.
   */
  hasStarted?: boolean;
}

export function LearningPlanModal({ learnerId, learnerName, onClose, onSaved, readOnly = false, hasStarted = false }: Props) {
  const toast = useToast();
  const [data, setData] = useState<LearningPlanResponse | null>(null);
  const [plan, setPlan] = useState<LearningPlanModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [picker, setPicker] = useState(false);
  /** Which programme the picker is showing. '' = every programme at once. */
  const [pickerProgramme, setPickerProgramme] = useState('');
  // Where an unplaced learner is being put. Names, not ids: that is what the
  // learner record stores and what the plan reads back.
  const [place, setPlace] = useState({ programme: '', cohort: '', group: '' });
  const [programmeOptions, setProgrammeOptions] = useState<string[]>([]);
  const [cohortOptions, setCohortOptions] = useState<string[]>([]);
  const [groupOptions, setGroupOptions] = useState<string[]>([]);
  const [placing, setPlacing] = useState(false);
  // Read by the background refresh below. Refs rather than state so the
  // listener is bound once instead of re-subscribing on every edit.
  const savingRef = useRef(false);
  const dirtyRef = useRef(false);
  const placingRef = useRef(false);

  // Free courses assigned to this learner, the picker options, and a dirty flag
  // so the background refresh does not discard unsaved free-course edits.
  const [freeCourses, setFreeCourses] = useState<FreeCourseAssignment[]>([]);
  const [freeCourseOptions, setFreeCourseOptions] = useState<FreeCourseOption[]>([]);
  // The catalogue endpoint is slow on a cold cache, so track the load: an empty
  // dropdown while it is still in flight reads as "no courses", not "loading".
  const [freeCoursesLoading, setFreeCoursesLoading] = useState(true);
  const [freeCoursePicker, setFreeCoursePicker] = useState('');
  const freeDirtyRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchLearningPlan(learnerId);
        if (cancelled) return;
        setData(res);
        setPlan(res.plan);
        setFreeCourses(res.freeCourses ?? []);
        // Default to the learner's own programme: adding from theirs is the
        // ordinary case, and anything else is a deliberate choice.
        setPickerProgramme(res.learner.programmeId || '');
        // Whatever the learner already has is where the placement starts, so a
        // learner missing only a group does not have to re-pick the rest.
        setPlace({
          programme: res.learner.programme || '',
          cohort: res.learner.cohort || '',
          group: res.learner.group || '',
        });
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the learning plan.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [learnerId]);

  // The catalogue of free courses to offer, loaded once. Their rows share a
  // courseId, so collapse to distinct courses. Never fatal — a failed load just
  // leaves nothing to add.
  useEffect(() => {
    if (readOnly) return;
    let cancelled = false;
    setFreeCoursesLoading(true);
    fetchFreeProgrammeModules(FREE_COURSES_PROGRAMME_ID)
      .then((modules) => {
        if (cancelled) return;
        const byCourse = new Map<string, FreeCourseOption>();
        for (const m of modules) {
          const id = m.courseId || m.id;
          if (!id || byCourse.has(id)) continue;
          byCourse.set(id, { freeCourseId: id, courseName: m.courseName || m.title || 'Untitled course' });
        }
        setFreeCourseOptions([...byCourse.values()]);
      })
      .catch(() => !cancelled && setFreeCourseOptions([]))
      .finally(() => !cancelled && setFreeCoursesLoading(false));
    return () => { cancelled = true; };
  }, [readOnly]);

  // Groups and modules are edited on other screens, often in another tab while
  // this one sits open. Coming back to the window re-reads the plan, so the
  // modal shows the curriculum as it is now rather than as it was on open.
  //
  // Edits in progress are not thrown away: the refresh is skipped while a save
  // is in flight, and it keeps the staff member's own additions and removals,
  // re-applying them over the modules the server now reports.
  useEffect(() => {
    if (readOnly) return;
    let cancelled = false;
    let running = false;

    const refresh = async () => {
      // Not while a write is in flight: both replace `data` themselves, and a
      // refresh landing mid-write would reset the form under the staff member.
      if (running || savingRef.current || placingRef.current) return;
      if (document.visibilityState === 'hidden') return;
      running = true;
      try {
        const res = await fetchLearningPlan(learnerId);
        if (cancelled) return;
        setData(res);
        // Keep this session's free-course edits; otherwise take the server's.
        if (!freeDirtyRef.current) setFreeCourses(res.freeCourses ?? []);
        setPlan((current) => {
          // Nothing touched yet, so the server's plan is simply the truth.
          if (!dirtyRef.current) return res.plan;
          // Otherwise keep this session's edits: the rows still chosen, with
          // their values refreshed, plus anything new the curriculum has
          // gained. A row the staff member removed stays removed.
          const byId = new Map(res.plan.map((m) => [m.moduleId, m]));
          const kept = current.map((m) => byId.get(m.moduleId) ?? m);
          const known = new Set(current.map((m) => m.moduleId));
          return [...kept, ...res.plan.filter((m) => !known.has(m.moduleId))];
        });
      } catch {
        // A failed background refresh leaves what is on screen alone: the plan
        // is still editable and saving reports its own errors.
      } finally {
        running = false;
      }
    };

    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      cancelled = true;
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [learnerId, readOnly]);

  // The learner is not placed yet, so the plan below has nothing to draw on.
  const needsPlacement = Boolean(data) && !isPlaced(data!.learner);

  // The three lists cascade the way the directory's own filters do: a cohort
  // belongs to a programme, a group to a cohort. Each is fetched only once the
  // step is actually on screen.
  useEffect(() => {
    if (!needsPlacement || readOnly) return;
    let cancelled = false;
    fetchProgrammes()
      .then((names) => !cancelled && setProgrammeOptions(names))
      .catch(() => !cancelled && setProgrammeOptions([]));
    return () => { cancelled = true; };
  }, [needsPlacement, readOnly]);

  useEffect(() => {
    if (!needsPlacement || readOnly || !place.programme) {
      setCohortOptions([]);
      return;
    }
    let cancelled = false;
    fetchCohorts(place.programme)
      .then((names) => !cancelled && setCohortOptions(names))
      .catch(() => !cancelled && setCohortOptions([]));
    return () => { cancelled = true; };
  }, [needsPlacement, readOnly, place.programme]);

  useEffect(() => {
    if (!needsPlacement || readOnly || !place.programme || !place.cohort) {
      setGroupOptions([]);
      return;
    }
    let cancelled = false;
    fetchGroups(place.programme, place.cohort)
      .then((names) => !cancelled && setGroupOptions(names))
      .catch(() => !cancelled && setGroupOptions([]));
    return () => { cancelled = true; };
  }, [needsPlacement, readOnly, place.programme, place.cohort]);

  const chosen = useMemo(() => new Set(plan.map((m) => m.moduleId)), [plan]);

  // Anything on the programme that isn't already on the plan. Derived from the
  // full catalogue rather than the server's `available`, so a module removed in
  // this session becomes re-addable without a round trip.
  const addable = useMemo(() => {
    if (!data) return [];
    const catalogue = [...data.available, ...data.preset, ...data.plan];
    const seen = new Set<string>();
    const unique = catalogue.filter((m) => {
      if (seen.has(m.moduleId) || chosen.has(m.moduleId) || m.orphaned) return false;
      seen.add(m.moduleId);
      return true;
    });
    const scoped = pickerProgramme
      ? unique.filter((m) => m.programmeId === pickerProgramme)
      : unique;
    const q = search.trim().toLowerCase();
    return q
      ? scoped.filter((m) =>
          `${m.moduleTitle} ${m.groupName} ${m.programmeName}`.toLowerCase().includes(q))
      : scoped;
  }, [data, chosen, search, pickerProgramme]);

  const moduleHours = useMemo(
    () => plan.reduce((sum, m) => sum + Number(m.hours || 0), 0),
    [plan],
  );

  // Recurring Reviews the learner attends. Server-generated from the Review
  // Engine, so they do not change as modules are added or removed here — they
  // are listed for the commitment they represent, not edited.
  const meetings = useMemo(() => data?.meetings ?? [], [data?.meetings]);

  // Why the Meetings table is empty, in the words of whoever has to fix it.
  // Shown in place of rows rather than hiding the table: a table that explains
  // itself is actionable, a missing one reads as a bug.
  const meetingsEmptyMessage = (() => {
    switch (data?.meetingsReason) {
      case 'no-otjh-reviews':
        return 'No review on this programme counts towards OTJH yet. Set “Expected OTJH” and tick “counts towards OTJH” on a review to see it here.';
      case 'not-eligible':
        return 'This programme has reviews worth OTJH, but none applies to this learner’s current programme status.';
      case 'no-dates':
        return 'This learner needs a start and end date before their reviews can be scheduled.';
      case 'no-programme':
        return 'This learner’s programme is not configured in Curriculum, so it has no reviews.';
      case 'unavailable':
        return 'Could not load reviews from Curriculum. The modules above are unaffected.';
      default:
        return 'No reviews scheduled for this learner.';
    }
  })();
  const meetingHours = useMemo(
    () => meetings.reduce((sum, m) => sum + Number(m.hours || 0), 0),
    [meetings],
  );

  // The whole off-the-job commitment: taught modules plus the reviews that
  // count towards OTJH. The footer figure staff read is this one.
  const totalHours = moduleHours + meetingHours;

  const dirty = useMemo(() => {
    if (!data) return false;
    const before = data.plan.map((m) => m.moduleId).join('|');
    return before !== plan.map((m) => m.moduleId).join('|');
  }, [data, plan]);

  // Accepting the group's preset unchanged is the commonest outcome, so it has
  // to be savable. `dirty` alone cannot express that: while nothing is saved,
  // `data.plan` IS the preset, so agreeing with it looks like no change at all
  // and the button would stay dead until you removed a module and added it back.
  // An empty preset is the one thing still not worth saving — there is nothing
  // to agree to, and removing modules makes the plan dirty in its own right.
  // A module the group gained since the plan was agreed is shown on the plan but
  // not stored on the learner yet, so saving is what adopts it. `dirty` cannot
  // see that either — the server already merged it into `data.plan`, so the two
  // match — hence the same allowance the unsaved preset gets.
  // A change to the free-course assignments is its own reason to save, the same
  // as a change to the module list.
  const freeCoursesDirty = useMemo(
    () => (data ? !freeCoursesEqual(freeCourses, data.freeCourses ?? []) : false),
    [data, freeCourses],
  );

  const canSave = useMemo(() => {
    if (!data) return false;
    // `fromAptem` rows are in the same position as `inherited` ones: the
    // server merged them into the shown plan, so `dirty` is blind to them,
    // and saving is what actually records them on the learner.
    return dirty || freeCoursesDirty || (!data.saved && plan.length > 0) || plan.some((m) => m.inherited || m.fromAptem);
  }, [data, dirty, freeCoursesDirty, plan]);

  // Each of these is the staff member editing the plan by hand, which is what
  // the background refresh must not overwrite.
  const remove = (moduleId: string) => {
    dirtyRef.current = true;
    setPlan((rows) => rows.filter((m) => m.moduleId !== moduleId));
  };
  const add = (module: LearningPlanModule) => {
    dirtyRef.current = true;
    setPlan((rows) => [...rows, module]);
    setSearch('');
  };

  // Free courses carry no hours/KSBs, so they are added and removed as a plain
  // list — no catalogue tree to pull in like a module.
  const addFreeCourse = (option: FreeCourseOption) => {
    freeDirtyRef.current = true;
    setFreeCourses((rows) =>
      rows.some((f) => f.freeCourseId === option.freeCourseId)
        ? rows
        : [...rows, { freeCourseId: option.freeCourseId, courseName: option.courseName, addedAt: new Date().toISOString() }],
    );
    setFreeCoursePicker('');
  };
  const removeFreeCourse = (freeCourseId: string) => {
    freeDirtyRef.current = true;
    setFreeCourses((rows) => rows.filter((f) => f.freeCourseId !== freeCourseId));
  };

  const resetToGroup = () => {
    dirtyRef.current = true;
    if (data) setPlan(data.preset);
  };

  /**
   * Record where the learner sits, then re-read the plan so it opens on the
   * group's preset — the same state a learner who was placed at creation gets.
   */
  const savePlacement = async () => {
    setPlacing(true);
    placingRef.current = true;
    setError('');
    try {
      await updateEnrolmentUser(learnerId, {
        programme: place.programme,
        cohort: place.cohort,
        group: place.group,
      });
      const res = await fetchLearningPlan(learnerId);
      setData(res);
      setPlan(res.plan);
      setPickerProgramme(res.learner.programmeId || '');
      toast.success('Learner placed', `${place.programme} · ${place.cohort} · ${place.group}`);
      // The directory shows programme and group in their own columns.
      onSaved?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not save this learner’s placement.';
      setError(message);
      toast.error('Could not place learner', message);
    } finally {
      setPlacing(false);
      placingRef.current = false;
    }
  };

  const save = async () => {
    // Named rather than described in the abstract: an officer confirming a
    // removal should see which module they are taking away.
    const keptIds = new Set(plan.map((m) => m.moduleId));
    const removed = (data?.plan ?? []).filter((m) => !keptIds.has(m.moduleId));

    if (hasStarted && removed.length > 0) {
      const names = removed.map((m) => m.moduleTitle || m.moduleId).join(', ');
      const confirmed = window.confirm(
        `${learnerName} is already being taught. Removing ${removed.length === 1 ? 'this module' : 'these modules'} ` +
        `will take ${names} off their plan, and any progress they have recorded against ` +
        `${removed.length === 1 ? 'it' : 'them'} will no longer show in their training plan.

` +
        `Continue?`,
      );
      if (!confirmed) return;
    }

    setSaving(true);
    savingRef.current = true;
    setError('');
    try {
      const res = await saveLearningPlan(learnerId, plan.map((m) => m.moduleId), freeCourses);
      setData(res);
      setPlan(res.plan);
      setFreeCourses(res.freeCourses ?? []);
      // Saved: the plan on screen and the stored one agree again, so a later
      // refresh is free to take the server's version wholesale.
      dirtyRef.current = false;
      freeDirtyRef.current = false;
      toast.success(
        'Learning plan saved',
        `${res.totals.moduleCount} module${res.totals.moduleCount === 1 ? '' : 's'} · ${formatHours(res.totals.totalHours)}`,
      );
      onSaved?.();
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not save the learning plan.';
      setError(message);
      toast.error('Save failed', message);
    } finally {
      setSaving(false);
      savingRef.current = false;
    }
  };

  const learner = data?.learner;

  return (
    <Modal
      title={
        <span className="flex items-baseline gap-2">
          <span>Learning plan</span>
          <span className="text-[12px] font-normal text-foreground-400">{learnerName}</span>
        </span>
      }
      onClose={onClose}
      size="max-w-4xl"
      footer={
        <div className="flex items-center justify-between gap-3 w-full">
          <span className="text-[12px] text-foreground-500">
            {needsPlacement ? (
              'A programme, cohort and group decide which modules this plan can hold.'
            ) : (
              <>
                {plan.length} module{plan.length === 1 ? '' : 's'}
                {meetings.length > 0 && ` · ${meetings.length} meeting${meetings.length === 1 ? '' : 's'}`} ·{' '}
                <strong className="text-foreground-800">{formatHours(totalHours)}</strong> total
              </>
            )}
          </span>
          {needsPlacement && !readOnly ? (
            <span className="flex items-center gap-2">
              <button type="button" className={btnSecondary} onClick={onClose} disabled={placing}>
                Cancel
              </button>
              <button
                type="button"
                className={btnPrimary}
                onClick={savePlacement}
                disabled={placing || !place.programme || !place.cohort || !place.group}
                title={
                  place.programme && place.cohort && place.group
                    ? undefined
                    : 'Choose a programme, cohort and group first.'
                }
              >
                {placing ? 'Saving…' : 'Save and continue'}
              </button>
            </span>
          ) : readOnly ? (
            <button type="button" className={btnSecondary} onClick={onClose}>
              Close
            </button>
          ) : (
            <span className="flex items-center gap-2">
              <button type="button" className={btnSecondary} onClick={onClose} disabled={saving}>
                Cancel
              </button>
              <button
                type="button"
                className={btnPrimary}
                onClick={save}
                disabled={saving || !canSave}
                title={
                  canSave || saving
                    ? undefined
                    : plan.length === 0
                      ? 'Add at least one module to save this plan.'
                      : 'This plan is already saved as it stands.'
                }
              >
                {saving ? 'Saving…' : 'Save learning plan'}
              </button>
            </span>
          )}
        </div>
      }
    >
      {loading ? (
        // Shaped like the plan it is standing in for: the context line, then the
        // module table.
        <div className="space-y-5">
          <SkeletonBlock className="h-2.5 w-64" />
          <div className="rounded-xl border border-foreground-200/60 p-4">
            <RowsSkeleton rows={3} avatar={false} />
          </div>
        </div>
      ) : error && !data ? (
        <p className="py-10 text-center text-[13px] text-red-600">{error}</p>
      ) : needsPlacement ? (
        <div className="space-y-5">
          <div className="rounded-xl border border-amber-200/70 bg-amber-50/60 px-4 py-3">
            <p className="text-[13px] font-semibold text-amber-900">
              {learnerName} is not on a programme yet
            </p>
            <p className="mt-1 text-[12px] text-amber-800">
              A learning plan is built from the modules a group is taught, so this learner needs a
              programme, a cohort and a group before there is anything to plan. Choosing them here
              saves them to the learner&rsquo;s record.
            </p>
          </div>

          {error && <p className="text-[12px] text-red-600">{error}</p>}

          {readOnly ? (
            <p className="py-6 text-center text-[13px] text-foreground-400">
              This learner has no programme, cohort or group. They are past the stage where the plan
              is edited here, so this has to be set on their record.
            </p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-3">
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-semibold text-foreground-600">
                  Programme
                </span>
                <select
                  className={inputClass}
                  value={place.programme}
                  // A cohort belongs to one programme and a group to one cohort,
                  // so changing either clears what sat underneath it.
                  onChange={(e) => setPlace({ programme: e.target.value, cohort: '', group: '' })}
                >
                  <option value="">Choose a programme…</option>
                  {programmeOptions.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-1.5 block text-[12px] font-semibold text-foreground-600">
                  Cohort
                </span>
                <select
                  className={inputClass}
                  value={place.cohort}
                  disabled={!place.programme}
                  onChange={(e) => setPlace((current) => ({ ...current, cohort: e.target.value, group: '' }))}
                >
                  <option value="">
                    {place.programme ? 'Choose a cohort…' : 'Pick a programme first'}
                  </option>
                  {cohortOptions.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
                {/* Says where to go rather than leaving an empty list to be read
                    as a loading state that never finishes. */}
                {place.programme && cohortOptions.length === 0 && (
                  <span className="mt-1 block text-[11px] text-amber-700">
                    {place.programme} has no cohorts yet — add one in Curriculum Studio.
                  </span>
                )}
              </label>

              <label className="block">
                <span className="mb-1.5 block text-[12px] font-semibold text-foreground-600">
                  Group
                </span>
                <select
                  className={inputClass}
                  value={place.group}
                  disabled={!place.cohort}
                  onChange={(e) => setPlace((current) => ({ ...current, group: e.target.value }))}
                >
                  <option value="">
                    {place.cohort ? 'Choose a group…' : 'Pick a cohort first'}
                  </option>
                  {groupOptions.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
                {place.cohort && groupOptions.length === 0 && (
                  <span className="mt-1 block text-[11px] text-amber-700">
                    {place.cohort} has no groups yet — add one in Curriculum Studio.
                  </span>
                )}
              </label>
            </div>
          )}

          {/* A plan saved before this — against no programme — is still on the
              record, and its modules are why the reader may be here at all. */}
          {plan.length > 0 && (
            <p className="text-[12px] text-foreground-500">
              This learner already has {plan.length} module{plan.length === 1 ? '' : 's'} saved
              against no programme. They will still be listed once the placement is saved, marked
              as not in the catalogue where they no longer resolve.
            </p>
          )}
        </div>
      ) : (
        <div className="space-y-5">
          {/* Context: which programme/group this plan came from. */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-foreground-500">
            <span>
              Programme: <strong className="text-foreground-800">{learner?.programme || '—'}</strong>
            </span>
            <span>
              Group: <strong className="text-foreground-800">{learner?.group || '—'}</strong>
            </span>
            {!data?.saved && (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">
                Pre-filled from group — not saved yet
              </span>
            )}
            {/* Says why there is nothing to change here, rather than leaving the
                missing controls to be read as a bug. */}
            {readOnly && (
              <span className="rounded-full bg-background-100 px-2 py-0.5 text-[11px] font-semibold text-foreground-500">
                View only — a plan is edited while the learner is in Delivery
              </span>
            )}
          </div>

          {error && <p className="text-[12px] text-red-600">{error}</p>}

          {/* The plan itself. */}
          <div className="rounded-xl border border-foreground-200/60 overflow-hidden">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="bg-background-100/60 text-left">
                  <th className="py-2 px-3 font-semibold text-foreground-600">Module</th>
                  <th className="py-2 px-3 font-semibold text-foreground-600">Group</th>
                  <th className="py-2 px-3 font-semibold text-foreground-600">Start</th>
                  <th className="py-2 px-3 font-semibold text-foreground-600">End</th>
                  <th className="py-2 px-3 font-semibold text-foreground-600 text-right">Hours</th>
                  {!readOnly && <th className="py-2 px-3 w-10" />}
                </tr>
              </thead>
              <tbody>
                {plan.length === 0 && (
                  <tr>
                    <td colSpan={readOnly ? 5 : 6} className="py-8 text-center text-foreground-400">
                      {readOnly
                        ? 'No modules on this plan.'
                        : 'No modules on this plan yet — add one below.'}
                    </td>
                  </tr>
                )}
                {plan.map((m) => (
                  <tr key={m.moduleId} className="border-t border-foreground-200/50">
                    <td className="py-2 px-3 text-foreground-900">
                      {m.moduleTitle}
                      {m.orphaned && (
                        <span
                          className="ml-2 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700"
                          title="Saved on this plan but no longer in the module catalogue"
                        >
                          Not in catalogue
                        </span>
                      )}
                      {/* Arrived from the group after this plan was agreed. The
                          learner is taught it either way; saving records it. */}
                      {/* Taught through the Aptem import but never recorded on
                          the plan. Saving is what writes it to the learner. */}
                      {m.fromAptem && (
                        <span
                          className="ml-2 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700"
                          title="This learner is enrolled in this subject through their Aptem import. Save to record it on the plan."
                        >
                          From Aptem
                        </span>
                      )}
                      {m.inherited && (
                        <span
                          className="ml-2 rounded-full bg-sky-50 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700"
                          title={`Added to ${learner?.group || 'this group'} since this plan was agreed. Save to record it on the plan.`}
                        >
                          New from group
                        </span>
                      )}
                      {/* A module borrowed from another programme maps to
                          different KSBs and funding, so the plan says which. */}
                      {learner && m.programmeId && m.programmeId !== learner.programmeId && (
                        <span
                          className="ml-2 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700"
                          title={`From the ${m.programmeName} programme, not ${learner.programme}`}
                        >
                          {m.programmeName}
                        </span>
                      )}
                    </td>
                    <td className="py-2 px-3 text-foreground-500">{m.groupName || '—'}</td>
                    <td className="py-2 px-3 whitespace-nowrap text-foreground-500">
                      {formatPlanDate(m.startDate)}
                    </td>
                    <td className="py-2 px-3 whitespace-nowrap text-foreground-500">
                      {formatPlanDate(m.endDate)}
                    </td>
                    <td className="py-2 px-3 text-right font-medium text-foreground-700">
                      {formatHours(m.hours)}
                    </td>
                    {!readOnly && (
                      <td className="py-2 px-3 text-right">
                        <button
                          type="button"
                          onClick={() => remove(m.moduleId)}
                          title={`Remove ${m.moduleTitle}`}
                          className="text-foreground-400 hover:text-red-600 cursor-pointer"
                        >
                          <i className="ri-close-line" />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-foreground-300/50 bg-background-100/40">
                  <td className="py-2 px-3 font-semibold text-foreground-700" colSpan={4}>
                    {/* Module hours alone once meetings are listed too, so the
                        column still adds up to the number beneath it. */}
                    {meetings.length > 0 ? 'Modules total' : 'Total'}
                  </td>
                  <td className="py-2 px-3 text-right font-bold text-foreground-900">
                    {formatHours(moduleHours)}
                  </td>
                  {!readOnly && <td />}
                </tr>
              </tfoot>
            </table>
          </div>

          {/* Recurring Reviews that count as off-the-job training. A separate
              table because these are not chosen here: Curriculum's Review
              templates and the learner's own start date generate them, so
              there is nothing to add or remove — only hours to account for. */}
          {/* Always rendered, even with no rows: see meetingsEmptyMessage. */}
          <div>
              <div className="flex items-baseline justify-between gap-3 mb-2">
                <h3 className="text-[13px] font-semibold text-foreground-800">Meetings</h3>
                <span className="text-[11px] text-foreground-500">
                  Scheduled from this learner&apos;s start date — not editable here
                </span>
              </div>
              <div className="rounded-xl border border-foreground-200/60 overflow-hidden">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="bg-background-100/60 text-left">
                      <th className="py-2 px-3 font-semibold text-foreground-600">Meeting</th>
                      <th className="py-2 px-3 font-semibold text-foreground-600">Frequency</th>
                      <th className="py-2 px-3 font-semibold text-foreground-600">Start</th>
                      <th className="py-2 px-3 font-semibold text-foreground-600">End</th>
                      <th className="py-2 px-3 font-semibold text-foreground-600 text-right">Hours</th>
                      {!readOnly && <th className="py-2 px-3 w-10" />}
                    </tr>
                  </thead>
                  <tbody>
                    {meetings.length === 0 && (
                      <tr>
                        <td
                          colSpan={readOnly ? 5 : 6}
                          className="py-6 px-3 text-center text-foreground-400"
                        >
                          {meetingsEmptyMessage}
                        </td>
                      </tr>
                    )}
                    {meetings.map((m) => (
                      <tr key={m.reviewTemplateId} className="border-t border-foreground-200/50">
                        <td className="py-2 px-3 text-foreground-900">
                          {m.name}
                          {/* Where the hours come from, so a total that looks
                              large can be checked against its own arithmetic. */}
                          <span className="ml-2 text-[11px] text-foreground-500">
                            {formatHours(m.hoursEach)} × {m.occurrences}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-foreground-500">{m.recurrenceLabel || '—'}</td>
                        <td className="py-2 px-3 whitespace-nowrap text-foreground-500">
                          {formatPlanDate(m.startDate)}
                        </td>
                        <td className="py-2 px-3 whitespace-nowrap text-foreground-500">
                          {formatPlanDate(m.endDate)}
                        </td>
                        <td className="py-2 px-3 text-right font-medium text-foreground-700">
                          {formatHours(m.hours)}
                        </td>
                        {!readOnly && <td className="py-2 px-3" />}
                      </tr>
                    ))}
                  </tbody>
                  {meetings.length > 0 && (
                    <tfoot>
                      <tr className="border-t-2 border-foreground-300/50 bg-background-100/40">
                        <td className="py-2 px-3 font-semibold text-foreground-700" colSpan={4}>
                          Meetings total
                        </td>
                        <td className="py-2 px-3 text-right font-bold text-foreground-900">
                          {formatHours(meetingHours)}
                        </td>
                        {!readOnly && <td />}
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
              {/* The two tables above are halves of one commitment. Only worth
                  printing once meetings actually add something: with none, it
                  would just restate the modules total directly beneath it. */}
              {meetings.length > 0 && (
                <div className="mt-2 flex items-baseline justify-end gap-2 text-[13px]">
                  <span className="font-semibold text-foreground-700">Plan total</span>
                  <strong className="text-foreground-900">{formatHours(totalHours)}</strong>
                </div>
              )}
            </div>

          {/* Add a module from anywhere in the catalogue. Absent when viewing:
              nothing here is meaningful without a save. */}
          {!readOnly && (
            <div>
              <div className="flex items-center justify-between gap-3 mb-2">
                <button
                  type="button"
                  onClick={() => setPicker((v) => !v)}
                  className="text-[12px] font-semibold text-primary-600 hover:underline cursor-pointer"
                >
                  <i className={`ri-${picker ? 'subtract' : 'add'}-line mr-1`} />
                  Add a module
                </button>
                {data && data.preset.length > 0 && (
                  <button
                    type="button"
                    onClick={resetToGroup}
                    className="text-[12px] text-foreground-500 hover:text-foreground-800 hover:underline cursor-pointer"
                  >
                    Reset to group default
                  </button>
                )}
              </div>

              {picker && (
                <div className="rounded-xl border border-foreground-200/60 p-3 space-y-2">
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <select
                      className={`${inputClass} sm:max-w-[16rem]`}
                      value={pickerProgramme}
                      onChange={(e) => setPickerProgramme(e.target.value)}
                      aria-label="Programme to add modules from"
                    >
                      <option value="">All programmes</option>
                      {(data?.programmes ?? []).map((p) => (
                        <option key={p.programmeId} value={p.programmeId}>
                          {p.programmeName} ({p.moduleCount})
                        </option>
                      ))}
                    </select>
                    <input
                      className={inputClass}
                      placeholder="Search modules…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </div>
                  <div className="max-h-56 overflow-y-auto divide-y divide-foreground-200/40">
                    {addable.length === 0 && (
                      <p className="py-4 text-center text-[12px] text-foreground-400">
                        {search.trim()
                          ? 'No modules match that search.'
                          : 'No modules left to add here.'}
                      </p>
                    )}
                    {addable.map((m) => (
                      <div key={m.moduleId} className="flex items-center gap-3 py-2">
                        <span className="flex-1 min-w-0">
                          <span className="block truncate text-[13px] text-foreground-900">{m.moduleTitle}</span>
                          <span className="block text-[11px] text-foreground-400">
                            {m.groupName || 'No group'} · {formatHours(m.hours)}
                            {/* Its window, where it has one — the same dates the
                                plan table will show once it is added. */}
                            {m.startDate && m.endDate && ` · ${formatPlanDate(m.startDate)} – ${formatPlanDate(m.endDate)}`}
                          </span>
                          {/* Only worth saying when it is not where you would
                              expect the module to come from. */}
                          {learner && m.programmeId && m.programmeId !== learner.programmeId && (
                            <span className="mt-0.5 inline-block rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                              {m.programmeName}
                            </span>
                          )}
                        </span>
                        <button
                          type="button"
                          onClick={() => add(m)}
                          className="rounded-lg border border-foreground-200 px-2.5 py-1 text-[11px] font-semibold text-foreground-600 hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 cursor-pointer whitespace-nowrap"
                        >
                          Add
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Free courses — optional extras assigned to the learner. Held apart
              from the modules above: no hours, no KSBs, no progress, and no
              programme/group needed. */}
          <div>
            <div className="flex items-baseline justify-between gap-3 mb-2">
              <h3 className="text-[13px] font-semibold text-foreground-800">Free courses</h3>
              <span className="text-[11px] text-foreground-500">Extras — no hours or KSBs</span>
            </div>

            {freeCourses.length === 0 ? (
              <p className="text-[12px] text-foreground-400">
                {readOnly ? 'No free courses assigned.' : 'No free courses assigned yet — add one below.'}
              </p>
            ) : (
              <ul className="rounded-xl border border-foreground-200/60 divide-y divide-foreground-200/40">
                {freeCourses.map((f) => (
                  <li key={f.freeCourseId} className="flex items-center justify-between gap-3 px-3 py-2">
                    <span className="text-[13px] text-foreground-900 inline-flex items-center gap-2 min-w-0">
                      <i className="ri-gift-line text-primary-600 shrink-0" />
                      <span className="truncate">{f.courseName || f.freeCourseId}</span>
                    </span>
                    {!readOnly && (
                      <button
                        type="button"
                        onClick={() => removeFreeCourse(f.freeCourseId)}
                        title={`Remove ${f.courseName || f.freeCourseId}`}
                        className="text-foreground-400 hover:text-red-600 cursor-pointer shrink-0"
                      >
                        <i className="ri-close-line" />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}

            {!readOnly && (() => {
              const assigned = new Set(freeCourses.map((f) => f.freeCourseId));
              const available = freeCourseOptions.filter((o) => !assigned.has(o.freeCourseId));
              // Distinguish "still loading" from "genuinely none left": an empty
              // dropdown during the cold-cache fetch otherwise reads as broken.
              const placeholder = freeCoursesLoading
                ? 'Loading free courses…'
                : available.length === 0
                  ? 'No free courses to add'
                  : 'Add a free course…';
              return (
                <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                  <select
                    className={`${inputClass} sm:max-w-[20rem]`}
                    value={freeCoursePicker}
                    onChange={(e) => setFreeCoursePicker(e.target.value)}
                    disabled={freeCoursesLoading || available.length === 0}
                    aria-label="Free course to add"
                  >
                    <option value="">{placeholder}</option>
                    {available.map((o) => (
                      <option key={o.freeCourseId} value={o.freeCourseId}>{o.courseName}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className={btnSecondary}
                    disabled={!freeCoursePicker}
                    onClick={() => {
                      const option = freeCourseOptions.find((o) => o.freeCourseId === freeCoursePicker);
                      if (option) addFreeCourse(option);
                    }}
                  >
                    Add free course
                  </button>
                </div>
              );
            })()}
          </div>
        </div>
      )}
    </Modal>
  );
}
