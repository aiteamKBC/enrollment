/**
 * Reading a layout: the standard form as a layout, a published layout made
 * whole, and what each item is called and answers.
 */
import { ADDRESS_GROUPS, ALWAYS_VISIBLE_PUBLIC_FIELDS, BUILTIN_ITEMS, BUILTIN_SECTIONS, NEW_PUBLIC_FIELDS, RETIRED_PUBLIC_FIELDS, RETIRED_PUBLIC_ITEMS, WORKPLACE_FIELDS, TEXT_SLOTS, builtinSection, isBuiltinQuestion, textFor } from './registry';
import { QUESTION_TYPES, type EligibilityItem, type EligibilityLayout, type EligibilitySection } from './types';

const TYPES = new Set<string>(QUESTION_TYPES.map((t) => t.value));
const MOVED_TO_RESIDENCY = ['fullAddress', ...ADDRESS_GROUPS.fullAddress, 'postcode', 'attendedFundedTraining', 'fundedTrainingCompletedOn'];

/** Keep the England-hours declaration after the other Employment questions,
 * including layouts published before the Employment form was reordered. */
function placeEmploymentDeclarationLast(items: EligibilityItem[]): EligibilityItem[] {
  const declaration = items.find(item => item.builtin && item.key === 'majorityHoursInEngland');
  if (!declaration) return items;
  const without = items.filter(item => item !== declaration);
  let lastQuestion = -1;
  without.forEach((item, index) => {
    const builtin = item.builtin ? BUILTIN_ITEMS[item.key] : undefined;
    if (!builtin || isBuiltinQuestion(builtin)) lastQuestion = index;
  });
  without.splice(lastQuestion + 1, 0, declaration);
  return without;
}

/** Keep the restriction notice immediately below Employment Status, even for
 * layouts published before the notice was added or reordered. */
function placeEmploymentNoticeAfterStatus(items: EligibilityItem[]): EligibilityItem[] {
  const status = items.find(item => item.builtin && item.key === 'employmentStatus');
  if (!status) return items;
  const notice = items.find(item => item.builtin && item.key === 'note.employmentRequirements');
  const without = items.filter(item => item !== notice);
  without.splice(without.indexOf(status) + 1, 0, {
    ...notice, key: 'note.employmentRequirements', builtin: true, hidden: false,
  });
  return without;
}

/** How a condition names the answers of a yes/no question. */
export const YES_NO: readonly string[] = ['Yes', 'No'];

/** The standard form, as a layout. */
export function defaultLayout(workplaceEnabled = false): EligibilityLayout {
  return {
    workplaceEnabled,
    sections: BUILTIN_SECTIONS.map((section) => ({
      slug: section.slug,
      title: section.title,
      builtin: true,
      hidden: false,
      items: section.items.filter(item => workplaceEnabled || !WORKPLACE_FIELDS.has(item.key)).map((item) => ({
        key: item.key,
        builtin: true,
        hidden: RETIRED_PUBLIC_ITEMS.has(item.key) && !ALWAYS_VISIBLE_PUBLIC_FIELDS.has(item.key),
      })),
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

/** Upgrade old address inputs on a copy, preserving builder edits and positions. */
function structuredAddressLayout(raw: EligibilityLayout): EligibilityLayout {
  const rawSections = raw.sections.filter(section => section && typeof section.slug === 'string');
  const present = new Set(rawSections.flatMap(section => Array.isArray(section.items) ? section.items : []).filter(item => item?.builtin).map(item => item.key));
  const texts = { ...raw.texts };
  const sections = rawSections.map(section => ({ ...section, items: (Array.isArray(section.items) ? section.items : []).flatMap(item => {
    if (!item || typeof item.key !== 'string') return [];
    const keys = item.builtin ? ADDRESS_GROUPS[item.key] : undefined;
    if (!keys) return [item];
    for (const slot of ['label', 'hint']) {
      if (texts[`${item.key}.${slot}`] !== undefined && texts[`${keys[0]}.${slot}`] === undefined) texts[`${keys[0]}.${slot}`] = texts[`${item.key}.${slot}`];
    }
    return keys.filter(key => !present.has(key)).map(key => {
      present.add(key);
      return { key, builtin: true, hidden: Boolean(item.hidden) };
    });
  }) }));
  return { ...raw, sections, ...(Object.keys(texts).length ? { texts } : {}) };
}

/**
 * A published layout made whole: unknown or repeated items dropped, and any
 * built-in content it does not mention put back (hidden, except the questions
 * that identify the applicant). The server publishes complete layouts; this
 * keeps the form sound if one is not. Null means the standard form.
 */
export function resolveLayout(raw: EligibilityLayout | null | undefined, workplaceEnabled = false): EligibilityLayout {
  if (!raw || !Array.isArray(raw.sections) || raw.sections.length === 0) return defaultLayout(workplaceEnabled);
  raw = structuredAddressLayout(raw);
  const seen = new Set<string>();
  const sections: EligibilitySection[] = [];
  for (const s of raw.sections) {
    if (!s || typeof s.slug !== 'string' || sections.some((x) => x.slug === s.slug)) continue;
    const standard = builtinSection(s.slug);
    const items: EligibilityItem[] = [];
    for (const it of Array.isArray(s.items) ? s.items : []) {
      if (!it || typeof it.key !== 'string' || seen.has(it.key)) continue;
      if (it.builtin && RETIRED_PUBLIC_FIELDS.has(it.key)) continue;
      if (it.builtin && WORKPLACE_FIELDS.has(it.key) && !workplaceEnabled) continue;
      if (it.builtin ? !BUILTIN_ITEMS[it.key] : !(it.type && TYPES.has(it.type) && typeof it.label === 'string')) continue;
      seen.add(it.key);
      items.push({
        ...it,
        builtin: Boolean(it.builtin),
        hidden: (Boolean(it.hidden) && !(it.builtin && ALWAYS_VISIBLE_PUBLIC_FIELDS.has(it.key)))
          || (it.builtin && RETIRED_PUBLIC_ITEMS.has(it.key) && !ALWAYS_VISIBLE_PUBLIC_FIELDS.has(it.key)),
      });
    }
    const section: EligibilitySection = {
      slug: s.slug,
      title: typeof s.title === 'string' && s.title.trim() ? s.title : standard?.title ?? 'Section',
      builtin: Boolean(standard),
      hidden: Boolean(s.hidden),
      items: s.slug === 'employment' ? placeEmploymentDeclarationLast(items) : items,
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
    if (RETIRED_PUBLIC_FIELDS.has(item.key)) continue;
    if (WORKPLACE_FIELDS.has(item.key) && !workplaceEnabled) continue;
    if (seen.has(item.key)) continue;
    const locked = isBuiltinQuestion(item) && Boolean(item.locked);
    sections.find((s) => s.slug === item.section)!.items.push({
      key: item.key,
      builtin: true,
      hidden: (!locked && !NEW_PUBLIC_FIELDS.has(item.key) && !ALWAYS_VISIBLE_PUBLIC_FIELDS.has(item.key))
        || (RETIRED_PUBLIC_ITEMS.has(item.key) && !ALWAYS_VISIBLE_PUBLIC_FIELDS.has(item.key)),
    });
  }
  const employment = sections.find(section => section.slug === 'employment');
  if (employment) employment.items = placeEmploymentNoticeAfterStatus(employment.items);
  if (employment && workplaceEnabled) {
    employment.hidden = false;
    for (const key of ['employmentStatus', 'employerPostcode', 'majorityHoursInEngland']) {
      const existing = sections.flatMap(section => section.items).find(item => item.builtin && item.key === key);
      const restored = { ...existing, key, builtin: true, hidden: false, required: true,
        ...(key === 'employmentStatus' ? { options: undefined } : {}) };
      const position = employment.items.findIndex(item => item.builtin && item.key === key);
      if (position >= 0) employment.items[position] = restored;
      else {
        for (const section of sections) section.items = section.items.filter(item => !(item.builtin && item.key === key));
        employment.items.push(restored);
      }
    }
    // Restore the preview questions in old layouts without editing stored rows.
    for (const section of sections) section.items = section.items.filter(item => !WORKPLACE_FIELDS.has(item.key));
    const items = employment.items.filter(item => !WORKPLACE_FIELDS.has(item.key));
    const position = items.findIndex(item => item.key === 'employerPostcode') + 1;
    items.splice(position || items.length, 0, ...['workplaceArrangement', 'mainWorkplacePostcode'].map(key => ({ key, builtin: true, hidden: false })));
    employment.items = items;
    employment.items = placeEmploymentDeclarationLast(placeEmploymentNoticeAfterStatus(employment.items));
  }
  // Published layouts created before the Residency redesign may still carry
  // address or funded-training items in their old sections. Move those items
  // by key while retaining their hidden/required/options edits and answer keys.
  const residency = sections.find(section => section.slug === 'residency');
  if (residency) {
    const moved = new Map<string, EligibilityItem>();
    for (const section of sections) {
      if (section === residency) continue;
      section.items = section.items.filter(item => {
        if (!item.builtin || !MOVED_TO_RESIDENCY.includes(item.key)) return true;
        moved.set(item.key, item);
        return false;
      });
    }
    for (const key of MOVED_TO_RESIDENCY) {
      const item = moved.get(key);
      if (item && !residency.items.some(existing => existing.key === key)) residency.items.push(item);
    }
  }
  // The Yes-only confirmation belongs directly after its residency question,
  // including older published layouts that omitted, hid or moved it.
  for (const [parentKey, childKey] of [
    ['residentPrev3Years', 'residenceNotForEducation'], ['fullTimeEducationPrior', 'expectedLeavingDate'],
  ]) {
    const parentSection = sections.find(section => !section.hidden && section.items.some(item =>
      item.builtin && item.key === parentKey && !item.hidden));
    if (!parentSection) continue;
    const confirmation = sections.flatMap(section => section.items).find(item =>
      item.builtin && item.key === childKey);
    for (const section of sections) section.items = section.items.filter(item =>
      !(item.builtin && item.key === childKey));
    const position = parentSection.items.findIndex(item => item.builtin && item.key === parentKey);
    parentSection.items.splice(position + 1, 0, {
      ...confirmation, key: childKey, builtin: true, hidden: false,
    });
  }
  // Resolve legacy four-section publications without rewriting their stored rows.
  const personal = sections.find(section => section.slug === 'personal-details')!;
  const programme = sections.flatMap(section => section.items).find(item => item.builtin && item.key === 'programme');
  for (const section of sections) section.items = section.items.filter(item => !(item.builtin && item.key === 'programme'));
  personal.items.push({ ...programme, key: 'programme', builtin: true, hidden: false });
  const retired = sections.find(section => section.slug === 'programme');
  if (retired) {
    // Preserve builder-added questions and their storage when retiring their old container.
    personal.items.push(...retired.items.map(item => ({ ...item, hidden: Boolean(retired.hidden || item.hidden) })));
    sections.splice(sections.indexOf(retired), 1);
  }
  const texts = cleanTexts(raw.texts);
  const birthSection = sections.find(section => section.items.some(item => item.builtin && item.key === 'dateOfBirth'))!;
  birthSection.hidden = false;
  for (const item of birthSection.items) if (item.builtin && item.key === 'dateOfBirth') Object.assign(item, { hidden: false, required: true });
  return texts ? { sections, texts, workplaceEnabled } : { sections, workplaceEnabled };
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
  if (item.builtin && ['workplaceArrangement', 'title'].includes(item.key) && isBuiltinQuestion(def)) return def.options;
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
