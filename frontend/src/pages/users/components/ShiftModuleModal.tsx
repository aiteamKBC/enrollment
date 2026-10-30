import { useEffect, useMemo, useState } from 'react';
import { useToast } from "@/hooks/useToast.helpers";
import {
  fetchLearningPlan,
  formatHours,
  formatPlanDate,
  type LearningPlanModule,
} from '@/api/learningPlan';
import {
  fetchModuleShiftOptions,
  fetchModuleShiftProgress,
  shiftModule,
  type ModuleShiftOptions,
  type ModuleShiftProgress,
  type ShiftComponent,
  type ShiftWeekPair,
} from '@/api/moduleShift';
import { Modal } from './Modal';
import { btnPrimary, btnSecondary } from './ui';
import { RowsSkeleton } from '@/components/feature/Skeletons';

// ============================================================================
// Shift a learner from one module to another, in two steps.
//
// Step 1 — the modules. Two sections, left to right in the order the decision is
// made: the modules this learner is assigned, then the modules taught to the
// same programme as whichever of theirs is selected, across all its cohorts
// and groups. Only the selected learner's module is changed.
//
// Step 2 — the progress. A learner part-way through a module has work recorded
// against its components, and that work has to land somewhere in the module they
// are joining. Weeks match by stable id or one exact lesson title before
// falling back to delivery position. Components match by shared id first,
// otherwise by one exact cleaned title + type match. Ambiguous rows stay
// unmatched.
//
// The write has its own endpoint, which re-runs the programme lookup and moves plan
// and progress in one transaction — see module_shift.py.
// ============================================================================

interface Props {
  learnerId: string;
  learnerName: string;
  onClose: () => void;
  /** Called after a successful shift, so the directory can refresh. */
  onSaved?: () => void;
}

const cardClass = 'rounded-xl border border-foreground-200/60 overflow-hidden';
const sectionTitle = 'px-3 py-2 bg-background-100/60 text-[12px] font-semibold text-foreground-600';
const emptyNote = 'px-3 py-8 text-center text-[12px] text-foreground-400';
const selectClass =
  'w-full rounded-lg border border-foreground-200 bg-background-50 px-2 py-1 text-[11px] ' +
  'text-foreground-800 focus:border-primary-400 outline-none transition-smooth cursor-pointer';
const moduleSearchClass =
  'block w-full rounded-lg border border-foreground-200 bg-background-50 py-2 pl-8 pr-3 text-[12px] ' +
  'text-foreground-800 placeholder:text-foreground-400 focus:border-primary-400 outline-none transition-smooth';

/** "2 h · 15 pts · 26 Aug 2026" — what a learner has banked on a component. */
function progressSummary(component: ShiftComponent): string {
  const p = component.progress;
  if (!p) return '';
  const parts = [`${p.entries} ${p.entries === 1 ? 'entry' : 'entries'}`];
  if (p.otjHours) parts.push(formatHours(p.otjHours));
  if (p.points) parts.push(`${p.points} pts`);
  if (p.lastAt) parts.push(formatPlanDate(p.lastAt.slice(0, 10)));
  return parts.join(' · ');
}

/** "Awaiting tutor review" — the marking state that travels with the work. */
function reviewLabel(component: ShiftComponent): string {
  const review = component.review;
  if (!review?.status) return '';
  const status = review.status.replace(/_/g, ' ');
  const marked = status === 'submitted for tutor review' ? 'Awaiting tutor review' : status;
  const who = review.reviewedBy ? ` by ${review.reviewedBy}` : '';
  const note = review.feedback ? ' · with feedback' : '';
  return `${marked.charAt(0).toUpperCase()}${marked.slice(1)}${who}${note}`;
}

/** Mirror the server's conservative visible-title cleanup for picker options. */
function componentTitleKey(value: string): string {
  return value
    .normalize('NFKC')
    .replace(/\u00a0/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase('en');
}

/** Automatic matches stay conservative; staff can review other same-type targets. */
function isAutomaticComponentMatch(source: ShiftComponent, target: ShiftComponent): boolean {
  if (source.componentId === target.componentId) return true;
  const sourceTitle = componentTitleKey(source.title);
  return (
    source.type === target.type &&
    Boolean(sourceTitle) &&
    componentTitleKey(target.title) === sourceTitle
  );
}

/** Search the visible module metadata without changing the selected module. */
function moduleMatchesSearch(module: LearningPlanModule, query: string): boolean {
  const needle = componentTitleKey(query);
  if (!needle) return true;
  return componentTitleKey(`${module.moduleTitle} ${module.groupName || ''}`).includes(needle);
}

/** One module in either list of step 1: title, then where it sits and when. */
function ModuleRow({
  module,
  selected,
  disabled,
  disabledNote,
  onSelect,
}: {
  module: LearningPlanModule;
  selected: boolean;
  disabled?: boolean;
  disabledNote?: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`w-full text-left px-3 py-2.5 border-t border-foreground-200/50 transition-smooth ${
        disabled
          ? 'cursor-not-allowed opacity-60'
          : selected
            ? 'bg-primary-50/70 cursor-pointer'
            : 'hover:bg-background-100/60 cursor-pointer'
      }`}
    >
      <span className="flex items-start gap-2.5">
        <i
          className={`mt-0.5 text-[15px] ${
            selected ? 'ri-radio-button-line text-primary-600' : 'ri-circle-line text-foreground-300'
          }`}
        />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] text-foreground-900">{module.moduleTitle}</span>
          <span className="block text-[11px] text-foreground-400">
            {module.groupName || 'No group'} · {formatHours(module.hours)}
            {module.startDate && module.endDate &&
              ` · ${formatPlanDate(module.startDate)} – ${formatPlanDate(module.endDate)}`}
          </span>
          {disabledNote && (
            <span className="block text-[11px] font-medium text-amber-700">{disabledNote}</span>
          )}
        </span>
      </span>
    </button>
  );
}

/**
 * One week of the module being left, beside its matched week in the module being
 * joined. Collapsed to its heading until opened, since a module can run two
 * dozen weeks and only some hold progress.
 */
function WeekPair({
  pair,
  open,
  onToggle,
  mapping,
  claimedElsewhere,
  onMap,
}: {
  pair: ShiftWeekPair;
  open: boolean;
  onToggle: () => void;
  mapping: Record<string, string>;
  /** Targets already taken by a component in another week. */
  claimedElsewhere: Set<string>;
  onMap: (fromComponentId: string, toComponentId: string) => void;
}) {
  const progressed = pair.from.components.filter((c) => c.progress);
  const targets = pair.to?.components || [];
  /** Staff may manually choose a same-type component, but only in the paired week. */
  const selectableTargets = (component: ShiftComponent) =>
    targets.filter((target) => target.type === component.type);
  // Which target each progressed component was mapped onto, so the right-hand
  // column can show what is about to land on it.
  const landing = new Map<string, ShiftComponent>();
  progressed.forEach((c) => {
    const target = mapping[c.componentId];
    if (target) landing.set(target, c);
  });

  return (
    <div className={cardClass}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 bg-background-100/60 text-left cursor-pointer hover:bg-background-100"
      >
        <i className={`text-[15px] text-foreground-400 ri-arrow-${open ? 'down' : 'right'}-s-line`} />
        <span className="flex-1 min-w-0 text-[12px] font-semibold text-foreground-700">
          Week {pair.order}
          <span className="font-normal text-foreground-500">
            {' '}· {pair.from.title} &rarr; {pair.to ? pair.to.title : 'no matching week'}
          </span>
        </span>
        <span className="text-[11px] text-foreground-500 whitespace-nowrap">
          {progressed.length} with progress
        </span>
      </button>

      {open && (
        <div className="grid md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-foreground-200/50">
          {/* Left: what the learner did, and where it should go. */}
          <div className="p-3 space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-foreground-400">
              Leaving
            </p>
            {pair.from.components.map((component) => {
              const summary = progressSummary(component);
              const selectedTarget = targets.find(
                (target) => target.componentId === mapping[component.componentId],
              );
              const isManualMatch = Boolean(
                selectedTarget && !isAutomaticComponentMatch(component, selectedTarget),
              );
              return (
                <div
                  key={component.componentId}
                  className={`rounded-lg border px-2.5 py-2 ${
                    component.progress
                      ? 'border-primary-200 bg-primary-50/40'
                      : 'border-foreground-200/60'
                  }`}
                >
                  <p className="text-[12px] text-foreground-900">{component.title}</p>
                  <p className="text-[11px] text-foreground-400">
                    {component.type} · ID {component.componentId}
                    {summary && ` · ${summary}`}
                  </p>
                  {/* The marking state moves with the work, so it is shown
                      before the decision rather than discovered after it. */}
                  {reviewLabel(component) && (
                    <p className="text-[11px] font-medium text-amber-700">
                      <i className="ri-chat-check-line mr-1" />
                      {reviewLabel(component)}
                    </p>
                  )}
                  {/* Only progressed components need a destination; the rest are
                      shown for context, since a week reads as a whole. */}
                  {component.progress && (
                    <label className="mt-1.5 block">
                      <span className="sr-only">Move progress on {component.title} to</span>
                      <select
                        className={selectClass}
                        value={mapping[component.componentId] || ''}
                        onChange={(e) => onMap(component.componentId, e.target.value)}
                        disabled={selectableTargets(component).length === 0}
                      >
                        <option value="">
                          {selectableTargets(component).length === 0
                            ? 'No component of the same type in the paired week'
                            : 'Archive this progress'}
                        </option>
                        {selectableTargets(component).map((target) => (
                          <option
                            key={target.componentId}
                            value={target.componentId}
                            // One target cannot receive two components' progress.
                            disabled={
                              claimedElsewhere.has(target.componentId) ||
                              (landing.has(target.componentId) &&
                                landing.get(target.componentId)?.componentId !== component.componentId)
                            }
                          >
                            {target.title} ({target.type} · ID {target.componentId})
                            {!isAutomaticComponentMatch(component, target) ? ' — manual match' : ''}
                          </option>
                        ))}
                      </select>
                      {isManualMatch && (
                        <span className="mt-1 block text-[11px] font-medium text-amber-700">
                          Manual match: the titles differ. Confirm this is the equivalent component
                          before shifting.
                        </span>
                      )}
                    </label>
                  )}
                </div>
              );
            })}
          </div>

          {/* Right: the equivalent week, and what is about to land on it. */}
          <div className="p-3 space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-foreground-400">
              Joining
            </p>
            {!pair.to ? (
              <p className="text-[12px] text-foreground-400">
                The module being joined has no week {pair.order}, so this week&rsquo;s progress
                cannot be moved and will be archived.
              </p>
            ) : targets.length === 0 ? (
              <p className="text-[12px] text-foreground-400">
                {pair.to.title} has no components yet.
              </p>
            ) : (
              targets.map((target) => {
                const incoming = landing.get(target.componentId);
                return (
                  <div
                    key={target.componentId}
                    className={`rounded-lg border px-2.5 py-2 ${
                      incoming ? 'border-emerald-300 bg-emerald-50/50' : 'border-foreground-200/60'
                    }`}
                  >
                    <p className="text-[12px] text-foreground-900">{target.title}</p>
                    <p className="text-[11px] text-foreground-400">
                      {target.type} · ID {target.componentId}
                    </p>
                    {incoming && (
                      <p className="text-[11px] font-medium text-emerald-700">
                        <i className="ri-arrow-left-line mr-1" />
                        {incoming.title}
                      </p>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function ShiftModuleModal({ learnerId, learnerName, onClose, onSaved }: Props) {
  const toast = useToast();
  const [step, setStep] = useState<'modules' | 'progress'>('modules');
  const [plan, setPlan] = useState<LearningPlanModule[] | null>(null);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [assignedSearch, setAssignedSearch] = useState('');
  const [programmeSearch, setProgrammeSearch] = useState('');
  const [options, setOptions] = useState<ModuleShiftOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [progress, setProgress] = useState<ModuleShiftProgress | null>(null);
  const [loadingProgress, setLoadingProgress] = useState(false);
  // fromComponentId -> toComponentId. Missing or '' means "archive it".
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [openWeeks, setOpenWeeks] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // The learner's assigned modules — the same plan the learning-plan modal edits.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchLearningPlan(learnerId);
        if (cancelled) return;
        setPlan(res.plan);
        // A single module is not a choice, so it selects itself and the programme
        // list loads without a click.
        if (res.plan.length === 1) setFrom(res.plan[0].moduleId);
      } catch (err) {
        if (!cancelled) {
          setPlan([]);
          setError(err instanceof Error ? err.message : 'Could not load this learner’s modules.');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [learnerId]);

  // The programme's modules, refetched whenever the module being moved changes —
  // each one can resolve to a different programme.
  useEffect(() => {
    if (!from) {
      setOptions(null);
      return;
    }
    let cancelled = false;
    setError('');
    setOptions(null);
    setLoadingOptions(true);
    setTo('');
    setProgrammeSearch('');
    (async () => {
      try {
        const res = await fetchModuleShiftOptions(from);
        if (!cancelled) setOptions(res);
      } catch (err) {
        if (!cancelled) {
          setOptions(null);
          setError(err instanceof Error ? err.message : 'Could not load the programme’s modules.');
        }
      } finally {
        if (!cancelled) setLoadingOptions(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [from]);

  const assigned = useMemo(() => new Set((plan || []).map((m) => m.moduleId)), [plan]);

  // A module is not an alternative to itself.
  const choices = useMemo(
    () => (options?.modules || []).filter((m) => m.moduleId !== from),
    [options, from],
  );
  const visiblePlan = useMemo(
    () => (plan || []).filter((m) => moduleMatchesSearch(m, assignedSearch)),
    [plan, assignedSearch],
  );
  const visibleChoices = useMemo(
    () => choices.filter((m) => moduleMatchesSearch(m, programmeSearch)),
    [choices, programmeSearch],
  );

  const fromModule = (plan || []).find((m) => m.moduleId === from);
  const toModule = choices.find((m) => m.moduleId === to);

  const mappings = useMemo(
    () =>
      Object.entries(mapping)
        .filter(([, target]) => target)
        .map(([fromComponentId, toComponentId]) => ({ fromComponentId, toComponentId })),
    [mapping],
  );

  const openProgressStep = async () => {
    setError('');
    setLoadingProgress(true);
    setStep('progress');
    try {
      const res = await fetchModuleShiftProgress(learnerId, from, to);
      setProgress(res);
      // Start from the server's safe suggestion: same component id, otherwise
      // one exact cleaned-title + type match. Ambiguous rows stay unmatched.
      setMapping(
        Object.fromEntries(res.suggested.map((s) => [s.fromComponentId, s.toComponentId])),
      );
      // The first week with progress is the one being looked at.
      setOpenWeeks(res.weeks.length > 0 ? [res.weeks[0].order] : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load this learner’s progress.');
      setProgress(null);
    } finally {
      setLoadingProgress(false);
    }
  };

  const shift = async () => {
    if (!from || !to) return;
    setSaving(true);
    setError('');
    try {
      const res = await shiftModule(learnerId, from, to, mappings);
      const moved = res.progressMoved || 0;
      const archived = res.progressArchived || 0;
      const reviews = res.reviewsMoved || 0;
      const kept = res.reviewsKept || 0;
      const detail = [
        `${fromModule?.moduleTitle || 'Module'} → ${toModule?.moduleTitle || 'module'}`,
        moved ? `${moved} progress ${moved === 1 ? 'entry' : 'entries'} moved` : '',
        archived
          ? `${archived} progress ${archived === 1 ? 'entry' : 'entries'} archived`
          : '',
        reviews ? `${reviews} ${reviews === 1 ? 'review' : 'reviews'} moved` : '',
        // Named rather than silent: a review that stayed behind is one the
        // component joined already had, and someone may need to reconcile them.
        kept ? `${kept} left behind` : '',
      ].filter(Boolean);
      toast.success('Learner shifted', detail.join(' · '));
      onSaved?.();
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not shift this learner.';
      setError(message);
      toast.error('Shift failed', message);
    } finally {
      setSaving(false);
    }
  };

  const setPair = (fromComponentId: string, toComponentId: string) =>
    setMapping((current) => ({ ...current, [fromComponentId]: toComponentId }));

  const toggleWeek = (order: number) =>
    setOpenWeeks((current) =>
      current.includes(order) ? current.filter((o) => o !== order) : [...current, order],
    );

  /** Targets claimed by a component outside this week — one target, one source. */
  const claimedOutside = (pair: ShiftWeekPair) => {
    const own = new Set(pair.from.components.map((c) => c.componentId));
    return new Set(
      mappings.filter((m) => !own.has(m.fromComponentId)).map((m) => m.toComponentId),
    );
  };

  return (
    <Modal
      title={
        <span className="flex items-baseline gap-2">
          <span>Shift module</span>
          <span className="text-[12px] font-normal text-foreground-400">{learnerName}</span>
        </span>
      }
      onClose={onClose}
      size="max-w-5xl"
      scrollResetKey={step}
      footer={
        <div className="flex items-center justify-between gap-3 w-full">
          <span className="text-[12px] text-foreground-500">
            {fromModule && toModule ? (
              <>
                <strong className="text-foreground-800">{fromModule.moduleTitle}</strong> &rarr;{' '}
                <strong className="text-foreground-800">{toModule.moduleTitle}</strong>
                {step === 'progress' && (
                  <span className="text-foreground-400">
                    {' '}· {mappings.length} progress{' '}
                    {mappings.length === 1 ? 'match' : 'matches'}
                  </span>
                )}
              </>
            ) : (
              'Pick the module to move from, then the one to move to.'
            )}
          </span>
          <span className="flex items-center gap-2">
            {step === 'progress' ? (
              <button
                type="button"
                className={btnSecondary}
                onClick={() => setStep('modules')}
                disabled={saving}
              >
                Back
              </button>
            ) : (
              <button type="button" className={btnSecondary} onClick={onClose}>
                Cancel
              </button>
            )}
            {step === 'modules' ? (
              <button
                type="button"
                className={btnPrimary}
                onClick={openProgressStep}
                disabled={!from || !to}
              >
                Next: match progress
              </button>
            ) : (
              <button type="button" className={btnPrimary} onClick={shift} disabled={saving}>
                {saving
                  ? 'Shifting…'
                  : mappings.length > 0
                    ? 'Shift module & progress'
                    : 'Shift module'}
              </button>
            )}
          </span>
        </div>
      }
    >
      <div className="space-y-4">
        {error && <p className="text-[12px] text-red-600">{error}</p>}

        {step === 'modules' ? (
          <div className="grid gap-4 md:grid-cols-2">
            {/* 1 — what the learner is assigned now. */}
            <div className={cardClass}>
              <p className={sectionTitle}>
                Assigned modules
                {plan && <span className="ml-1 font-normal text-foreground-400">({plan.length})</span>}
              </p>
              {plan !== null && plan.length > 0 && (
                <label className="relative block border-t border-foreground-200/50 p-2">
                  <span className="sr-only">Search assigned modules</span>
                  <i className="ri-search-line pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[13px] text-foreground-400" />
                  <input
                    type="search"
                    className={moduleSearchClass}
                    value={assignedSearch}
                    onChange={(event) => setAssignedSearch(event.target.value)}
                    placeholder="Search module or group…"
                    aria-label="Search assigned modules"
                  />
                </label>
              )}
              {plan === null ? (
                <div className="p-3">
                  <RowsSkeleton rows={3} avatar={false} />
                </div>
              ) : plan.length === 0 ? (
                <p className={emptyNote}>This learner has no modules to shift from yet.</p>
              ) : visiblePlan.length === 0 ? (
                <p className={emptyNote}>No assigned modules match that search.</p>
              ) : (
                <div className="max-h-72 overflow-y-auto">
                  {visiblePlan.map((m) => (
                    <ModuleRow
                      key={m.moduleId}
                      module={m}
                      selected={m.moduleId === from}
                      onSelect={() => setFrom(m.moduleId)}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* 2 — everything in the same programme, across cohorts and groups. */}
            <div className={cardClass}>
              <p className={sectionTitle}>
                Programme modules
                {options?.programme.name && (
                  <span className="ml-1 font-normal text-foreground-400">
                    &mdash; {options.programme.name}
                  </span>
                )}
              </p>
              {!from ? (
                <p className={emptyNote}>
                  Pick one of the learner&rsquo;s modules first — its programme decides what can be
                  offered here.
                </p>
              ) : loadingOptions ? (
                <div className="p-3">
                  <RowsSkeleton rows={3} avatar={false} />
                </div>
              ) : !options ? (
                <p className={emptyNote}>Module options could not be loaded.</p>
              ) : options.reason ? (
                // Says why there is nothing here, rather than looking broken.
                <p className={emptyNote}>{options.reason}</p>
              ) : choices.length === 0 ? (
                <p className={emptyNote}>
                  {options?.programme.name || 'This programme'} teaches no other modules.
                </p>
              ) : (
                <>
                  {options && options.groups.length > 0 && (
                    <p className="px-3 pt-2 text-[11px] text-foreground-400">
                      Across {options.groups.length === 1 ? 'group' : 'groups'}{' '}
                      {options.groups.join(', ')}
                    </p>
                  )}
                  <label className="relative block p-2">
                    <span className="sr-only">Search programme modules</span>
                    <i className="ri-search-line pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[13px] text-foreground-400" />
                    <input
                      type="search"
                      className={moduleSearchClass}
                      value={programmeSearch}
                      onChange={(event) => setProgrammeSearch(event.target.value)}
                      placeholder="Search module or group…"
                      aria-label="Search programme modules"
                    />
                  </label>
                  {visibleChoices.length === 0 ? (
                    <p className={emptyNote}>No programme modules match that search.</p>
                  ) : (
                    <div className="max-h-72 overflow-y-auto">
                      {visibleChoices.map((m) => {
                        // Already on the plan: the plan holds a module once, so
                        // shifting onto it would drop one rather than swap it.
                        const already = assigned.has(m.moduleId);
                        return (
                          <ModuleRow
                            key={m.moduleId}
                            module={m}
                            selected={m.moduleId === to}
                            disabled={already}
                            disabledNote={already ? 'Already assigned to this learner' : undefined}
                            onSelect={() => setTo(m.moduleId)}
                          />
                        );
                      })}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ) : loadingProgress ? (
          <div className={cardClass}>
            <div className="p-3">
              <RowsSkeleton rows={4} avatar={false} />
            </div>
          </div>
        ) : progress && progress.weeks.length > 0 ? (
          <div className="space-y-3">
            <p className="text-[12px] text-foreground-500">
              Each week the learner has worked in, beside its matching lesson in{' '}
              <strong className="text-foreground-800">{toModule?.moduleTitle}</strong>. Exact lesson
              titles can match even when cohort calendars use different week numbers. Open a week
              to review its completed components. Automatic component matches use the same ID, or
              one exact title and type match. When titles differ, you can manually choose a
              same-type component in the paired week. Unmatched progress is archived and
              removed from active completion, achieved hours and KSB totals.
            </p>
            {progress.weeks.map((pair) => (
              <WeekPair
                key={pair.order}
                pair={pair}
                open={openWeeks.includes(pair.order)}
                onToggle={() => toggleWeek(pair.order)}
                mapping={mapping}
                claimedElsewhere={claimedOutside(pair)}
                onMap={setPair}
              />
            ))}
          </div>
        ) : (
          <div className={cardClass}>
            <p className={sectionTitle}>Progress</p>
            <p className={emptyNote}>
              {progress?.reason ||
                'There is no recorded progress to move — the shift will just change the module.'}
            </p>
          </div>
        )}
      </div>
    </Modal>
  );
}
