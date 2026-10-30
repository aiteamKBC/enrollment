/**
 * Pure edits on a wizard layout, for the builder. Each returns a new layout and
 * never mutates its input, so the builder can compare against what was loaded
 * to know whether anything is unpublished.
 */
import { BUILTIN_ITEMS, isMovable } from '../wizard/layout/registry';
import { dropdownOptions, itemLabel } from '../wizard/layout/resolve';
import type { CustomFieldType, LayoutItem, LayoutStep, WizardLayout } from '../wizard/layout/types';

const mapSteps = (layout: WizardLayout, fn: (s: LayoutStep) => LayoutStep): WizardLayout => ({ steps: layout.steps.map(fn) });

const swap = <T,>(list: T[], from: number, to: number): T[] => {
  if (to < 0 || to >= list.length || from === to) return list;
  const next = [...list];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
};

export function moveStep(layout: WizardLayout, slug: string, delta: -1 | 1): WizardLayout {
  const i = layout.steps.findIndex((s) => s.slug === slug);
  return i === -1 ? layout : { steps: swap(layout.steps, i, i + delta) };
}

export function updateStep(layout: WizardLayout, slug: string, patch: Partial<LayoutStep>): WizardLayout {
  return mapSteps(layout, (s) => (s.slug === slug ? { ...s, ...patch } : s));
}

/** A new, empty step at the end. Its slug is temporary — the server assigns the real one. */
export function addStep(layout: WizardLayout, label: string): { layout: WizardLayout; slug: string } {
  const slug = `new-step-${Date.now().toString(36)}`;
  return { layout: { steps: [...layout.steps, { slug, label, builtin: false, hidden: false, items: [] }] }, slug };
}

/**
 * Move an item one place up or down among the step's shown items — removed
 * items keep their slot but are skipped over, so the arrows always move the
 * item past something the admin can actually see.
 */
export function moveItem(layout: WizardLayout, slug: string, key: string, delta: -1 | 1): WizardLayout {
  return mapSteps(layout, (s) => {
    if (s.slug !== slug) return s;
    const visible = s.items.map((it, i) => ({ it, i })).filter(({ it }) => !it.hidden);
    const pos = visible.findIndex(({ it }) => it.key === key);
    const target = visible[pos + delta];
    if (pos === -1 || !target) return s;
    return { ...s, items: swap(s.items, visible[pos].i, target.i) };
  });
}

export function updateItem(layout: WizardLayout, key: string, patch: Partial<LayoutItem>): WizardLayout {
  return mapSteps(layout, (s) => ({ ...s, items: s.items.map((it) => (it.key === key ? { ...it, ...patch } : it)) }));
}

/** Put a block (or custom field) at the end of another step. */
export function moveItemToStep(layout: WizardLayout, key: string, toSlug: string): WizardLayout {
  let moving: LayoutItem | undefined;
  const without = mapSteps(layout, (s) => {
    const found = s.items.find((it) => it.key === key);
    if (!found) return s;
    moving = found;
    return { ...s, items: s.items.filter((it) => it.key !== key) };
  });
  if (!moving) return layout;
  const item = moving;
  return mapSteps(without, (s) => (s.slug === toSlug ? { ...s, items: [...s.items, item] } : s));
}

/** Add a custom field at the end of a step. Its key is temporary until published. */
export function addCustomField(layout: WizardLayout, slug: string, field: Omit<LayoutItem, 'key' | 'builtin' | 'hidden'>): { layout: WizardLayout; key: string } {
  const key = `new_${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
  const item: LayoutItem = { ...field, key, builtin: false, hidden: false };
  return { layout: mapSteps(layout, (s) => (s.slug === slug ? { ...s, items: [...s.items, item] } : s)), key };
}

/** A follow-up question asked only when the dropdown is answered `option`. */
export interface FollowUpDraft {
  option: string;
  /** Present when the follow-up already exists in the layout. */
  key?: string;
  label: string;
  type: CustomFieldType;
  required: boolean;
  /** Its own choices, when the follow-up is a dropdown. */
  options?: string[];
}

/** Put `item` straight after the item `afterKey`, on that item's step. */
function insertAfter(layout: WizardLayout, afterKey: string, item: LayoutItem): WizardLayout {
  return mapSteps(layout, (s) => {
    const at = s.items.findIndex((it) => it.key === afterKey);
    if (at === -1) return s;
    const items = [...s.items];
    items.splice(at + 1, 0, item);
    return { ...s, items };
  });
}

/**
 * Save a custom field from the field editor — and, for a dropdown, its
 * per-answer follow-up questions. Each follow-up is a field of its own, shown
 * only for its answer; new ones go straight after the dropdown (after any
 * follow-ups it already has), existing ones keep their place. `removed`
 * follow-ups go the way removeItem sends them.
 */
export function saveCustomField(
  layout: WizardLayout,
  slug: string,
  key: string | null,
  fields: Partial<LayoutItem>,
  followUps: FollowUpDraft[] = [],
  removed: string[] = [],
): { layout: WizardLayout; key: string } {
  let next = layout;
  let fieldKey = key;
  if (fieldKey) {
    next = updateItem(next, fieldKey, fields);
  } else {
    const added = addCustomField(next, slug, fields as Omit<LayoutItem, 'key' | 'builtin' | 'hidden'>);
    next = added.layout;
    fieldKey = added.key;
  }
  for (const k of removed) next = removeItem(next, k);

  const items = () => next.steps.flatMap((s) => s.items);
  for (const f of followUps) {
    const condition = { field: fieldKey, values: [f.option] };
    const existing = f.key ? items().find((it) => it.key === f.key) : undefined;
    if (existing) {
      // A published follow-up keeps its type: its column already has one.
      const type = existing.column ? existing.type ?? f.type : f.type;
      next = updateItem(next, existing.key, {
        label: f.label,
        required: f.required,
        condition,
        hidden: false,
        type,
        options: type === 'dropdown' ? f.options ?? existing.options ?? [] : undefined,
      });
      continue;
    }
    // After the dropdown and the follow-ups already placed under it.
    const siblings = items().filter((it) => it.key === fieldKey || it.condition?.field === fieldKey);
    const anchor = siblings[siblings.length - 1]?.key ?? fieldKey;
    const newKey = `new_${Date.now().toString(36)}${Math.floor(Math.random() * 100000)}`;
    next = insertAfter(next, anchor, { key: newKey, builtin: false, hidden: false, label: f.label, type: f.type, required: f.required, condition, ...(f.type === 'dropdown' ? { options: f.options ?? [] } : {}) });
  }
  return { layout: next, key: fieldKey };
}

/** The follow-ups a dropdown already has: shown fields that depend on exactly one of its answers. */
export function existingFollowUps(layout: WizardLayout, key: string): LayoutItem[] {
  return layout.steps.flatMap((s) =>
    s.items.filter((it) => !it.builtin && !it.hidden && it.condition?.field === key && it.condition.values.length === 1)
  );
}

/**
 * Remove an item from the wizard. A field never yet published simply goes; a
 * built-in item or a published custom field is hidden instead, so its column
 * and the answers in it are kept and it can be restored. Anything that was
 * only shown because of it loses that condition.
 */
export function removeItem(layout: WizardLayout, key: string): WizardLayout {
  const unpublished = (it: LayoutItem) => !it.builtin && !it.column;
  return mapSteps(layout, (s) => ({
    ...s,
    items: s.items
      .filter((it) => !(it.key === key && unpublished(it)))
      .map((it) => (it.key === key ? { ...it, hidden: true } : it)),
  }));
}

/** Can the item be placed on another step? Blocks, and custom fields not yet published. */
export function canChangeStep(item: LayoutItem): boolean {
  return item.builtin ? isMovable(BUILTIN_ITEMS[item.key]) : !item.column;
}

/** Every dropdown in the wizard a condition could hang off, except `exceptKey`. */
export function conditionSources(layout: WizardLayout, exceptKey?: string): { item: LayoutItem; step: LayoutStep; options: string[] }[] {
  const out: { item: LayoutItem; step: LayoutStep; options: string[] }[] = [];
  for (const step of layout.steps) {
    if (step.hidden) continue;
    for (const item of step.items) {
      if (item.hidden || item.key === exceptKey) continue;
      const options = dropdownOptions(item);
      if (options && options.length) out.push({ item, step, options });
    }
  }
  return out;
}

/** What publishing would change in the database: new columns, per table. */
export function pendingColumns(layout: WizardLayout): { label: string; type: string }[] {
  return layout.steps.flatMap((s) => s.items.filter((it) => !it.builtin && !it.column).map((it) => ({ label: itemLabel(it), type: it.type ?? 'text' })));
}

/**
 * Set the wording of one text slot; `undefined` (or the standard wording) puts
 * the standard wording back.
 */
export function setText(layout: WizardLayout, key: string, value: string | undefined): WizardLayout {
  const texts = { ...(layout.texts ?? {}) };
  if (value === undefined) delete texts[key];
  else texts[key] = value;
  const next: WizardLayout = { steps: layout.steps };
  if (Object.keys(texts).length) next.texts = texts;
  return next;
}

/** Items that depend on `key` through a condition. */
export function dependents(layout: WizardLayout, key: string): LayoutItem[] {
  return layout.steps.flatMap((s) => s.items.filter((it) => !it.hidden && it.condition?.field === key));
}
