/**
 * Turning a published layout (or none) into the wizard the learner sees.
 *
 * The registry is the floor: the default layout is every built-in step and
 * item in its original order, so a wizard with nothing published behaves
 * exactly as it always has. A published layout is laid over it — anything the
 * registry has that the layout does not mention (a field added to the code
 * after the layout was published) is put back at its default place rather than
 * silently disappearing, and anything the layout names that the registry no
 * longer knows is dropped.
 */
import { BUILTIN_ITEMS, BUILTIN_STEPS, isMovable, type BuiltinItemDef } from './registry';
import type { CustomAnswer, LayoutItem, LayoutStep, WizardLayout } from './types';
import type { WizardDraft } from '../../types';
import { cleanTexts } from './texts';

export function defaultLayout(): WizardLayout {
  return {
    steps: BUILTIN_STEPS.map((s) => ({
      slug: s.slug,
      label: s.label,
      builtin: true,
      hidden: false,
      items: s.items.map((key) => ({ key, builtin: true, hidden: false })),
    })),
  };
}

export const DEFAULT_LAYOUT: WizardLayout = defaultLayout();

const isCustomItem = (item: LayoutItem) => !item.builtin;

/** Steps, and blocks wherever they sit, that only apprenticeship learners take. */
const APPRENTICESHIP_ONLY_STEPS = new Set(['inclusion']);
const APPRENTICESHIP_ONLY_ITEMS = new Set(['block.inclusion']);

/**
 * The layout as one learner walks it: a commercial learner skips the
 * apprenticeship-only parts (the Inclusion assessment). Applied to the layout
 * itself rather than to the step list, so every step index the wizard uses for
 * gating and validation still lines up.
 */
export function layoutForLearner(layout: WizardLayout, commercial: boolean): WizardLayout {
  if (!commercial) return layout;
  return {
    ...layout,
    steps: layout.steps.map((s) => ({
      ...s,
      hidden: s.hidden || (s.builtin && APPRENTICESHIP_ONLY_STEPS.has(s.slug)),
      items: s.items.map((it) => (it.builtin && APPRENTICESHIP_ONLY_ITEMS.has(it.key) ? { ...it, hidden: true } : it)),
    })),
  };
}

/** The published layout, made whole against the registry. */
export function resolveLayout(saved: WizardLayout | null | undefined): WizardLayout {
  if (!saved || !Array.isArray(saved.steps) || saved.steps.length === 0) return defaultLayout();

  const known = new Set(BUILTIN_STEPS.map((s) => s.slug));
  const placed = new Set<string>();
  const steps: LayoutStep[] = [];

  for (const raw of saved.steps) {
    if (!raw || typeof raw.slug !== 'string') continue;
    if (raw.builtin && !known.has(raw.slug)) continue;
    if (steps.some((s) => s.slug === raw.slug)) continue;
    const items: LayoutItem[] = [];
    for (const item of Array.isArray(raw.items) ? raw.items : []) {
      if (!item || typeof item.key !== 'string' || placed.has(item.key)) continue;
      if (item.builtin) {
        const def = BUILTIN_ITEMS[item.key];
        if (!def) continue;
        // Only blocks travel; a stray field or heading goes home below.
        if (!isMovable(def) && def.step !== raw.slug) continue;
      }
      placed.add(item.key);
      items.push({ ...item, hidden: Boolean(item.hidden) });
    }
    steps.push({ ...raw, label: raw.label || raw.slug, hidden: Boolean(raw.hidden), items });
  }

  // Built-in steps the layout doesn't mention go back at their default index.
  BUILTIN_STEPS.forEach((def, index) => {
    if (steps.some((s) => s.slug === def.slug)) return;
    steps.splice(Math.min(index, steps.length), 0, { slug: def.slug, label: def.label, builtin: true, hidden: false, items: [] });
  });

  // Built-in items the layout doesn't mention go back into their home step,
  // after the registry item that precedes them.
  for (const def of BUILTIN_STEPS) {
    const home = steps.find((s) => s.slug === def.slug)!;
    def.items.forEach((key, index) => {
      if (placed.has(key)) return;
      placed.add(key);
      const before = def.items.slice(0, index).reverse().find((k) => home.items.some((i) => i.key === k));
      const at = before ? home.items.findIndex((i) => i.key === before) + 1 : 0;
      home.items.splice(at, 0, { key, builtin: true, hidden: false });
    });
  }

  const texts = cleanTexts(saved.texts);
  return texts ? { steps, texts } : { steps };
}

/** The steps a learner walks through, in order. */
export function visibleSteps(layout: WizardLayout): LayoutStep[] {
  return layout.steps.filter((s) => !s.hidden);
}

export function builtinDef(item: LayoutItem): BuiltinItemDef | undefined {
  return item.builtin ? BUILTIN_ITEMS[item.key] : undefined;
}

/** Whether the item must be answered: the layout's say, else the registry default. */
export function isRequired(item: LayoutItem): boolean {
  if (isCustomItem(item)) return Boolean(item.required);
  const def = BUILTIN_ITEMS[item.key];
  if (!def || !def.requirable) return Boolean(def?.defaultRequired);
  return item.required ?? def.defaultRequired;
}

/** Every item in the layout by key, with the step it sits on. */
export function indexItems(layout: WizardLayout): Map<string, { item: LayoutItem; step: LayoutStep }> {
  const out = new Map<string, { item: LayoutItem; step: LayoutStep }>();
  for (const step of layout.steps) for (const item of step.items) out.set(item.key, { item, step });
  return out;
}

function readPath(draft: unknown, path: string): unknown {
  let cur: unknown = draft;
  for (const part of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

/** The current answer to a dropdown item, as a string ('' when unanswered). */
export function dropdownAnswer(item: LayoutItem, draft: WizardDraft): string {
  const value = item.builtin
    ? (() => {
        const def = BUILTIN_ITEMS[item.key];
        return def?.path ? readPath(draft, def.path) : undefined;
      })()
    : draft.custom?.[item.key];
  return typeof value === 'string' ? value : '';
}

/**
 * Whether the item is shown right now: not removed, its step not removed, and
 * any condition met. A condition on a dropdown that is itself not shown is not
 * met — a follow-up to a question nobody was asked is not asked either.
 */
export function isShown(
  item: LayoutItem,
  draft: WizardDraft,
  index: Map<string, { item: LayoutItem; step: LayoutStep }>,
  depth = 0,
): boolean {
  if (item.hidden) return false;
  const cond = item.condition;
  if (!cond || !cond.field) return true;
  if (depth > 8) return false; // a cycle saved by hand; refuse rather than loop
  const trigger = index.get(cond.field);
  if (!trigger || trigger.step.hidden || !isShown(trigger.item, draft, index, depth + 1)) return false;
  return cond.values.includes(dropdownAnswer(trigger.item, draft));
}

/** Items that can drive a condition: built-in dropdowns and custom dropdown fields. */
export function dropdownOptions(item: LayoutItem): string[] | null {
  if (item.builtin) {
    const def = BUILTIN_ITEMS[item.key];
    return def?.options ? def.options() : null;
  }
  return item.type === 'dropdown' ? item.options ?? [] : null;
}

export function itemLabel(item: LayoutItem): string {
  return item.builtin ? BUILTIN_ITEMS[item.key]?.label ?? item.key : item.label || 'Untitled field';
}

export function isBlankAnswer(value: CustomAnswer | undefined): boolean {
  if (value == null) return true;
  if (Array.isArray(value)) return value.length === 0;
  return value.trim() === '';
}

/** What one custom field is still missing. */
export function customMissing(item: LayoutItem, draft: WizardDraft): string[] {
  const label = itemLabel(item);
  const value = draft.custom?.[item.key];
  if (isBlankAnswer(value)) return isRequired(item) ? [label] : [];
  if (item.type === 'number' && typeof value === 'string' && Number.isNaN(Number(value.trim()))) {
    return [`${label} — Enter a number`];
  }
  if (item.type === 'dropdown' && typeof value === 'string' && item.options && !item.options.includes(value)) {
    return isRequired(item) ? [label] : [];
  }
  return [];
}

/** Everything a step still needs, in the order its items appear. */
export function missingForLayoutStep(step: LayoutStep, draft: WizardDraft, layout: WizardLayout): string[] {
  const index = indexItems(layout);
  const out: string[] = [];
  for (const item of step.items) {
    if (!isShown(item, draft, index)) continue;
    if (item.builtin) {
      const def = BUILTIN_ITEMS[item.key];
      if (def?.missing) out.push(...def.missing(draft, isRequired(item)));
    } else {
      out.push(...customMissing(item, draft));
    }
  }
  return out;
}
