/**
 * Pure edits on an eligibility form layout, for the builder. Each returns a new
 * layout and never mutates its input, so the builder can compare against what
 * was loaded to know whether anything is unpublished.
 */
import { BUILTIN_ITEMS, isBuiltinQuestion } from '@/pages/apprenticeship-application/layout/registry';
import { answersOf, isLocked, itemLabel } from '@/pages/apprenticeship-application/layout/resolve';
import type {
  EligibilityCondition,
  EligibilityItem,
  EligibilityLayout,
  EligibilityQuestionType,
  EligibilitySection,
} from '@/pages/apprenticeship-application/layout/types';

const mapSections = (layout: EligibilityLayout, fn: (s: EligibilitySection) => EligibilitySection): EligibilityLayout => ({
  ...layout,
  sections: layout.sections.map(fn),
});

const swap = <T,>(list: T[], from: number, to: number): T[] => {
  if (to < 0 || to >= list.length || from === to) return list;
  const next = [...list];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
};

const allItems = (layout: EligibilityLayout) => layout.sections.flatMap((s) => s.items);

export function findItem(layout: EligibilityLayout, key: string): EligibilityItem | undefined {
  return allItems(layout).find((it) => it.key === key);
}

export function moveSection(layout: EligibilityLayout, slug: string, delta: -1 | 1): EligibilityLayout {
  const i = layout.sections.findIndex((s) => s.slug === slug);
  return i === -1 ? layout : { ...layout, sections: swap(layout.sections, i, i + delta) };
}

export function updateSection(layout: EligibilityLayout, slug: string, patch: Partial<EligibilitySection>): EligibilityLayout {
  return mapSections(layout, (s) => (s.slug === slug ? { ...s, ...patch } : s));
}

/** A new, empty section at the end. Its slug is temporary — the server assigns the real one. */
export function addSection(layout: EligibilityLayout, title: string): { layout: EligibilityLayout; slug: string } {
  const slug = `new-section-${Date.now().toString(36)}`;
  const section: EligibilitySection = { slug, title, description: '', builtin: false, hidden: false, items: [] };
  return { layout: { ...layout, sections: [...layout.sections, section] }, slug };
}

/**
 * Move an item one place up or down among the section's shown items — removed
 * items keep their slot but are skipped over, so the arrows always move the
 * item past something the admin can actually see.
 */
export function moveItem(layout: EligibilityLayout, slug: string, key: string, delta: -1 | 1): EligibilityLayout {
  return mapSections(layout, (s) => {
    if (s.slug !== slug) return s;
    const visible = s.items.map((it, i) => ({ it, i })).filter(({ it }) => !it.hidden);
    const pos = visible.findIndex(({ it }) => it.key === key);
    const target = visible[pos + delta];
    if (pos === -1 || !target) return s;
    return { ...s, items: swap(s.items, visible[pos].i, target.i) };
  });
}

export function updateItem(layout: EligibilityLayout, key: string, patch: Partial<EligibilityItem>): EligibilityLayout {
  return mapSections(layout, (s) => ({ ...s, items: s.items.map((it) => (it.key === key ? { ...it, ...patch } : it)) }));
}

/** Put an item at the end of another section. Answers are stored by question, so any item may move. */
export function moveItemToSection(layout: EligibilityLayout, key: string, toSlug: string): EligibilityLayout {
  const moving = findItem(layout, key);
  if (!moving) return layout;
  const without = mapSections(layout, (s) => ({ ...s, items: s.items.filter((it) => it.key !== key) }));
  return mapSections(without, (s) => (s.slug === toSlug ? { ...s, items: [...s.items, moving] } : s));
}

/** The builder's working copy of one custom question while it is being added or edited. */
export interface QuestionDraft {
  label: string;
  type: EligibilityQuestionType;
  options: string[];
  helpText: string;
  required: boolean;
  condition: EligibilityCondition | null;
}

export function questionDraftFrom(item?: EligibilityItem): QuestionDraft {
  return {
    label: item?.label ?? '',
    type: item?.type ?? 'text',
    options: item?.options ?? [],
    helpText: item?.helpText ?? '',
    required: item?.required ?? false,
    condition: item?.condition ?? null,
  };
}

/** Add a custom question at the end of a section, or update one. A new question's key is temporary until published. */
export function saveCustomQuestion(
  layout: EligibilityLayout,
  slug: string,
  key: string | null,
  draft: QuestionDraft,
): { layout: EligibilityLayout; key: string } {
  const fields: Partial<EligibilityItem> = {
    label: draft.label,
    type: draft.type,
    options: draft.type === 'dropdown' ? draft.options : undefined,
    helpText: draft.helpText || undefined,
    required: draft.required,
    condition: draft.condition,
  };
  if (key) return { layout: updateItem(layout, key, fields), key };
  const newKey = `new_${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
  const item: EligibilityItem = { ...fields, key: newKey, builtin: false, hidden: false };
  return { layout: mapSections(layout, (s) => (s.slug === slug ? { ...s, items: [...s.items, item] } : s)), key: newKey };
}

/**
 * Remove an item from the form. A question never yet published simply goes;
 * a built-in item or a published question is hidden instead, so its column
 * and the answers in it are kept and it can be restored.
 */
export function removeItem(layout: EligibilityLayout, key: string): EligibilityLayout {
  const unpublished = (it: EligibilityItem) => !it.builtin && !it.column;
  return mapSections(layout, (s) => ({
    ...s,
    items: s.items
      .filter((it) => !(it.key === key && unpublished(it)))
      .map((it) => (it.key === key ? { ...it, hidden: true } : it)),
  }));
}

/** Set the wording of one text slot; `undefined` puts the standard wording back. */
export function setText(layout: EligibilityLayout, key: string, value: string | undefined): EligibilityLayout {
  const texts = { ...(layout.texts ?? {}) };
  if (value === undefined) delete texts[key];
  else texts[key] = value;
  const next: EligibilityLayout = { sections: layout.sections };
  if (Object.keys(texts).length) next.texts = texts;
  return next;
}

/** The built-in question an item is only asked after, if any. */
function followUpOf(item: EligibilityItem): string | undefined {
  const def = item.builtin ? BUILTIN_ITEMS[item.key] : undefined;
  return isBuiltinQuestion(def) ? def.followUpOf?.key : undefined;
}

/** Shown items asked only for some answer to `key`. */
export function dependents(layout: EligibilityLayout, key: string): EligibilityItem[] {
  return allItems(layout).filter((it) => !it.hidden && (it.condition?.field === key || followUpOf(it) === key));
}

/** Whether `item` is shown only through a chain of conditions reaching `key`. */
function dependsOn(layout: EligibilityLayout, item: EligibilityItem, key: string): boolean {
  let current: EligibilityItem | undefined = item;
  for (let depth = 0; current?.condition && depth < 20; depth += 1) {
    if (current.condition.field === key) return true;
    current = findItem(layout, current.condition.field);
  }
  return false;
}

/** Every shown question with set answers that a condition could hang off, except `exceptKey` and what depends on it. */
export function conditionSources(layout: EligibilityLayout, exceptKey?: string) {
  const out: { item: EligibilityItem; section: EligibilitySection; answers: readonly string[] }[] = [];
  for (const section of layout.sections) {
    if (section.hidden) continue;
    for (const item of section.items) {
      if (item.hidden || item.key === exceptKey || (exceptKey && dependsOn(layout, item, exceptKey))) continue;
      const answers = answersOf(item);
      if (answers && answers.length) out.push({ item, section, answers });
    }
  }
  return out;
}

/** What publishing would add to enrolment.eligibility_form: one column per new question. */
export function pendingColumns(layout: EligibilityLayout): { label: string; type: EligibilityQuestionType }[] {
  return allItems(layout)
    .filter((it) => !it.builtin && !it.column)
    .map((it) => ({ label: itemLabel(layout, it), type: it.type ?? 'text' }));
}

/** What stops the layout being published, in words for the admin. Mirrors the server's checks. */
export function problems(layout: EligibilityLayout): string[] {
  const out = new Set<string>();
  const items = new Map(allItems(layout).map((it) => [it.key, it]));
  for (const section of layout.sections) {
    if (!section.title.trim()) out.add('Every section needs a name.');
    for (const item of section.items) {
      const label = itemLabel(layout, item);
      if (isLocked(item) && (item.hidden || section.hidden)) {
        out.add(`“${label}” identifies the applicant, so it must stay on a section applicants see.`);
      }
      if (item.hidden) continue;
      if (!item.builtin && !item.label?.trim()) out.add('Every question needs a label.');
      const list = item.builtin ? item.options : item.type === 'dropdown' ? item.options ?? [] : undefined;
      if (list) {
        const answers = list.map((o) => o.trim()).filter(Boolean);
        if (!answers.length) out.add(`“${label}” needs at least one answer.`);
        else if (new Set(answers).size !== answers.length) out.add(`“${label}” lists the same answer twice.`);
      }
      if (item.condition) {
        const trigger = items.get(item.condition.field);
        const answers = trigger ? answersOf(trigger) : null;
        if (!answers) {
          out.add(`“${label}” depends on a question that no longer has set answers.`);
        } else {
          const missing = item.condition.values.filter((v) => !answers.includes(v));
          if (missing.length) out.add(`“${label}” is shown for answers the form no longer offers: ${missing.join(', ')}.`);
        }
      }
    }
  }
  return [...out];
}
