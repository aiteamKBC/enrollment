/**
 * What a Module settings save will do to the module's weeks, said before it
 * happens.
 *
 * The drawer's PATCH carries a week *count*, never the week list, so the server
 * resizes the stored weeks itself (`resize_authoring_week_structure_preserving_content`
 * in curriculum_api/views.py): growing appends empty weeks, shrinking drops
 * trailing weeks that hold nothing and stops at the first one with authored
 * content. Done silently, the Module Builder then met weeks that had vanished
 * from under it and reported them as "someone else deleted Week 9" -- the
 * author's own save, attributed to a stranger. This mirrors that rule so the
 * drawer can say what will happen, and why, and let the author decline.
 */
import type { ModuleWeek } from '../../module-builder/moduleAuthoringData';

type WeekLike = Partial<Pick<ModuleWeek, 'weekNumber' | 'title' | 'summary' | 'components' | 'ksbMappings' | 'learningOutcomes'>>;

const plain = (value: unknown) => String(value ?? '').trim().toLowerCase().replace(/\s+/g, ' ');

/** Mirrors `week_holds_authored_content`: anything past a default "Week N" shell. */
export function weekHoldsAuthoredContent(week: WeekLike | null | undefined): boolean {
  if (!week) return false;
  if (week.components?.length || week.ksbMappings?.length || week.learningOutcomes?.length) return true;
  if (String(week.summary ?? '').trim()) return true;
  const title = plain(week.title);
  if (!title) return false;
  return title !== plain(`Week ${Number(week.weekNumber) || 0}`);
}

export interface WeekCountChange {
  before: number;
  after: number;
  /** Week numbers the save drops: always empty trailing weeks. */
  removed: number[];
  /** How many empty weeks the save appends. */
  added: number;
  /** The count the save asks for, which can be less than `after` when an authored week stops the shrink. */
  requested: number;
}

/** The weeks after the server resizes `weeks` to `requested`, or null when nothing moves. */
export function predictWeekCountChange(weeks: WeekLike[], requested: number): WeekCountChange | null {
  const before = weeks.length;
  const target = Math.max(0, Math.round(requested) || before);
  if (target === before) return null;
  if (target > before) return { before, after: target, removed: [], added: target - before, requested: target };
  const kept = [...weeks];
  while (kept.length > target && !weekHoldsAuthoredContent(kept[kept.length - 1])) kept.pop();
  if (kept.length === before) return null;
  const removed = weeks.slice(kept.length).map((week, index) => Number(week.weekNumber) || kept.length + index + 1);
  return { before, after: kept.length, removed, added: 0, requested: target };
}

function weekList(numbers: number[]): string {
  if (numbers.length === 1) return `Week ${numbers[0]}`;
  const contiguous = numbers.every((value, index) => index === 0 || value === numbers[index - 1] + 1);
  return contiguous
    ? `Weeks ${numbers[0]} to ${numbers[numbers.length - 1]}`
    : `Weeks ${numbers.slice(0, -1).join(', ')} and ${numbers[numbers.length - 1]}`;
}

/**
 * The sentence the confirmation shows: the count now, the count after, and the
 * reason -- the Weeks box the author changed, or the module's own saved count
 * when the box was left alone.
 */
export function weekCountChangeText(change: WeekCountChange, boxChanged: boolean): string {
  const cause = boxChanged
    ? `you set Weeks to ${change.requested}`
    : `this module's saved week count is ${change.requested}, but it holds ${change.before} weeks`;
  const what = change.added
    ? `${change.added} empty week${change.added === 1 ? '' : 's'} will be added at the end`
    : `${weekList(change.removed)} ${change.removed.length === 1 ? 'is' : 'are'} empty and will be removed`;
  const stopped = change.after > change.requested
    ? ` It stops at ${change.after} because Week ${change.after} has content, and weeks with content are never removed.`
    : '';
  return `Saving will make this module ${change.after} week${change.after === 1 ? '' : 's'} instead of ${change.before}, because ${cause}. `
    + `${what.charAt(0).toUpperCase()}${what.slice(1)}.${stopped}`;
}

/** The stored module, as far as the resize reads it. */
export interface StoredWeekStructure {
  weekStructure?: WeekLike[];
  weeksNumber?: number;
  weeks?: number;
}

/**
 * What this save does to the stored module's weeks: the count sent when the
 * Weeks box changed, else the stored count, as the server resolves it. Null
 * when the weeks stay as they are, or when no stored copy is to hand -- the
 * save then goes ahead exactly as it did before this check existed.
 */
export function weekCountChangeForSave(
  stored: StoredWeekStructure | null,
  sentWeeks: number | undefined,
): WeekCountChange | null {
  const weeks = stored?.weekStructure || [];
  if (!weeks.length) return null;
  const storedCount = Number(stored?.weeksNumber) || Number(stored?.weeks) || weeks.length;
  return predictWeekCountChange(weeks, sentWeeks || storedCount);
}
