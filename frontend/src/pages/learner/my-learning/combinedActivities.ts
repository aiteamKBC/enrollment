import type { SubjectEntry } from './learningSummary';

/** Presentation only. The catalogue's original evidence, totals and IDs stay intact. */
export function combinedActivities(entries: SubjectEntry[]) {
  const parents = new Map(entries.map(entry => [entry.id, entry]));
  const quizzes = new Map<string, SubjectEntry[]>();
  for (const entry of entries) {
    const legacy = entry.legacy;
    const parent = parents.get(legacy?.source_material_activity_id || '');
    if (legacy?.catalogue_kind !== 'quiz' || !parent?.legacy
      || parent.legacy.catalogue_kind !== 'material' || parent.legacy.can_open_material === false
      || parent.legacy.group_id !== legacy.group_id
      || !/^reading\s*\+\s*quiz$/i.test(parent.category)) continue;
    quizzes.set(parent.id, [...(quizzes.get(parent.id) || []), entry]);
  }
  // Ambiguous companion links remain separate, with their own source identities.
  const pairs = new Map([...quizzes].filter(([, children]) => children.length === 1)
    .map(([parent, children]) => [parent, children[0]]));
  const aliases = new Map([...pairs].map(([parent, quiz]) => [quiz.id, parent]));
  const activities = entries.filter(entry => !aliases.has(entry.id)).map(entry => {
    const quiz = pairs.get(entry.id);
    if (!quiz) return entry;
    return { ...entry, category: 'Reading + Quiz',
      completed: entry.completed && quiz.completed,
      bestScorePercent: quiz.bestScorePercent ?? entry.bestScorePercent };
  });
  return { activities, aliases };
}
