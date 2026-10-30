import type { LearningWeek } from './subjectLearning';

/** Honour all retained ordering facts, even when some source ordinals are absent. */
export function orderSourceWeeks(weeks: LearningWeek[]) {
  if (weeks.every(week => week.sourceOrder != null) && new Set(weeks.map(week => week.sourceOrder)).size === weeks.length) {
    return [...weeks].sort((a, b) => a.sourceOrder! - b.sourceOrder!);
  }
  const edges = new Map(weeks.map(week => [week.id, new Set<string>()]));
  const constrain = (ordered: LearningWeek[]) => {
    for (let index = 1; index < ordered.length; index++) edges.get(ordered[index - 1].id)!.add(ordered[index].id);
  };
  const weekNumber = (week: LearningWeek) => /^week\s*(\d+)\b/i.exec(week.title)?.[1];
  const numbered = weeks.filter(week => weekNumber(week) != null);
  const ordinals = weeks.filter(week => week.sourceOrder != null);
  // Equal ordinals/labels cannot establish an ordering between distinct sections.
  if (new Set(ordinals.map(week => week.sourceOrder)).size === ordinals.length) {
    constrain([...ordinals].sort((a, b) => a.sourceOrder! - b.sourceOrder!));
  }
  if (new Set(numbered.map(weekNumber)).size === numbered.length) {
    constrain([...numbered].sort((a, b) => Number(weekNumber(a)) - Number(weekNumber(b))));
  }
  const dated = weeks.filter(week => week.start);
  if (new Set(dated.map(week => week.start)).size === dated.length) {
    constrain([...dated].sort((a, b) => a.start!.localeCompare(b.start!)));
  }
  const remaining = [...weeks].sort((a, b) =>
    (a.sourceOrder ?? Infinity) - (b.sourceOrder ?? Infinity)
    || (Number(weekNumber(a) ?? Infinity) - Number(weekNumber(b) ?? Infinity))
    || a.title.localeCompare(b.title, undefined, { numeric: true }) || a.id.localeCompare(b.id));
  const result: LearningWeek[] = [];
  while (remaining.length) {
    const next = remaining.findIndex(week => !remaining.some(other => edges.get(other.id)!.has(week.id)));
    // Conflicting retained metadata must not drop a section or hang navigation.
    result.push(remaining.splice(next < 0 ? 0 : next, 1)[0]);
  }
  return result;
}
