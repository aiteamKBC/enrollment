// ============================================================================
// The week list beside an open activity.
//
// Picking a week there used to navigate straight into one of its components, so
// a learner looking ahead was dropped into an activity they never chose. It now
// expands in place, and this module decides what each expanded week shows.
// ============================================================================
import {
  buildLearnerJourney,
  hasComponentContent,
  isComponentComplete,
  isOpenableComponent,
  type JourneyComponent,
} from '@/utils/learnerJourney';
import type { LearnerDetail } from '@/api/learnerDetail';
import { dateKey } from '@/pages/learner/training-plan-timeline/model';

/** One week as the sidebar lists it. */
export interface SidebarWeek {
  key?: string;
  /** The authored curriculum week (curriculum.weeks.id), when this week has one. */
  weekId?: string;
  week: string;
  count: number;
  completed: number;
  active: boolean;
  components: JourneyComponent[];
}

export interface WeekComponentRow {
  component: JourneyComponent;
  /** Whether opening it would show the learner something. */
  openable: boolean;
  complete: boolean;
}

/** True when a sidebar row leads somewhere: a quiz, or any openable component. */
export function isNavigableComponent(component: JourneyComponent): boolean {
  return (component.isQuiz && hasComponentContent(component)) || isOpenableComponent(component);
}

/**
 * The rows for one expanded week, in plan order.
 *
 * Everything the week holds is listed, including what cannot be opened yet —
 * the point of looking ahead is to see what is coming, and hiding half of it
 * would make the count in the row above disagree with the list under it.
 */
export function weekComponentRows(
  week: SidebarWeek | null | undefined,
  completedIds: Set<string>,
): WeekComponentRow[] {
  return (week?.components ?? []).map((component) => ({
    component,
    openable: hasComponentContent(component) && isNavigableComponent(component),
    complete: isComponentComplete(component, completedIds),
  }));
}

/**
 * Which week is expanded after clicking `week`, given what is expanded now.
 *
 * Clicking the open one closes it, so the list always returns to the plain
 * overview the learner started from.
 */
export function toggleExpandedWeek(current: string | null, week: string): string | null {
  return current === week ? null : week;
}


/** Where one activity sits in the plan: its week, its module, its siblings. */
export interface ActivityPlacement {
  moduleTitle: string;
  weekTitle: string;
  weekLabel: string;
  weekComponents: JourneyComponent[];
  weeks: SidebarWeek[];
}

/** Repeated week names need their plan position to distinguish occurrences. */
export function weekDisplayLabel(weeks: { week: string }[], index: number): string {
  const title = weeks[index].week;
  return weeks.filter((week) => week.week === title).length > 1
    ? `Week ${index + 1} · ${title}`
    : title;
}

/** Name each four-week block from the module's training-plan start month. */
export function weekMonthHeadings(weeks: { week: string }[], moduleStartDate?: string | null): (string | null)[] {
  const start = dateKey(moduleStartDate);
  let teachingWeeks = 0;
  let previousMonth = 0;
  return weeks.map(({ week }) => {
    if (/^(?:introduction|induction|extra\b|additional\b|undated\b|module assessments?\b|assessments?\b)/i.test(week.trim())) {
      previousMonth = 0;
      return null;
    }
    teachingWeeks += 1;
    const month = Math.ceil(teachingWeeks / 4);
    let heading: string | null = null;
    if (month !== previousMonth) {
      if (start) {
        // Anchor to day 1 so starts on the 29th–31st cannot skip February.
        const date = new Date(`${start.slice(0, 7)}-01T12:00:00Z`);
        date.setUTCMonth(date.getUTCMonth() + month - 1);
        heading = date.toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
      } else {
        heading = `Month ${month}`;
      }
    }
    previousMonth = month;
    return heading;
  });
}

/**
 * Find an activity in the learner's plan and describe where it sits.
 *
 * `match` is what identifies it — a component id for everything the component
 * runner opens, a quiz id for a quiz, which has no component id of its own.
 * Returns null when the activity is not in the plan at all, which is the case
 * the sidebar treats as "nothing to list".
 */
export function placeActivity(
  detail: LearnerDetail | null,
  match: { componentId?: string | null; quizId?: number | string | null },
  completedIds: Set<string>,
): ActivityPlacement | null {
  if (!detail) return null;
  const wantedComponent = match.componentId ? String(match.componentId) : '';
  const wantedQuiz = match.quizId != null && match.quizId !== '' ? String(match.quizId) : '';
  if (!wantedComponent && !wantedQuiz) return null;

  for (const module of buildLearnerJourney(detail)) {
    for (const week of module.weeks) {
      const found = week.components.some((component) => (
        wantedComponent
          ? component.componentId === wantedComponent
          : String(component.quizMeta?.quizId ?? '') === wantedQuiz
      ));
      if (!found) continue;
      return {
        moduleTitle: module.module,
        weekTitle: week.week,
        weekLabel: weekDisplayLabel(module.weeks, module.weeks.indexOf(week)),
        weekComponents: week.components,
        weeks: module.weeks.map((w, index) => ({
          key: w.components[0]?.weekId || `week-${index}`,
          weekId: w.components.find((c) => c.weekId)?.weekId || undefined,
          week: w.week,
          count: w.components.length,
          completed: w.components.filter((c) => isComponentComplete(c, completedIds)).length,
          active: w === week,
          components: w.components,
        })),
      };
    }
  }
  return null;
}
