import type { Subject, SubjectEntry, CoverMetadata } from './SubjectWorkspace';
import type { PlanModule } from '@/api/trainingPlanDashboard';
import type { LearningSchedule } from '@/api/learnerOverview';
import type { LearnerDetail } from '@/api/learnerDetail';
import type { OverviewWeek } from '@/api/learnerOverview';
import type { CertificateTemplateSummary } from '@/api/learnerCertificates';
import { learnerHeaderPlan } from '@/pages/workspace/learner/learnerHeaderPlan';
import { dateKey, weekKey } from '@/pages/learner/training-plan-timeline/model';
import { hasComponentContent } from '@/utils/learnerJourney';
import { orderSourceWeeks } from './sourceWeekOrder';

export type LearningWeek = { id: string; label: string; title: string; start: string | null; end: string | null; month?: string; activities: SubjectEntry[];
  sourceSection?: boolean; sourceOrder?: number | null;
  /** The authored curriculum week (curriculum.weeks.id) this card stands for, when it has one. */
  weekId?: string };
export const learningToday = () => new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/London' });
export const learningDate = (date: string) => new Date(`${date.slice(0, 10)}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
export const subjectPercent = (subject: Subject) => subject.aggregateProgress ?? (subject.card ? subject.card.progress : subject.activities.length ? Math.round(subject.activities.filter(a => a.completed).length / subject.activities.length * 10000) / 100 : 0);

/** Enrolment remains visible before any learning has started. */
export function subjectLearningStatus(subject: Subject): 'Completed' | 'In progress' | 'Upcoming' {
  if (subject.card) return subject.card.status;
  if (subject.aggregateProgress != null && subject.aggregateProgress > 0) return subject.aggregateProgress === 100 ? 'Completed' : 'In progress';
  if (subject.activities.length && subject.activities.every(activity => activity.completed)) return 'Completed';
  const started = subject.activities.some(activity => {
    const legacy = activity.legacy;
    const status = legacy?.status?.trim().toLowerCase().replace(/[\s-]+/g, '_');
    return activity.completed || legacy?.video_started || legacy?.reading_viewed || legacy?.quiz_attempted
      || (legacy?.new_attempt_count ?? 0) > 0 || (activity.native?.quizAttempts?.length ?? 0) > 0
      || ['started', 'in_progress', 'submitted', 'failed', 'completed', 'passed'].includes(status || '');
  });
  return started ? 'In progress' : 'Upcoming';
}

export function certificateEligible(subject: Subject, template: CertificateTemplateSummary | null) {
  const total = subject.card?.activityCount ?? subject.activities.length;
  const completed = subject.card?.completedActivityCount ?? subject.activities.filter(activity => activity.completed).length;
  const percent = total ? Math.round(completed / total * 10000) / 100 : 0;
  const finalTestPassed = subject.card?.finalTestPassed ?? subject.activities.some(activity => {
    const component = activity.native;
    return component?.isQuiz
      && (component.title || '').toLowerCase().includes('final')
      && component.quizAttempts?.some(attempt => attempt.passed === true);
  });
  return !!template && total > 0
    && (subject.id.startsWith('current:') || subject.id.startsWith('unlinked:'))
    && percent >= Number(template.minimumProgress || 0)
    && (!template.requireFinalTest || finalTestPassed);
}

/** Only authored end-of-week rules create deadlines; ordinary dated content does not. */
export function learningDeadlines(subjects: Subject[], today = learningToday()): OverviewWeek['deadlines'] {
  const deadlines = new Map<string, OverviewWeek['deadlines'][number]>();
  for (const subject of subjects) {
    for (const activity of subject.activities) {
      const type = activity.category.trim().toLowerCase().replace(/-/g, '_');
      const dueTiming = activity.schedule.due_timing?.trim().toLowerCase();
      const date = activity.schedule.week_end;
      if (!['assignment', 'checkpoint'].includes(type) || activity.completed
        || dueTiming !== 'end of week' || !date || date < today) continue;
      const componentId = activity.native?.componentId;
      const id = componentId ? `native:${componentId}` : activity.id;
      deadlines.set(id, { id, title: activity.title, type: type as 'assignment' | 'checkpoint', date, subjectId: subject.id });
    }
  }
  return [...deadlines.values()].sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id));
}

/** Only teaching dates can anchor a delivery week; upload/evidence dates cannot. */
function deliveryDate(entry: SubjectEntry) {
  const source = entry.schedule.date_source;
  if (entry.schedule.date_needs_review) return '';
  if (['title', 'section_title', 'builder_section_title', 'builder_session', 'builder_week'].includes(source || '')) {
    return dateKey(entry.schedule.date);
  }
  return dateKey(entry.native?.sessionDate);
}

/** Unscheduled history keeps its calendar placement without becoming a session. */
function activityWeekRange(entry: SubjectEntry) {
  const delivery = deliveryDate(entry);
  const start = delivery || dateKey(entry.schedule.week_start) || weekKey(entry.schedule.date || '') || null;
  const lastDay = start ? new Date(`${start}T12:00:00Z`) : null;
  if (lastDay) lastDay.setUTCDate(lastDay.getUTCDate() + 6);
  const end = (!delivery && dateKey(entry.schedule.week_end)) || lastDay?.toISOString().slice(0, 10) || null;
  return { start, end };
}

/** Calendar weeks remain one card even when their activities cross a month boundary. */
export function subjectWeeks(subject: Subject): LearningWeek[] {
  const groups = new Map<string, LearningWeek>();
  const hasSections = subject.activities.some(entry => entry.legacy?.source_section);
  const deliveryDays = [...new Set(subject.activities.map(deliveryDate).filter(Boolean))].sort();
  for (const entry of subject.activities) {
    const section = entry.legacy?.source_section;
    if (hasSections && entry.legacy) {
      const key = section ? `section:${entry.legacy.group_id}:${section.id}` : 'unplaced';
      const group: LearningWeek = groups.get(key) || { id: key, label: section?.title || 'Additional stored activities',
        title: section?.title || 'Additional stored activities', start: null, end: null, activities: [],
        sourceSection: true, sourceOrder: section?.order };
      group.activities.push(entry);
      groups.set(key, group);
      continue;
    }
    const source = entry.schedule.date_source;
    const special = source === 'introduction' ? 'introduction' : source === 'extra_activity' ? 'extra' : '';
    const { start, end: rangeEnd } = activityWeekRange(entry);
    const nextDelivery = deliveryDate(entry) && deliveryDays[deliveryDays.indexOf(start!) + 1];
    // Start on the delivery day and stop before an earlier next session.
    // Missing activity weeks must not stretch this week across a schedule gap.
    const nextBoundary = nextDelivery ? new Date(Date.parse(`${nextDelivery}T12:00:00Z`) - 86400000).toISOString().slice(0, 10) : null;
    const end = nextBoundary && rangeEnd && nextBoundary < rangeEnd ? nextBoundary : rangeEnd;
    const label = entry.week || entry.legacy?.section_title || '';
    const month = /^\d{4}-(0[1-9]|1[0-2])$/.test(entry.schedule.month || '') ? entry.schedule.month! : undefined;
    const key = special === 'introduction' ? special : special === 'extra' ? `extra:${label}`
      : start || entry.native?.weekId || (month ? `${month}:${label || 'activities'}` : label || 'undated');
    const monthLabel = !special && !start && month
      ? new Date(`${month}-01T12:00:00Z`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : '';
    const group = groups.get(key) || { id: key, label: special === 'introduction' ? 'Introduction' : special === 'extra' ? 'Extra activities' : monthLabel,
      title: label, start: special ? null : start, end: special ? null : end, month: special ? undefined : month, activities: [] };
    group.activities.push(entry);
    // A calendar week can hold activities from more than one authored week; the
    // first one named owns the card, matching the title it already carries.
    if (!group.weekId && entry.native?.weekId) group.weekId = entry.native.weekId;
    if (!special && end && (!group.end || end > group.end)) group.end = end;
    groups.set(key, group);
  }
  for (const week of groups.values()) {
    if (!week.sourceSection) continue;
    const dates = [...new Set(week.activities.map(deliveryDate).filter(Boolean))].sort();
    week.start = dates[0] || null;
    if (week.start) {
      const last = new Date(`${week.start}T12:00:00Z`);
      last.setUTCDate(last.getUTCDate() + 6);
      const next = deliveryDays.find(day => day > week.start!);
      week.end = last.toISOString().slice(0, 10);
      if (next && next <= week.end) week.end = new Date(Date.parse(`${next}T12:00:00Z`) - 86400000).toISOString().slice(0, 10);
    }
    // Upload months may place undated content, but never define a teaching week.
    const months = [...new Set(week.activities.map(entry => entry.schedule.month)
      .filter((month): month is string => !!month && /^\d{4}-\d{2}$/.test(month)))].sort();
    week.month = week.start?.slice(0, 7) || (months.length === 1 ? months[0] : undefined);
  }
  const rank = (week: LearningWeek) => week.id === 'introduction' ? -1 : week.id.startsWith('extra:') ? 2 : week.start || week.month ? 0 : 1;
  const ordered = [...groups.values()].sort((a, b) => rank(a) - rank(b) || (a.month || a.start?.slice(0, 7) || '').localeCompare(b.month || b.start?.slice(0, 7) || '') || (a.start || a.id).localeCompare(b.start || b.id, undefined, { numeric: true }));
  const sectionsByMonth = new Map<string, LearningWeek[]>();
  for (const week of ordered.filter(week => week.sourceSection)) {
    const month = week.month || 'undated';
    sectionsByMonth.set(month, [...(sectionsByMonth.get(month) || []), week]);
  }
  for (const [month, weeks] of sectionsByMonth) sectionsByMonth.set(month, orderSourceWeeks(weeks));
  let number = 0;
  return ordered.map(original => {
    const week = original.sourceSection ? sectionsByMonth.get(original.month || 'undated')!.shift()! : original;
    const activities = [...week.activities].sort((a, b) => week.sourceSection
      ? (a.legacy?.source_section?.component_order ?? a.position) - (b.legacy?.source_section?.component_order ?? b.position)
      : (a.schedule.date || '').localeCompare(b.schedule.date || '') || a.position - b.position);
    const label = week.label || (week.id === 'undated' ? 'Undated activities' : (number++, /^week\s*\d+/i.exec(week.title)?.[0] || `Week ${number}`));
    const title = week.title && !/^week\s*\d+\s*$/i.test(week.title) ? week.title
      : activities.find(a => a.legacy?.section_title)?.legacy?.section_title || activities[0]?.title || 'Learning activities';
    return { ...week, label, title, activities };
  });
}

/** Keep old Monday-based bookmarks usable when they identify exactly one delivery week. */
export function resolveSubjectWeek(weeks: LearningWeek[], requested?: string | null) {
  const exact = weeks.find(week => week.id === requested);
  if (exact || !requested) return exact;
  const previous = weeks.filter(week => week.activities.some(entry =>
    (dateKey(entry.schedule.week_start) || weekKey(entry.schedule.date || '')) === requested || deliveryDate(entry) === requested));
  return previous.length === 1 ? previous[0] : undefined;
}

/** Keep assigned weeks visible even before their materials have been published. */
export function subjectMapWeeks(subject: Subject, real?: LearnerDetail | null, metadata?: CoverMetadata | null, schedule?: LearningSchedule | null) {
  const weeks = subjectWeeks(subject);
  // Before transfer, a stored source curriculum owns its sections; the new
  // Builder plan must not append unrelated empty weeks to the old course.
  if (subject.source === 'legacy' && subject.activities.some(entry => entry.legacy?.source_section)) return weeks;
  const moduleIds = new Set([
    subject.id.startsWith('current:') ? subject.id.slice(8) : '',
    metadata?.builder_subjects?.[subject.id]?.id, schedule?.moduleLinks?.[subject.id]?.id,
    ...subject.activities.map(a => a.native?.moduleId),
  ].filter(Boolean));
  for (const planned of real?.week || []) {
    const belongs = planned.moduleId ? moduleIds.has(planned.moduleId) : planned.module === subject.title;
    if (!belongs || !planned.week) continue;
    const represented = weeks.some(week => week.activities.some(a => planned.weekId ? a.native?.weekId === planned.weekId : a.week === planned.week)
      || week.title === planned.week || week.label.toLowerCase() === planned.week.toLowerCase());
    if (represented) continue;
    const id = planned.weekId || planned.week;
    if (!weeks.some(week => week.id === id)) weeks.push({ id, label: planned.week, title: planned.week, start: null, end: null, activities: [], weekId: planned.weekId || undefined });
  }
  return weeks;
}

/** Resolve verified Builder links to the catalogue currently visible to the learner. */
export function resolveLearningSubject(subjects: Subject[], requested?: string | null, metadata?: CoverMetadata | null, schedule?: LearningSchedule | null) {
  if (!requested) return undefined;
  const exact = subjects.find(s => s.id === requested);
  if (exact) return exact;
  const unified = subjects.filter(s => s.replacementCourseRef === requested);
  if (unified.length === 1) return unified[0];
  const moduleId = requested.replace(/^current:/, '');
  const importedCourse = metadata?.replacement_courses?.[`current:${moduleId}`] || metadata?.imported_courses?.[`current:${moduleId}`];
  const original = importedCourse && subjects.find(s => s.id === importedCourse && s.source === 'legacy');
  if (original) return original;
  const approved = Object.entries(metadata?.approved_transfers || {}).filter(([, old]) => old === requested);
  if (approved.length === 1) return subjects.find(s => s.id === approved[0][0]);
  const links = { ...metadata?.builder_subjects, ...schedule?.moduleLinks };
  const matches = subjects.filter(s => links[s.id]?.id === moduleId || s.moduleId === moduleId || s.activities.some(a => a.native?.moduleId === moduleId)
    || s.activities.some(a => a.legacy && Object.values(metadata?.activity_sources || {}).some(source => source.module_id === moduleId && source.group_id === a.legacy!.group_id)));
  if (matches.length === 1) return matches[0];
  const title = metadata?.current_subjects?.find(s => s.id === moduleId)?.title || schedule?.modules?.find(m => m.id === moduleId)?.title;
  const named = title ? subjects.filter(s => s.title.trim().toLowerCase() === title.trim().toLowerCase()) : [];
  return named.length === 1 ? named[0] : undefined;
}

type LearningPlanSelection = {
  status: 'current' | 'next' | 'previous' | 'undated' | 'empty' | 'unavailable';
  entries: { subject: Subject; module: PlanModule }[];
};

/** Only the assigned training-plan teaching period can make a module current.
 * Keep the header's placement rules and its next/previous choices, but preserve
 * that distinction instead of labelling every fallback as current. */
export function learningPlanSelection(subjects: Subject[], real: Pick<LearnerDetail, 'programme' | 'cohort' | 'group'> | null, metadata?: CoverMetadata | null, schedule?: LearningSchedule | null, today = learningToday()): LearningPlanSelection {
  if (!schedule) return { status: 'unavailable', entries: [] };
  const normalise = (value?: string | null) => (value || '').trim().toLowerCase();
  const fields = [['programme_name', real?.programme], ['cohort_name', real?.cohort], ['group_name', real?.group]] as const;
  const compatible = (schedule.modules || []).filter(module => fields.every(([key, expected]) =>
    !normalise(expected) || !normalise(module[key]) || normalise(module[key]) === normalise(expected)));
  const plan = learnerHeaderPlan(compatible, real || {}, today);
  const first = plan.modules[0];
  if (!first) return { status: 'empty', entries: [] };
  const start = dateKey(first.start_date), end = dateKey(first.end_date);
  const status = start && end && start <= today && end >= today ? 'current'
    : start && start > today ? 'next' : end && end < today ? 'previous' : 'undated';
  const entries = plan.modules.flatMap(module => {
    const subject = resolveLearningSubject(subjects, `current:${module.id}`, metadata, schedule);
    return subject ? [{ subject, module }] : [];
  });
  return { status, entries };
}

export function currentLearningSubject(subjects: Subject[], real: Pick<LearnerDetail, 'programme' | 'cohort' | 'group'> | null, metadata?: CoverMetadata | null, schedule?: LearningSchedule | null, today = learningToday()) {
  const selection = learningPlanSelection(subjects, real, metadata, schedule, today);
  return selection.status === 'current' ? selection.entries[0]?.subject : undefined;
}

/** Continue with an unfinished current module, then the next dated module,
 * then any remaining subject when the plan has no usable dates. */
export function recommendedLearningSubject(subjects: Subject[], real: Pick<LearnerDetail, 'programme' | 'cohort' | 'group'> | null, metadata?: CoverMetadata | null, schedule?: LearningSchedule | null, today = learningToday()) {
  const hasRemaining = (subject: Subject) => subject.card ? subject.card.completedActivityCount < subject.card.activityCount : subject.activities.some(activity => !activity.completed);
  const selection = learningPlanSelection(subjects, real, metadata, schedule, today);
  const planned = selection.entries.find(entry => hasRemaining(entry.subject))?.subject;
  if (['current', 'next'].includes(selection.status) && planned) return planned;

  const next = [...(schedule?.modules || [])]
    .filter(module => dateKey(module.start_date) > today)
    .sort((a, b) => dateKey(a.start_date).localeCompare(dateKey(b.start_date)))
    .map(module => resolveLearningSubject(subjects, `current:${module.id}`, metadata, schedule))
    .find((subject): subject is Subject => !!subject && hasRemaining(subject));
  return next || subjects.find(hasRemaining);
}

export function nextLearningWeek(subject: Subject, today = learningToday()) {
  const weeks = subjectWeeks(subject);
  const pending = weeks.filter(w => w.activities.some(a => !a.completed));
  return pending.find(w => w.start && w.start <= today && (w.end || w.start) >= today) || pending[0] || weeks[0];
}

export function currentLearningWeek(subject: Subject, today = learningToday()) {
  return subjectWeeks(subject).find(week => week.start && week.end && week.start <= today && week.end >= today
    && week.activities.some(entry => !entry.schedule.date_needs_review
      && !['original_created_at', 'source_date'].includes(entry.schedule.date_source || '')));
}

/** Continue follows the dated current week; completion only chooses an activity within it. */
export function continuingLearningWeek(subject: Subject, today = learningToday()) {
  return currentLearningWeek(subject, today) || nextLearningWeek(subject, today);
}

/** Open the current teaching week even when it is already complete. */
export function subjectOpeningActivity(subject: Subject, requestedWeek?: LearningWeek, today = learningToday()) {
  const week = requestedWeek || continuingLearningWeek(subject, today);
  const available = week?.activities.filter(entry => entry.native && hasComponentContent(entry.native)) || [];
  return available.find(entry => !entry.completed) || available[0];
}

export function learningHref(view: 'catalogue' | 'map', kind?: string, learnerId?: string, subject?: string, week?: string) {
  const base = view === 'map' ? '/learner/learning-plan/modules' : '/learner/my-learning';
  const path = kind && learnerId ? `${base}/${encodeURIComponent(kind)}/${encodeURIComponent(learnerId)}` : base;
  const query = new URLSearchParams();
  if (subject) query.set('subject', subject);
  if (week) query.set('week', week);
  return `${path}${query.size ? `?${query}` : ''}`;
}

export function groupSubjectActivities(activities: SubjectEntry[]) {
  const months = new Map<string, Map<string, SubjectEntry[]>>();
  for (const activity of activities) {
    const introduction = activity.schedule.date_source === 'introduction';
    const extra = activity.schedule.date_source === 'extra_activity';
    const month = introduction ? 'introduction' : extra ? 'extra' : activity.schedule.month || activity.schedule.date?.slice(0, 7) || 'undated';
    const week = extra ? activity.legacy?.section_title || activity.week || 'Additional activities' : activity.schedule.week_start || activity.week || 'undated';
    if (!months.has(month)) months.set(month, new Map());
    const weeks = months.get(month)!;
    if (!weeks.has(week)) weeks.set(week, []);
    weeks.get(week)!.push(activity);
  }
  const rank = (month: string) => month === 'introduction' ? -1 : month === 'extra' ? 2 : month === 'undated' ? 1 : 0;
  return [...months].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b)).map(([month, weeks]) => ({
    month, weeks: [...weeks].sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true })).map(([week, items]) => ({
      week, activities: [...items].sort((a, b) => (a.schedule.date || '').localeCompare(b.schedule.date || '') || a.position - b.position || a.title.localeCompare(b.title)),
    })),
  }));
}

export function nativeHref(entry: SubjectEntry, kind?: string, learnerId?: string) {
  const component = entry.native;
  if (!component || !kind || !learnerId || !hasComponentContent(component)) return null;
  const path = component.isQuiz && component.quizMeta ? `quiz/${kind}/${learnerId}/${component.quizMeta.quizId}`
    : component.type === 'video' && component.videoUrl && component.componentId ? `video/${kind}/${learnerId}/${component.componentId}`
      : component.componentId ? `component/${kind}/${learnerId}/${component.componentId}` : null;
  return path ? `/learner/${path}?week=${encodeURIComponent(entry.week || '')}` : null;
}
