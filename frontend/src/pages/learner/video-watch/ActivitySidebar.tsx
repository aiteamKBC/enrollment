import { useEffect, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { AppIcon } from '@/components/feature/AppIcon';
import type { JourneyComponent } from '@/utils/learnerJourney';
import { componentTypeMeta, gradePercent, hasComponentContent, isComponentComplete, resourceTypeMeta } from '@/utils/learnerJourney';
import { learningSchedule } from '@/api/learnerOverview';
import { HolidayNoteHint } from '@/components/feature/HolidayNoteHint';
import { weekHolidayNotes } from '@/pages/learner/training-plan-timeline/model';
import { useLiveLearnerRead } from '@/hooks/useLiveLearnerRead';
import { PointActivityRows } from './PointActivityRows';
import {
  isNavigableComponent,
  toggleExpandedWeek,
  weekComponentRows,
  weekDisplayLabel,
  weekMonthHeadings,
  type SidebarWeek,
} from './weekPreview';

// ============================================================================
// The list beside an open activity: this week's activities, and the plan's
// other weeks underneath.
//
// Shared, because it is how a learner moves between activities — a quiz that
// did not have it was a dead end, reachable only by going back to the plan.
// The rows are the same wherever it appears; only the extras differ (the
// component runner adds an editable completion time for demo accounts), which
// is what `rowExtras` is for.
// ============================================================================

export interface ActivitySidebarProps {
  /** This week's activities, in plan order. */
  weekComponents: JourneyComponent[];
  weekTitle: string;
  weekDateLabel?: string;
  dateLabelFor?: (week: SidebarWeek, index: number) => string | undefined;
  moduleTitle: string;
  /** Every week of the module, for the dropdown list. */
  weeks: SidebarWeek[];
  completedIds: Set<string>;
  kind?: string;
  id?: string;
  /** The component being consumed, if this sidebar sits beside one. */
  currentComponentId?: string;
  /** The quiz being sat, if it sits beside one. Quizzes have no componentId. */
  currentQuizId?: number | null;
  /** Per-row completion time, when there is one to show. */
  completionTimeFor?: (component: JourneyComponent) => string | null;
  /** Anything the host page adds under a completed row. */
  rowExtras?: (component: JourneyComponent, completed: boolean) => ReactNode;
  /** Where each row goes. */
  routeFor: (component: JourneyComponent, week: string) => string;
  /** Catalogue browsers can select a week without leaving their saved material. */
  onSelectWeek?: (week: SidebarWeek) => void;
  /** Source-specific availability; never creates native content or quiz attempts. */
  canOpen?: (component: JourneyComponent) => boolean;
  monthHeadingFor?: (week: SidebarWeek, index: number) => string | null;
  /** Catalogue current week is independent of the week being reviewed; null means completed. */
  currentWeekKey?: string | null;
  accessOpen?: boolean;
  onAccessBlocked?: () => void;
}

const ACTIVITY_TONES: Record<string, { background: string; border: string; color: string }> = {
  reading: { background: '#e8f4ff', border: '#bfdbfe', color: '#0369a1' },
  'reading+quiz': { background: '#e8f4ff', border: '#bfdbfe', color: '#0369a1' },
  video: { background: '#f3e8ff', border: '#ddd6fe', color: '#7e22ce' },
  podcast: { background: '#fce7f3', border: '#fbcfe8', color: '#be185d' },
  quiz: { background: '#fff4d6', border: '#f5d98b', color: '#a16207' },
  assignment: { background: '#ffe9ed', border: '#fecdd3', color: '#be123c' },
  powerpoint: { background: '#ffeddc', border: '#fed7aa', color: '#c2410c' },
  reflection: { background: '#e9eaff', border: '#c7d2fe', color: '#4338ca' },
  'live-session': { background: '#e4f7ee', border: '#bbebd2', color: '#137055' },
  recording: { background: '#e5f5f7', border: '#bae6eb', color: '#0e7490' },
  evidence: { background: '#f1eafd', border: '#ddd0f4', color: '#6d28d9' },
  document: { background: '#f1f5f9', border: '#dbe3ec', color: '#475569' },
  activity: { background: '#f1f5f9', border: '#dbe3ec', color: '#475569' },
};

const ACTIVITY_LABELS: Record<string, string> = {
  reading: 'Reading', video: 'Video', podcast: 'Podcast', quiz: 'Quiz', assignment: 'Assignment',
  'reading+quiz': 'Reading + Quiz',
  powerpoint: 'PowerPoint', reflection: 'Reflection', 'live-session': 'Live session', recording: 'Recording',
  evidence: 'Evidence', document: 'Document', activity: 'Activity',
};

function sidebarActivityMeta(component: JourneyComponent) {
  const raw = component.isQuiz ? 'quiz' : String(component.type || '').trim().toLowerCase().replace(/\s*\+\s*/g, '+').replace(/[\s_]+/g, '-');
  const key = raw === 'audio' ? 'podcast'
    : ['presentation', 'slides', 'ppt'].includes(raw) ? 'powerpoint'
      : ['live-session', 'live'].includes(raw) ? 'live-session'
        : ['recording-placeholder'].includes(raw) ? 'recording'
          : ['workplace-evidence'].includes(raw) ? 'evidence'
            : ['resource', 'file', 'pdf', 'word'].includes(raw) ? 'document'
              : ACTIVITY_TONES[raw] ? raw : 'activity';
  const titleMeta = componentTypeMeta(component.title);
  const label = ACTIVITY_LABELS[key];
  const titleIsLabel = titleMeta.label.trim().toLowerCase() === label.toLowerCase();
  const detail = titleMeta.detail || (!titleIsLabel ? component.title : null);
  const resource = resourceTypeMeta(key === 'reading+quiz' ? 'reading' : key.replace('-', ' '));
  return { key, label, detail, icon: resource.icon, tone: ACTIVITY_TONES[key] };
}

export function ActivitySidebar({
  weekComponents,
  weekTitle,
  weekDateLabel,
  dateLabelFor,
  moduleTitle,
  weeks,
  completedIds,
  kind,
  id,
  currentComponentId,
  currentQuizId,
  completionTimeFor,
  rowExtras,
  routeFor,
  onSelectWeek,
  canOpen,
  monthHeadingFor,
  currentWeekKey,
  accessOpen = true,
  onAccessBlocked,
}: ActivitySidebarProps) {
  const navigate = useNavigate();
  const learnerKind = kind === 'commercial' || kind === 'apprenticeship' ? kind : null;
  // Read the plan whenever there is a week to describe, not only when the list
  // below has more than one row: a single-week module can still fall on a
  // holiday, and its hint has to reach the learner too.
  const schedule = useLiveLearnerRead(learnerKind, id, weeks.length > 0, learningSchedule.read, learningSchedule.peek);
  const moduleIds = new Set(weeks.flatMap(week => week.components.flatMap(component => component.moduleId ? [component.moduleId] : [])));
  const matchingModules = (schedule.data?.modules || []).filter(module => moduleIds.size
    ? moduleIds.has(module.id) : module.title.trim().toLowerCase() === moduleTitle.trim().toLowerCase());
  const plannedModule = matchingModules.length === 1 ? matchingModules[0] : undefined;
  // Which week of the plan is expanded, if any. Picking a week used to navigate
  // straight into one of its activities; it now opens under the row it belongs
  // to and whatever is on screen stays there.
  const [expandedWeek, setExpandedWeek] = useState<string | null>(null);

  // Moving to another activity collapses the list: what was expanded belonged
  // to the page the learner was reading, not the one they opened.
  useEffect(() => {
    if (!onSelectWeek) setExpandedWeek(null);
  }, [currentComponentId, currentQuizId, onSelectWeek]);

  const weekDoneCount = weekComponents.filter((c) => isComponentComplete(c, completedIds)).length;
  const activeWeekIndex = weeks.findIndex((w) => w.active);
  const currentWeekLabel = currentWeekKey !== undefined || activeWeekIndex < 0 ? weekTitle : weekDisplayLabel(weeks, activeWeekIndex);
  const isCurrentWeek = (week: SidebarWeek) => currentWeekKey === undefined ? week.active : week.key === currentWeekKey;
  const weekHeading = currentWeekKey === null ? 'Week activities'
    : 'Current week';
  const contentAvailableFor = canOpen || hasComponentContent;
  const openableFor = canOpen || ((c: JourneyComponent) => hasComponentContent(c) && isNavigableComponent(c));
  const hasUnavailableContent = weekComponents.some((c) => !contentAvailableFor(c));
  const monthHeadings = monthHeadingFor ? weeks.map(monthHeadingFor) : weekMonthHeadings(weeks, plannedModule?.start_date);
  // Only weeks the curriculum team published a holiday hint for appear here.
  const holidayNotes = weekHolidayNotes(plannedModule?.curriculumSlots);
  const currentWeekNote = holidayNotes.get(weeks.find(w => w.active)?.weekId
    || weekComponents.find(c => c.weekId)?.weekId || '');
  const legendItems = [...new Map([...weekComponents, ...weeks.flatMap(week => week.components)]
    .map(component => {
      const meta = sidebarActivityMeta(component);
      return [meta.key, meta] as const;
    })).values()];

  /** Whether this row is the activity the host page is showing. */
  const isCurrentRow = (c: JourneyComponent) => (
    c.isQuiz
      ? currentQuizId != null && c.quizMeta?.quizId === currentQuizId
      : Boolean(c.componentId) && c.componentId === currentComponentId
  );

  return (
    <aside aria-label="Module activities" className="min-w-0 space-y-4 lg:sticky lg:top-4">
      {legendItems.length > 0 && <section aria-label="Activity type key" className="rounded-xl border border-primary-100 bg-white px-4 py-3 shadow-sm">
        <p className="text-[10px] font-bold uppercase tracking-wider text-primary-900">Activity types</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {legendItems.map(meta => <span key={meta.key} data-activity-type={meta.key}
            className="inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] font-semibold"
            style={{ backgroundColor: meta.tone.background, borderColor: meta.tone.border, color: meta.tone.color }}>
            <AppIcon className={`${meta.icon} text-[11px]`} />{meta.label}
          </span>)}
        </div>
      </section>}
      <div className="overflow-hidden rounded-xl border border-emerald-200 bg-emerald-50/70 shadow-sm">
        <div className="border-b border-emerald-100 px-4 py-3">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-primary-600">{weekHeading}</p>
          <h2 className="text-sm font-heading font-bold text-primary-900">{currentWeekLabel || 'This week'}</h2>
          <p className="text-[11px] text-foreground-400 mt-0.5">
            {weekDateLabel && <span className="mb-1 block text-foreground-500">{weekDateLabel}</span>}
            {weekComponents.length} components{' '}
            {weekDoneCount > 0 && <span className="text-emerald-600 font-semibold"> · {weekDoneCount} done</span>}
          </p>
          {hasUnavailableContent && <p className="mt-2 text-[11px] leading-4 text-foreground-500">Locked activities have no learning content available yet.</p>}
          <HolidayNoteHint note={currentWeekNote} className="mt-2" />
        </div>
        <ul className="divide-y divide-emerald-100">
          <PointActivityRows components={weekComponents} current={isCurrentRow} renderRow={(c) => {
            const cm = sidebarActivityMeta(c);
            const isCurrent = isCurrentRow(c);
            const contentAvailable = contentAvailableFor(c);
            const navigable = openableFor(c) && !isCurrent;
            const accessBlocked = navigable && !accessOpen;
            const clickable = navigable && accessOpen;
            const attempts = c.isQuiz ? (c.quizAttempts || []) : [];
            const lastAttempt = attempts.length > 0 ? attempts[attempts.length - 1] : null;
            const completed = isComponentComplete(c, completedIds);
            const completionTime = completed ? completionTimeFor?.(c) ?? null : null;
            return (
              <li key={c.componentId || c.title}>
                <button
                  disabled={!clickable && !accessBlocked}
                  aria-disabled={accessBlocked || undefined}
                  title={!contentAvailable ? 'Learning content is not available yet.' : undefined}
                  onClick={() => accessBlocked ? onAccessBlocked?.() : clickable && navigate(routeFor(c, weekTitle))}
                  className={`w-full flex items-center gap-2.5 px-4 py-2.5 text-left transition-colors ${
                    !contentAvailable || accessBlocked
                      ? 'cursor-not-allowed bg-emerald-100/60 opacity-55'
                      : isCurrent
                        ? 'bg-emerald-100/80'
                        : completed
                          ? `bg-emerald-100/70 ${clickable ? 'hover:bg-emerald-100 cursor-pointer' : 'cursor-default'}`
                          : clickable ? 'bg-emerald-50/30 hover:bg-emerald-100/60 cursor-pointer' : 'bg-emerald-50/30 cursor-default'
                  }`}
                >
                  <span data-activity-type={cm.key} className="w-7 h-7 rounded-lg border flex items-center justify-center shrink-0"
                    style={{ backgroundColor: cm.tone.background, borderColor: cm.tone.border, color: cm.tone.color }}>
                    <AppIcon className={`${cm.icon} text-[12px]`} />
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="block text-[9px] font-semibold uppercase tracking-wider" style={{ color: cm.tone.color }}>{cm.label}</span>
                    <span className={`block text-[13px] font-semibold leading-snug truncate ${
                      isCurrent ? 'text-primary-700' : completed ? 'text-emerald-900' : 'text-primary-800'
                    }`}>
                      {cm.detail || cm.label}
                    </span>
                    {completionTime && (
                      <span className="mt-0.5 flex items-center gap-1 font-mono text-[10px] font-semibold tabular-nums text-emerald-700" title="Time taken">
                        <AppIcon className="ri-timer-line text-[10px]" />
                        {completionTime}
                      </span>
                    )}
                  </span>
                  {c.isQuiz && lastAttempt && (
                    <span className={`shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${
                      lastAttempt.passed ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'
                    }`}>
                      {gradePercent(lastAttempt.grade)}%
                    </span>
                  )}
                  {completed ? (
                    <AppIcon className="ri-checkbox-circle-fill text-emerald-600 text-sm shrink-0" />
                  ) : !contentAvailable || accessBlocked ? (
                    <AppIcon className="ri-lock-line shrink-0 text-sm text-primary-400" />
                  ) : isCurrent ? (
                    <AppIcon className="ri-focus-3-line text-primary-600 text-sm shrink-0" />
                  ) : clickable ? (
                    <AppIcon className="ri-arrow-right-s-line text-primary-500 text-sm shrink-0" />
                  ) : null}
                </button>
                {rowExtras?.(c, completed)}
              </li>
            );
          }} />
        </ul>
      </div>

      {(weeks.length > 1 || onSelectWeek) && (
        <div className="rounded-xl border border-primary-100 bg-white overflow-hidden">
          <div className="px-4 py-3 border-b border-primary-100">
            <h2 className="text-sm font-heading font-bold text-primary-900">{moduleTitle || 'Module'}</h2>
            <p className="text-[11px] text-foreground-400 mt-0.5">{weeks.length} weeks</p>
          </div>
          <ul className="divide-y divide-primary-100">
            {weeks.map((w, index) => {
              const weekKey = w.key || `week-${index}`;
              const weekComplete = w.count > 0 && w.completed >= w.count;
              // A week with nothing in it has nothing to drop down; every other
              // week can be opened, whether or not its activities can be
              // started yet.
              const viewable = !!onSelectWeek || w.count > 0;
              const expanded = viewable && weekKey === expandedWeek;
              const weekNote = w.weekId ? holidayNotes.get(w.weekId) : undefined;
              return (
                <li key={weekKey}>
                  {monthHeadings[index] && <h3 className="border-b border-primary-100 bg-primary-50/80 px-4 py-2 text-[11px] font-bold uppercase tracking-wide text-primary-800">{monthHeadings[index]}</h3>}
                  <button
                    disabled={!viewable}
                    aria-label={`${weekDisplayLabel(weeks, index)}, ${w.count} components${isCurrentWeek(w) ? ', Current' : weekComplete ? ', Done' : ''}`}
                    aria-expanded={viewable ? expanded : undefined}
                    aria-current={onSelectWeek && w.active ? 'step' : undefined}
                    onClick={() => {
                      setExpandedWeek(toggleExpandedWeek(expandedWeek, weekKey));
                      if (!w.count) onSelectWeek?.(w);
                    }}
                    className={`w-full flex items-center gap-2.5 px-4 py-2.5 text-left transition-colors ${
                      !viewable
                        ? 'cursor-not-allowed bg-primary-50/70 opacity-55'
                        : expanded
                          ? 'bg-primary-50/80 cursor-pointer'
                          : weekComplete
                            ? 'bg-emerald-50 text-emerald-900 hover:bg-emerald-100'
                            : 'hover:bg-primary-50/70 cursor-pointer'
                    }`}
                  >
                    <span className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${
                      weekComplete ? 'bg-emerald-100 text-emerald-700' : 'bg-primary-100 text-primary-700'
                    }`}>
                      <AppIcon className={`${weekComplete ? 'ri-check-line' : 'ri-calendar-line'} text-[12px]`} />
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className={`block text-[13px] font-semibold leading-snug truncate ${
                        weekComplete ? 'text-emerald-900' : w.active ? 'text-primary-900' : 'text-primary-800'
                      }`}>
                        {weekDisplayLabel(weeks, index)}
                      </span>
                      <span className={`block text-[10px] ${weekComplete ? 'text-emerald-700' : 'text-foreground-400'}`}>
                        {dateLabelFor?.(w, index) && <span className="mb-1 block text-foreground-500">{dateLabelFor(w, index)}</span>}
                        {w.count} components{weekComplete ? ' complete' : ''}
                      </span>
                    </span>
                    {isCurrentWeek(w) && (
                      <span className="text-[10px] font-semibold text-primary-600 shrink-0">Current</span>
                    )}
                    {weekComplete && !isCurrentWeek(w) && (
                      <span className="text-[10px] font-semibold text-emerald-700 shrink-0">Done</span>
                    )}
                    {!viewable ? (
                      <AppIcon className="ri-lock-line shrink-0 text-sm text-foreground-400" />
                    ) : (
                      <AppIcon
                        className={`shrink-0 text-sm text-primary-500 ri-arrow-${expanded ? 'up' : 'down'}-s-line`}
                      />
                    )}
                  </button>

                  {/* The curriculum team's hint for this week, outside the row's
                      button so it stays readable rather than becoming part of
                      the label that opens the week. */}
                  {weekNote && <HolidayNoteHint note={weekNote} className="mx-4 mb-2.5" />}

                  {/* The week's own activities, under the week they belong to.
                      Everything is listed, including what cannot be started
                      yet, so the count above and the list below agree. */}
                  {expanded && (
                    <ul className="border-t border-primary-100 bg-primary-50/40 divide-y divide-primary-100">
                      <PointActivityRows components={w.components} current={isCurrentRow} renderRow={(component) => {
                        const row = weekComponentRows({ ...w, components: [component] }, completedIds)[0];
                        const rowMeta = sidebarActivityMeta(row.component);
                        const openable = canOpen ? canOpen(row.component) : row.openable;
                        const accessBlocked = openable && !accessOpen;
                        return (
                          <li key={row.component.componentId || row.component.title}>
                            <button
                              type="button"
                              disabled={!openable && !accessBlocked}
                              aria-disabled={accessBlocked || undefined}
                              title={!openable ? 'Learning content is not available yet.' : undefined}
                              onClick={() => accessBlocked ? onAccessBlocked?.() : openable && navigate(routeFor(row.component, w.week))}
                              className={`w-full flex items-center gap-2 pl-11 pr-4 py-2 text-left transition-colors ${
                                openable && accessOpen ? 'hover:bg-primary-50 cursor-pointer' : 'cursor-not-allowed opacity-60'
                              }`}
                            >
                              <span data-activity-type={rowMeta.key} className="w-6 h-6 rounded-md border flex items-center justify-center shrink-0"
                                style={{ backgroundColor: rowMeta.tone.background, borderColor: rowMeta.tone.border, color: rowMeta.tone.color }}>
                                <AppIcon className={`${rowMeta.icon} text-[11px]`} />
                              </span>
                              <span className="flex-1 min-w-0">
                                <span className="block text-[9px] font-semibold uppercase tracking-wider" style={{ color: rowMeta.tone.color }}>
                                  {rowMeta.label}
                                </span>
                                <span className={`block text-[12px] font-medium leading-snug truncate ${
                                  row.complete ? 'text-emerald-900' : 'text-primary-800'
                                }`}>
                                  {rowMeta.detail || rowMeta.label}
                                </span>
                              </span>
                              {row.complete ? (
                                <AppIcon className="ri-checkbox-circle-fill text-emerald-600 text-[13px] shrink-0" />
                              ) : !openable ? (
                                <AppIcon className="ri-lock-line text-primary-400 text-[13px] shrink-0" />
                              ) : (
                                <AppIcon className="ri-arrow-right-s-line text-primary-500 text-[13px] shrink-0" />
                              )}
                            </button>
                          </li>
                        );
                      }} />
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </aside>
  );
}
