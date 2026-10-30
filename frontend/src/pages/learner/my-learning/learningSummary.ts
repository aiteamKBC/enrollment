import type { LearnerDetail } from '@/api/learnerDetail';
import type { StudentActivityItem, StudentActivityResponse } from '@/api/studentActivity';
import { completedComponentIds, isComponentComplete, quizAttemptsFor, type JourneyComponent } from '@/utils/learnerJourney';

export type Schedule = Pick<StudentActivityItem, 'date' | 'month' | 'week_start' | 'week_end' | 'date_needs_review' | 'date_source'> & { due_timing?: string };
export type SubjectEntry = { id: string; title: string; category: string; completed: boolean; position: number; schedule: Schedule; week?: string; legacy?: StudentActivityItem; native?: JourneyComponent; bestScorePercent?: number | null };
export type SubjectCardSummary = { activityCount: number; completedActivityCount: number; progress: number; status: 'Completed' | 'In progress' | 'Upcoming'; nextTitle?: string | null; finalTestPassed: boolean };
export type Subject = { card?: SubjectCardSummary; id: string; title: string; source: 'legacy' | 'current'; moduleId?: string | null; activities: SubjectEntry[]; recordedHistory?: boolean; catalogueProgress?: boolean; catalogueCount?: number; acceptedHours?: number; previousLearningFor?: string; importedCourseRef?: string; replacementCourseRef?: string; archivedActivities?: SubjectEntry[]; aggregateProgress?: number; transferredProgress?: number; transferPending?: boolean; demoProgress?: boolean };
type BuilderSubject = { id: string; title: string };
type ActivitySource = { module_id: string; group_id: number; activity_id: number };
export type CoverMetadata = { covers: Record<string, string>; activity_dates?: Record<string, Schedule>; current_subjects?: BuilderSubject[]; builder_subjects?: Record<string, BuilderSubject>; activity_sources?: Record<string, ActivitySource>; previous_learning_groups?: Record<string, string>; imported_courses?: Record<string, string>; replacement_courses?: Record<string, string>; approved_transfers?: Record<string, string>; module_credits?: Record<string, { progress: number; actualHours: number; transferredProgress?: number; transferPending?: boolean; demoProgress?: boolean }> };
export type UnifiedLearningSummary = {
  subjects: Subject[];
  subjectCount: number;
  activityCount: number;
  completedActivityCount: number;
  percent: number;
  previousLearning?: { activityCount: number; completedActivityCount: number; percent: number };
};
export type LearningSummaryDetail = Pick<LearnerDetail,
  'modules' | 'components' | 'retiredQuizComponents' | 'quizAttempts' | 'videoProgress' | 'componentProgress'>;
export type LearningSummaryActivity = Pick<StudentActivityResponse,
  'subjects' | 'activities' | 'progress_basis' | 'activity_sources' | 'replacement_hours'>;

export function subjectRefs(data: LearningSummaryActivity | null, real: LearningSummaryDetail | null) {
  return [...new Set([
    ...(data?.subjects || []).map(subject => `legacy:${subject.id}`),
    ...(data?.activities || []).map(activity => `legacy:${activity.group_id}`),
    ...(real?.components || []).flatMap(component => component.moduleId ? [`current:${component.moduleId}`] : []),
  ])].sort().join(',');
}


function scheduleForDate(value?: string | null): Schedule {
  const date = value?.slice(0, 10);
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return { date: null, month: 'undated' };
  const day = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(day.getTime())) return { date: null, month: 'undated' };
  day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
  const week_start = day.toISOString().slice(0, 10);
  day.setUTCDate(day.getUTCDate() + 6);
  return { date, month: date.slice(0, 7), week_start, week_end: day.toISOString().slice(0, 10) };
}


function nativeActivitySchedule(schedule: Schedule | undefined, sessionDate?: string | null): Schedule {
  // Older metadata used the upload date. It cannot override a session date or
  // move a whole future programme into the month its components were created.
  if (!schedule || ['original_created_at', 'source_date', 'undated'].includes(schedule.date_source || '')) {
    return scheduleForDate(sessionDate);
  }
  return schedule;
}


export function subjectsFrom(data: LearningSummaryActivity | null, real: LearningSummaryDetail | null, metadata?: CoverMetadata | null): Subject[] {
  const { activity_dates: dates = {}, current_subjects: currentSubjects = [], builder_subjects: builderSubjects = {} } = metadata || {};
  const recordedHistory = data?.progress_basis === 'recorded_activities';
  const catalogueProgress = data?.progress_basis === 'catalogue_activities';
  const subjects = new Map<string, Subject>();
  for (const subject of data?.subjects || []) subjects.set(`legacy:${subject.id}`, { id: `legacy:${subject.id}`, title: subject.name, source: 'legacy', moduleId: subject.module_id, activities: [], recordedHistory, catalogueProgress, catalogueCount: subject.catalogue_count, acceptedHours: subject.accepted_hours });
  for (const item of data?.activities || []) {
    const key = `legacy:${item.group_id}`;
    const subject = subjects.get(key) || { id: key, title: item.group_name || 'Unnamed subject', source: 'legacy' as const, activities: [] };
    if (!subject.activities.some((entry) => entry.id === item.activity_id)) subject.activities.push({ id: item.activity_id, title: item.activity, category: item.category, completed: item.completed, position: item.position || 0, schedule: item.month ? item : scheduleForDate(item.date), legacy: item });
    subjects.set(key, subject);
  }
  const importedCourses = metadata?.imported_courses || {};
  // Replace only a proven import with the exact owned course returned by
  // student-activity. A shared title or curriculum link is not an identity.
  const links = Object.entries({ ...importedCourses, ...metadata?.replacement_courses });
  const approved = metadata?.approved_transfers || {};
  const replaced = new Set(links
    .filter(([ref, course]) => ref.startsWith('current:') && subjects.get(course)?.source === 'legacy'
      && links.filter(([, old]) => old === course).length === 1 && !approved[ref])
    .map(([ref]) => ref));
  const replacedTitles = new Set<string>();
  const completed = completedComponentIds(real);
  for (const subject of currentSubjects) {
    const key = `current:${subject.id}`;
    if (replaced.has(key)) {
      replacedTitles.add(subject.title.trim().toLowerCase());
      continue;
    }
    // While current-plan detail is unavailable, keep the existing historical
    // browser rather than publishing an empty duplicate of a linked course.
    if (!real && [...subjects.values()].some(previous => previous.source === 'legacy'
      && (previous.moduleId === subject.id || builderSubjects[previous.id]?.id === subject.id
        || metadata?.replacement_courses?.[key] === previous.id
        || metadata?.previous_learning_groups?.[previous.id] === `current:${subject.id}`))) continue;
    if (!subjects.has(key)) subjects.set(key, { id: key, title: subject.title, source: 'current', activities: [],
      ...(importedCourses[key] ? { importedCourseRef: importedCourses[key] } : {}) });
  }
  // Removed quizzes the learner passed follow the live plan, so they keep their
  // module and count as completed work there.
  const planEntries = [...(real?.components || []), ...(real?.retiredQuizComponents || [])];
  for (const [index, item] of planEntries.entries()) {
    const key = item.moduleId ? `current:${item.moduleId}` : `unlinked:${item.module}`;
    if (replaced.has(key)) {
      replacedTitles.add((item.module || '').trim().toLowerCase());
      continue;
    }
    const subject = subjects.get(key) || { id: key, title: item.module || 'Unnamed subject', source: 'current' as const, activities: [],
      ...(importedCourses[key] ? { importedCourseRef: importedCourses[key] } : {}) };
    const component: JourneyComponent = { ...item,
      previousLearning: approved[key] === item.previousLearning?.courseRef ? item.previousLearning : undefined, title: item.component,
      quizAttempts: item.isQuiz && item.quizMeta ? quizAttemptsFor(item, real?.quizAttempts) : undefined };
    const id = item.componentId || `quiz:${item.quizMeta?.quizId ?? `${item.week}:${index}`}`;
    const isComplete = isComponentComplete(component, completed);
    const scores = (component.quizAttempts || []).map((attempt) => attempt.grade * 100).filter(Number.isFinite);
    const bestScorePercent = scores.length ? Math.max(...scores) : null;
    if (!subject.activities.some((entry) => entry.id === id)) subject.activities.push({
      id, title: component.title, category: component.type || 'activity', completed: isComplete, bestScorePercent, position: index,
      schedule: nativeActivitySchedule(dates[id], component.sessionDate), week: item.week || undefined, native: component,
    });
    subjects.set(key, subject);
  }
  for (const title of real?.modules || []) {
    // The title-only fallback must not recreate an import whose id was just
    // replaced, including a Builder copy renamed after the original course.
    if (replacedTitles.has(title.trim().toLowerCase())) continue;
    if ([...subjects.values()].some(subject => subject.title.trim().toLowerCase() === title.trim().toLowerCase())) continue;
    subjects.set(`unlinked:${title}`, { id: `unlinked:${title}`, title, source: 'current', activities: [] });
  }
  // Approval switches catalogues. The server supplies both aggregate credit and
  // exact completed matches; those matches are already included in that credit.
  for (const [currentRef, oldRef] of Object.entries(approved)) {
    const current = subjects.get(currentRef);
    if (current?.source === 'current') {
      subjects.set(currentRef, { ...current, importedCourseRef: undefined });
      subjects.delete(oldRef);
    }
  }
  return [...subjects.values()].map((subject) => ({ ...subject,
    ...(metadata?.module_credits?.[subject.id] ? {
      aggregateProgress: metadata.module_credits[subject.id].progress,
      transferredProgress: metadata.module_credits[subject.id].transferredProgress,
      transferPending: metadata.module_credits[subject.id].transferPending,
      demoProgress: metadata.module_credits[subject.id].demoProgress,
      acceptedHours: metadata.module_credits[subject.id].actualHours,
    } : {}),
    title: builderSubjects[subject.id]?.title || subject.title,
    previousLearningFor: subject.source === 'legacy' && subjects.get(metadata?.previous_learning_groups?.[subject.id] || '')?.source === 'current'
      ? metadata?.previous_learning_groups?.[subject.id] : undefined }))
    .sort((a, b) => a.title.localeCompare(b.title) || a.id.localeCompare(b.id));
}

/** Current-plan progress and imported previous learning have separate totals. */
export function buildUnifiedLearningSummary(
  data: LearningSummaryActivity | null,
  real: LearningSummaryDetail | null,
  metadata?: CoverMetadata | null,
): UnifiedLearningSummary {
  const subjects = subjectsFrom(data, real, metadata);
  // Deduplicate only when the placement has an explicit stable identity. A
  // title or position is never an identity fallback.
  const seen = new Set<string>();
  for (const subject of subjects) {
    subject.activities = subject.activities.filter((entry) => {
      const key = entry.legacy?.source_activity_id != null
        ? `legacy:${entry.legacy.group_id}:${entry.legacy.catalogue_kind || "material"}:${entry.legacy.activity_id}`
        : entry.native?.componentId ? `component:${entry.native.componentId}`
          : entry.native?.quizMeta?.quizId ? `quiz:${entry.native.quizMeta.quizId}` : null;
      if (!key) return true;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }
  const currentSubjects = subjects.filter(subject => subject.source === 'current');
  const previousSubjects = subjects.filter(subject => subject.source === 'legacy');
  const previousTotal = previousSubjects.reduce((sum, subject) => sum + subject.activities.length, 0);
  const previousDone = previousSubjects.reduce((sum, subject) => sum + subject.activities.filter(entry => entry.completed).length, 0);
  const activityCount = currentSubjects.reduce((sum, subject) => sum + subject.activities.length, 0);
  const completedActivityCount = currentSubjects.reduce(
    (sum, subject) => sum + subject.activities.filter((entry) => entry.completed).length,
    0,
  );
  return {
    subjects,
    previousLearning: { activityCount: previousTotal, completedActivityCount: previousDone,
      percent: previousTotal ? Math.round(previousDone / previousTotal * 10000) / 100 : 0 },
    subjectCount: subjects.filter(subject => !subject.previousLearningFor).length,
    activityCount,
    completedActivityCount,
    percent: activityCount ? Math.round(currentSubjects.reduce((sum, subject) => sum +
      (subject.aggregateProgress ?? (subject.activities.length ? subject.activities.filter(entry => entry.completed).length / subject.activities.length * 100 : 0)) * subject.activities.length, 0) / activityCount * 100) / 100 : 0,
  };
}

