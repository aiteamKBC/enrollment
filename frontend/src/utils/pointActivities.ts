/** A point is display navigation over the original activities, not a new
 * completion object. Preserve authored reading/audio pairs and move only the
 * point's quiz to the end. Untagged activities retain their normal rows. */
export type PointActivity = {
  pointNumber?: number | null;
  moduleId?: string | null;
  weekId?: string | null;
  type?: string | null;
  isQuiz?: boolean;
};

export type PointActivityGroup<T> = { key: string; pointNumber: number | null; activities: T[] };

export function groupPointActivities<T extends PointActivity>(activities: readonly T[]): PointActivityGroup<T>[] {
  const groups: PointActivityGroup<T>[] = [];
  const points = new Map<string, PointActivityGroup<T>>();
  for (const [index, activity] of activities.entries()) {
    const point = activity.pointNumber;
    if (typeof point !== 'number' || !Number.isSafeInteger(point) || point <= 0) {
      groups.push({ key: `activity:${index}`, pointNumber: null, activities: [activity] });
      continue;
    }
    const key = JSON.stringify([activity.moduleId || '', activity.weekId || '', point]);
    let group = points.get(key);
    if (!group) {
      group = { key, pointNumber: point, activities: [] };
      points.set(key, group);
      groups.push(group);
    }
    group.activities.push(activity);
  }
  const isQuiz = (activity: T) => activity.isQuiz || activity.type?.trim().toLowerCase() === 'quiz';
  return groups.map(group => group.pointNumber === null ? group : {
    ...group,
    activities: [...group.activities.filter(activity => !isQuiz(activity)), ...group.activities.filter(isQuiz)],
  });
}
