import type { LearnerDetail } from '@/api/learnerDetail';
import type { PlanMonth, TrainingPlanContract } from '@/api/trainingPlanDashboard';
import type { CoverMetadata } from '../my-learning/SubjectWorkspace';
import { completedComponentIds, componentTypeMeta, hasComponentContent, isMonthlyAssignment } from '@/utils/learnerJourney';
import { dateKey } from '../training-plan-timeline/model';

const PLAN_ASSIGNMENT_SUFFIX = /\s*[-(–—]?\s*(?:Coach[ -]Led Assignment|Assignment)\s*\)?$/i;
const PLAN_ROW_NOT_A_TOPIC = /^(?:LMS[- ]?Activity|Additional job activities|(?:January|February|March|April|May|June|July|August|September|October|November|December)[ -]*)$/i;

/** The assignments the learner's Training Plan names for a month (its
 * "… Coach Led Assignment" rows), as the plan words them. Shown beside the
 * portal assignment so learners can match the two; never matched automatically. */
export function trainingPlanAssignments(month: PlanMonth | undefined): string[] {
  const names = (month?.activities || []).filter(row => /assignment/i.test(row.method || ''))
    .map(row => (row.title || '').replace(PLAN_ASSIGNMENT_SUFFIX, '').replace(/^[\s\-–—]+|[\s\-–—]+$/g, ''))
    .filter(name => name && !PLAN_ROW_NOT_A_TOPIC.test(name));
  return [...new Set(names)];
}

export const statusLabels: Record<string, string> = {
  todo: 'To do', draft: 'Draft saved', submitted_for_tutor_review: 'Submitted for review',
  accepted: 'Accepted', completed: 'Completed', referred: 'Changes requested',
  returned: 'Changes requested', rejected: 'Changes requested', partial: 'Partially accepted',
};

export function monthName(month: string) {
  return month ? new Date(`${month}-01T12:00:00Z`).toLocaleDateString('en-GB', {
    month: 'long', year: 'numeric', timeZone: 'Europe/London',
  }) : 'Not scheduled';
}

export function groupMonthlyAssignments(real: LearnerDetail, metadata: CoverMetadata | null,
  contract: TrainingPlanContract | null, statuses: Record<string, string> | null, submissionCounts: Record<string, number> = {}) {
  const completed = completedComponentIds(real);
  const seen = new Set<string>();
  const assignments = real.components.filter(component => {
    if (!component.componentId || !isMonthlyAssignment(component, real.components) || seen.has(component.componentId)) return false;
    seen.add(component.componentId);
    return true;
  }).map(component => {
    const id = component.componentId!;
    const schedule = metadata?.activity_dates?.[id];
    // Import timestamps are not delivery dates; ambiguous dates stay unscheduled.
    const useSchedule = schedule && !['original_created_at', 'source_date', 'undated'].includes(schedule.date_source || '');
    const date = useSchedule ? schedule.date_needs_review ? '' : dateKey(schedule.date) : dateKey(component.sessionDate);
    const dueDate = useSchedule && !schedule.date_needs_review && schedule.due_timing?.trim().toLowerCase() === 'end of week'
      ? dateKey(schedule.week_end) : '';
    const month = date.slice(0, 7);
    const status = statuses?.[id] || real.componentMarkingStatus?.[id]?.status
      || (completed.has(id) ? 'completed' : statuses ? 'todo' : '');
    const submitted = ['submitted_for_tutor_review', 'accepted', 'completed'].includes(status);
    const revise = ['referred', 'returned', 'rejected', 'partial'].includes(status);
    const hasContent = hasComponentContent({ ...component, title: component.component });
    const awaitingBrief = !hasContent && status === 'todo';
    // "Assignment · Title" -> the title alone; the page already says it is an assignment.
    const title = componentTypeMeta(component.component).detail || component.component;
    return { ...component, id, title, date, dueDate, month, status, submitted, awaitingBrief,
      submissionCount: submissionCounts[id],
      marking: real.componentMarkingStatus?.[id],
      action: awaitingBrief ? 'Awaiting assignment brief' : status === 'draft' ? 'Continue assignment'
        : revise ? 'Revise assignment' : submitted ? 'View submission' : status === 'todo' ? 'Start assignment' : 'Open assignment' };
  });
  const groups = new Map<string, typeof assignments>();
  for (const assignment of assignments) {
    const rows = groups.get(assignment.month) || [];
    rows.push(assignment);
    groups.set(assignment.month, rows);
  }
  return [...groups].sort(([a], [b]) => a && b ? a.localeCompare(b) : a ? -1 : b ? 1 : 0).map(([month, rows]) => ({
    month, label: contract?.months[month]?.label?.trim() || monthName(month),
    topics: contract?.months[month]?.topics || [],
    planAssignments: trainingPlanAssignments(contract?.months[month]),
    assignments: rows.sort((a, b) => a.date.localeCompare(b.date)),
    submitted: rows.filter(row => row.submitted).length,
  }));
}

export type AssignmentMonth = ReturnType<typeof groupMonthlyAssignments>[number];
export type MonthlyAssignmentRow = AssignmentMonth['assignments'][number];

export function assignmentToday(today = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(today);
  const part = (type: string) => parts.find(item => item.type === type)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

export function isOverdueAssignment(assignment: MonthlyAssignmentRow, today = assignmentToday()) {
  return Boolean(assignment.dueDate && assignment.dueDate < today
    && ['todo', 'draft', 'referred', 'returned', 'rejected', 'partial'].includes(assignment.status));
}

export function defaultAssignmentMonth(groups: AssignmentMonth[], today = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit' }).formatToParts(today);
  const month = `${parts.find(part => part.type === 'year')?.value}-${parts.find(part => part.type === 'month')?.value}`;
  return groups.find(group => group.month === month) || groups.find(group => group.month >= month)
    || groups.filter(group => group.month).at(-1) || groups[0];
}
