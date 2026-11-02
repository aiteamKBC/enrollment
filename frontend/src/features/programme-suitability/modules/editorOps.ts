import { ordered, isModuleGroup, type ProgrammeModule } from './configuration';

export const newModule = (order: number): ProgrammeModule => ({ id: `module-${crypto.randomUUID()}`, itemType: 'MODULE', title: 'New module',
  sessions: null, description: '', order, active: true, selectable: false });

export function asModuleGroup(item: ProgrammeModule, children: ProgrammeModule[]): ProgrammeModule {
  return { id: item.id, itemType: 'GROUP', title: item.title, description: item.description,
    order: item.order, active: item.active, items: children };
}

export function moduleGroups(items: ProgrammeModule[]): ProgrammeModule[] {
  return items.flatMap((item) => [...(isModuleGroup(item) ? [item] : []), ...moduleGroups(item.items ?? [])]);
}

/** Move only an actual module; its stable ID and all module settings survive. */
export function assignModuleParent(items: ProgrammeModule[], moduleId: string, parentId: string): ProgrammeModule[] {
  const find = (values: ProgrammeModule[]): ProgrammeModule | undefined => values.find((item) => item.id === moduleId)
    ?? values.map((item) => find(item.items ?? [])).find(Boolean);
  const module = find(items);
  const parent = parentId ? moduleGroups(items).find((group) => group.id === parentId) : undefined;
  if (!module || isModuleGroup(module) || (parentId && !parent)) return items;
  const siblings = parent ? parent.items ?? [] : items;
  if (siblings.some((item) => item.id === moduleId)) return items;
  const moved = { ...module, order: Math.max(-1, ...siblings.map((item) => item.order)) + 1 };
  const relocate = (values: ProgrammeModule[]): ProgrammeModule[] => values.filter((item) => item.id !== moduleId).map((item) => {
    if (!item.items && item.id !== parentId) return item;
    const children = relocate(item.items ?? []);
    return { ...item, items: item.id === parentId ? [...children, moved] : children };
  });
  const next = relocate(items);
  return parent ? next : [...next, moved];
}

export function moveOrdered<T extends { order: number }>(items: T[], index: number, delta: number): T[] {
  const sorted = ordered(items); const target = index + delta;
  if (target < 0 || target >= sorted.length) return items;
  [sorted[index], sorted[target]] = [sorted[target], sorted[index]];
  return sorted.map((item, order) => ({ ...item, order }));
}
