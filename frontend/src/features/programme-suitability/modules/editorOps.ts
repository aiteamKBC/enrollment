import { ordered, type ProgrammeModule } from './configuration';

export const newModule = (order: number): ProgrammeModule => ({ id: `module-${crypto.randomUUID()}`, title: 'New module',
  sessions: null, description: '', order, active: true, selectable: false });

export function moveOrdered<T extends { order: number }>(items: T[], index: number, delta: number): T[] {
  const sorted = ordered(items); const target = index + delta;
  if (target < 0 || target >= sorted.length) return items;
  [sorted[index], sorted[target]] = [sorted[target], sorted[index]];
  return sorted.map((item, order) => ({ ...item, order }));
}
