/**
 * Reading a layout: the standard form as a layout, a published layout made
 * whole, and what each item is called and answers.
 */
import { BUILTIN_ITEMS, BUILTIN_SECTIONS, TEXT_SLOTS, builtinSection, isBuiltinQuestion, textFor } from './registry';
import { QUESTION_TYPES, type EligibilityItem, type EligibilityLayout, type EligibilitySection } from './types';

const TYPES = new Set<string>(QUESTION_TYPES.map((t) => t.value));

/** How a condition names the answers of a yes/no question. */
export const YES_NO: readonly string[] = ['Yes', 'No'];

/** The standard form, as a layout. */
export function defaultLayout(): EligibilityLayout {
  return {
    sections: BUILTIN_SECTIONS.map((section) => ({
      slug: section.slug,
      title: section.title,
      builtin: true,
      hidden: false,
      items: section.items.map((item) => ({ key: item.key, builtin: true, hidden: false })),
    })),
  };
}

/** Only edits for known slots, and only where they differ from the standard wording. */
function cleanTexts(raw: unknown): Record<string, string> | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const slot = TEXT_SLOTS[key];
    if (!slot || typeof value !== 'string' || value.trim() === '' || value === slot.default) continue;
    out[key] = value;
  }
  return Object.keys(out).length ? out : undefined;
}

/**
 * A published layout made whole: unknown or repeated items dropped, and any
 * built-in content it does not mention put back (hidden, except the questions
 * that identify the applicant). The server publishes complete layouts; this
 * keeps the form sound if one is not. Null means the standard form.
 */
export function resolveLayout(raw: EligibilityLayout | null | undefined): EligibilityLayout {
  if (!raw || !Array.isArray(raw.sections) || raw.sections.length === 0) return defaultLayout();
  const seen = new Set<string>();
  const sections: EligibilitySection[] = [];
  for (const s of raw.sections) {
    if (!s || typeof s.slug !== 'string' || sections.some((x) => x.slug === s.slug)) continue;
    const standard = builtinSection(s.slug);
    const items: EligibilityItem[] = [];
    for (const it of Array.isArray(s.items) ? s.items : []) {
      if (!it || typeof it.key !== 'string' || seen.has(it.key)) continue;
      if (it.builtin ? !BUILTIN_ITEMS[it.key] : !(it.type && TYPES.has(it.type) && typeof it.label === 'string')) continue;
      seen.add(it.key);
      items.push({ ...it, builtin: Boolean(it.builtin), hidden: Boolean(it.hidden) });
    }
    const section: EligibilitySection = {
      slug: s.slug,
      title: typeof s.title === 'string' && s.title.trim() ? s.title : standard?.title ?? 'Section',
      builtin: Boolean(standard),
      hidden: Boolean(s.hidden),
      items,
    };
    if (typeof s.description === 'string') section.description = s.description;
    sections.push(section);
  }
  for (const standard of BUILTIN_SECTIONS) {
    if (!sections.some((s) => s.slug === standard.slug)) {
      sections.push({ slug: standard.slug, title: standard.title, builtin: true, hidden: true, items: [] });
    }
  }
  for (const item of Object.values(BUILTIN_ITEMS)) {
    if (seen.has(item.key)) continue;
    const locked = isBuiltinQuestion(item) && Boolean(item.locked);
    sections.find((s) => s.slug === item.section)!.items.push({ key: item.key, builtin: true, hidden: !locked });
  }
  const texts = cleanTexts(raw.texts);
  return texts ? { sections, texts } : { sections };
}

/** Whether the item is a built-in question that must always be asked. */
export function isLocked(item: EligibilityItem): boolean {
  const def = item.builtin ? BUILTIN_ITEMS[item.key] : undefined;
  return isBuiltinQuestion(def) && Boolean(def.locked);
}

/** What the builder calls an item. */
export function itemLabel(layout: EligibilityLayout, item: EligibilityItem): string {
  if (!item.builtin) return item.label?.trim() || 'Untitled question';
  const def = BUILTIN_ITEMS[item.key];
  if (!def) return item.key;
  if (def.kind === 'note') return 'Information: employer representative';
  return textFor(layout.texts, `${item.key}.label`);
}

/** A section's description as applicants see it. */
export function sectionDescription(section: EligibilitySection): string {
  return section.description ?? builtinSection(section.slug)?.description ?? '';
}

/** A built-in choice question's answer list, edited or standard. */
export function builtinOptions(item: EligibilityItem): readonly string[] | undefined {
  const def = item.builtin ? BUILTIN_ITEMS[item.key] : undefined;
  return isBuiltinQuestion(def) && def.options ? item.options ?? def.options : undefined;
}

/** The answers a question offers, as a condition names them; null when it has no set answers. */
export function answersOf(item: EligibilityItem): readonly string[] | null {
  if (item.builtin) {
    const def = BUILTIN_ITEMS[item.key];
    if (!isBuiltinQuestion(def)) return null;
    if (def.kind === 'yesno') return YES_NO;
    return builtinOptions(item) ?? null;
  }
  if (item.type === 'yesno') return YES_NO;
  if (item.type === 'dropdown') return item.options ?? [];
  return null;
}
