import { traceWeeklyLearning } from './weeklyLearningDiagnostics';
import { createCachedResource } from './cachedRequest';
import { LearnerReadError, readLearnerJson, invalidateLearnerReads } from './learnerRead';
import type { LearnerKind } from './learnerDetail';
import { validateWeeklyLearning, studentWeekId, selectedNavigation } from './weeklyLearningContract';
import type { WeeklyLearningResponse } from './weeklyLearningContract';
export { studentWeekId } from './weeklyLearningContract';
export type { WeeklyLearningResponse, CanonicalWeek, CanonicalSelectedWeek } from './weeklyLearningContract';

export type WeeklyLearningDetail = WeeklyLearningResponse & { id: string };
export type WeeklyLearningWeek = WeeklyLearningResponse & { weekId: string; moduleId: string };

const weekKeys = new Set<string>();
let activeWeekRequests = 0;
const weekResource = createCachedResource<WeeklyLearningWeek>('learner-dashboard-week-detail', async key => {
  activeWeekRequests += 1;
  try {
    const [kind, id, moduleId, weekId] = JSON.parse(key) as string[];
    const raw = await readLearnerJson<WeeklyLearningResponse>(`/learner_api/dashboard/${kind}/${encodeURIComponent(id)}/weekly/plan/${encodeURIComponent(weekId)}/?moduleId=${encodeURIComponent(moduleId)}`);
    const response = validateWeeklyLearning(raw, true);
    const navigation = selectedNavigation(response);
    if (!navigation || studentWeekId(navigation) !== weekId || navigation.moduleId !== moduleId) {
      throw new LearnerReadError('The week response was incomplete. Please try again.', 200, 'invalid_week');
    }
    const data = { ...response, weekId, moduleId };
    return data;
  } finally {
    activeWeekRequests -= 1;
  }
}, 5 * 60_000);

const detailKey = (kind: LearnerKind, id: string, moduleId: string, weekId: string) => JSON.stringify([kind, id, moduleId, weekId]);

export function fetchWeeklyLearningWeek(kind: LearnerKind, id: string, moduleId: string, weekId: string) {
  const key = detailKey(kind, id, moduleId, weekId);
  weekKeys.add(key);
  return weekResource.read(key);
}

export async function prefetchWeeklyLearningWeek(kind: LearnerKind, id: string, moduleId: string, weekId: string) {
  if (activeWeekRequests || peekWeeklyLearningWeek(kind, id, moduleId, weekId)) return;
  try {
    await fetchWeeklyLearningWeek(kind, id, moduleId, weekId);
  } catch {
    traceWeeklyLearning('request-error', `${kind}:${id}`, 'Adjacent week prefetch failed; selection will retry.');
  }
}

export function peekWeeklyLearningWeek(kind: LearnerKind, id: string, moduleId: string, weekId: string, staleForMs = 0) {
  return weekResource.peek(detailKey(kind, id, moduleId, weekId), staleForMs);
}

export function primeWeeklyLearningWeek(kind: LearnerKind, id: string, data: WeeklyLearningDetail) {
  const navigation = selectedNavigation(data);
  if (!navigation) return;
  const weekId = studentWeekId(navigation);
  const key = detailKey(kind, id, navigation.moduleId, weekId);
  weekKeys.add(key);
  weekResource.prime(key, { ...data, weekId, moduleId: navigation.moduleId });
}

const initialKeys = new Set<string>();
const initialKey = (kind: LearnerKind, id: string, moduleId?: string | null) => `${kind}:${id}${moduleId ? `|${encodeURIComponent(moduleId)}` : ''}`;
const weeklyResource = createCachedResource<WeeklyLearningDetail>('learner-dashboard-weekly', async key => {
  const [learner, moduleId] = key.split('|');
  traceWeeklyLearning('request-start', key);
  let raw: WeeklyLearningResponse;
  try {
    raw = await readLearnerJson<WeeklyLearningResponse>(`/learner_api/dashboard/${learner.replace(':', '/')}/weekly/plan/${moduleId ? `?moduleId=${moduleId}` : ''}`);
    traceWeeklyLearning('request-success', key);
  } catch (error) {
    traceWeeklyLearning('request-error', key);
    throw error;
  }
  const response = validateWeeklyLearning(raw, true);
  const data: WeeklyLearningDetail = { ...response, id: learner.slice(learner.indexOf(':') + 1) };
  return data;
});

export function fetchWeeklyLearning(kind: LearnerKind, id: string, options: { revalidate?: boolean; moduleId?: string | null } = {}): Promise<WeeklyLearningDetail> {
  const key = initialKey(kind, id, options.moduleId);
  traceWeeklyLearning('read', key, `revalidate=${!!options.revalidate}; fresh=${!!weeklyResource.peek(key)}`);
  initialKeys.add(key);
  const previous = weeklyResource.peek(key, 5 * 60_000);
  return weeklyResource.read(key, { revalidate: options.revalidate }).then(data => {
    // Side effects belong only to the accepted read, never an invalidated one.
    if (weeklyResource.peek(key) !== data || previous === data) return data;
    const navigation = new Map(data.weeks.map(week => [studentWeekId(week), week]));
    const before = new Map(previous?.weeks.map(week => [studentWeekId(week), week]));
    for (const detail of weekKeys) {
      const [detailKind, detailId, , weekId] = JSON.parse(detail) as string[];
      if (`${detailKind}:${detailId}` !== `${kind}:${id}`) continue;
      if (!navigation.has(weekId) || (before.has(weekId) && JSON.stringify(before.get(weekId)) !== JSON.stringify(navigation.get(weekId)))) {
        weekResource.invalidate(detail);
        weekKeys.delete(detail);
      }
    }
    return data;
  });
}

/** A live entry, or with `staleForMs` a recent one to show while revalidating. */
export function peekWeeklyLearning(kind: LearnerKind, id: string, staleForMs = 0, moduleId?: string | null): WeeklyLearningDetail | undefined {
  return weeklyResource.peek(initialKey(kind, id, moduleId), staleForMs);
}

/** Called by invalidateLearnerDetailCache: the same saves change this plan. */
export type WeeklyProgressScope = { activityId?: string; quizId?: number };

export function invalidateWeeklyLearning(key?: string, scope?: WeeklyProgressScope): void {
  traceWeeklyLearning('invalidate', key ?? 'all');
  weeklyResource.invalidate(key);
  for (const initial of initialKeys) {
    if (!key || initial.split('|')[0] === key) { weeklyResource.invalidate(initial); initialKeys.delete(initial); }
  }
  invalidateWeekDetails(key, scope);
}

/** Mutation boundary: invalidate this learner before notifying mounted reads. */
export function invalidateWeeklyProgress(kind: LearnerKind, id: string, activityId?: string): void {
  invalidateWeeklyLearning(`${kind}:${id}`, activityId ? { activityId } : undefined);
  invalidateLearnerReads();
}

function invalidateWeekDetails(key?: string, scope?: WeeklyProgressScope): void {
  const { activityId, quizId } = scope ?? {};
  const known = activityId || quizId != null ? [...weekKeys].flatMap(detail => {
    const [kind, id] = JSON.parse(detail) as string[];
    if (`${kind}:${id}` !== key) return [];
    return weekResource.peek(detail, Number.POSITIVE_INFINITY)?.selectedWeek?.activities
      .filter(activity => activity.id === activityId || quizId != null && activity.quizId === quizId) ?? [];
  }) : [];
  const quizzes = new Set(known.map(activity => activity.quizId).filter(id => id != null));
  for (const detail of weekKeys) {
    const [kind, id] = JSON.parse(detail) as string[];
    if (key && `${kind}:${id}` !== key) continue;
    const cached = weekResource.peek(detail, Number.POSITIVE_INFINITY);
    // Unknown mappings and in-flight details still invalidate conservatively.
    // A known activity leaves independent cached weeks valid; shared quiz
    // placements invalidate together, even across weeks/modules.
    if (known.length && cached?.selectedWeek && !cached.selectedWeek.activities.some(activity =>
      activity.id === activityId || activity.quizId != null && quizzes.has(activity.quizId))) continue;
    weekResource.invalidate(detail);
    weekKeys.delete(detail);
  }
}
