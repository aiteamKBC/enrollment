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
import { isContentFieldType, type CustomAnswer, type LayoutItem, type LayoutStep, type WizardLayout } from './types';
import type { WizardDraft } from '../../types';
import { cleanTexts } from './texts';
import { SUITABILITY_STEP } from '@/features/programme-suitability/wizardStep';
import { hasConfirmedSignature } from '../signatureDeclaration';

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

const isCustomItem = (item: LayoutItem) => !item.builtin;

/**
 * These CV/Job Description questions were published before text fields were
 * split into single-line and multi-line types. Their existing columns are
 * already PostgreSQL text columns, so they can safely use the new multi-line
 * presentation without changing stored answers.
 */
const LEGACY_MULTILINE_LABELS = new Set([
  'what is your understanding of the programme you are applying for?',
  'how will this programme help you in your career development/aspirations, and/or with your progression?',
  'please explain how your current job role relates to the apprenticeship programme you are applying for. in your answer, include your job title, main duties, and how this programme will help you develop new knowledge, skills and behaviours in your role.',
]);

function resolveLegacyCustomType(item: LayoutItem): LayoutItem {
  if (item.builtin || item.type !== 'text') return item;
  const label = item.label?.replace(/\s+/g, ' ').trim().toLowerCase();
  const isLegacyMultiline = Boolean(label && LEGACY_MULTILINE_LABELS.has(label));
  return isLegacyMultiline ? { ...item, type: 'multi_line' } : item;
}

/** Steps, and blocks wherever they sit, that only apprenticeship learners take. */
const APPRENTICESHIP_ONLY_STEPS = new Set(['inclusion']);
const APPRENTICESHIP_ONLY_ITEMS = new Set(['block.inclusion', 'ild.niApplied', 'ild.healthConditions', 'ild.primaryHealth', 'ild.additionalSupport', 'ild.workingHours']);

/**
 * The layout as one learner walks it: a commercial learner skips the
 * apprenticeship-only parts (the Inclusion assessment). Applied to the layout
 * itself rather than to the step list, so every step index the wizard uses for
 * gating and validation still lines up.
 */
export function layoutForLearner(layout: WizardLayout, commercial: boolean): WizardLayout {
  if (!commercial) {
    // Keep the common builder catalog for commercial delivery and historical
    // answers. Apprenticeships always retire its Personal Details page, even
    // when an older publication explicitly showed or reordered that page.
    const steps = layout.steps.map(step => ({ ...step, items: step.items.map(item => ({ ...item })) }));
    const signature = steps.flatMap(step => step.items).find(item => item.builtin && item.key === 'pd.signature')
      ?? { key: 'pd.signature', builtin: true, hidden: false };
    const declaration = steps.flatMap(step => step.items).find(item => item.builtin && item.key === 'ild.signature')
      ?? { key: 'ild.signature', builtin: true };
    for (const step of steps) {
      if (step.slug === 'personal-details') step.hidden = true;
      step.items = step.items.filter(item => !(item.builtin && ['pd.signature', 'ild.signature', 'ild.postcodePrior'].includes(item.key)));
    }
    const ilr = steps.find(step => step.slug === 'ilr-details');
    if (ilr) {
      ilr.hidden = false;
      const title = ilr.items.find(item => item.builtin && item.key === 'ild.title');
      if (title) Object.assign(title, { hidden: false, required: true, condition: undefined });
      else ilr.items.splice(Math.max(0, ilr.items.findIndex(item => item.key === 'ild.record') + 1), 0,
        { key: 'ild.title', builtin: true, hidden: false, required: true });
      // Keep the editable residential postcode immediately after the learner
      // record block, even when an older published layout placed it elsewhere.
      const currentPostcode = ilr.items.find(item => item.builtin && item.key === 'ild.currentPostcode')
        ?? { key: 'ild.currentPostcode', builtin: true, hidden: false, required: true };
      ilr.items = ilr.items.filter(item => !(item.builtin && item.key === 'ild.currentPostcode'));
      const recordIndex = ilr.items.findIndex(item => item.builtin && item.key === 'ild.record');
      ilr.items.splice(Math.max(0, recordIndex + 1), 0, { ...currentPostcode, hidden: false, required: true, condition: undefined });
      // The old ILR key now renders the accuracy text only. Neither older
      // publications nor builder edits can reintroduce a second signing box.
      ilr.items.push({ ...declaration, hidden: false, required: false, condition: undefined });
      ilr.items.push({ ...signature, hidden: false, required: true, condition: undefined });
    }
    return { ...layout, steps };
  }
  return {
    ...layout,
    steps: layout.steps.map((s) => ({
      ...s,
      hidden: s.hidden || (s.builtin && APPRENTICESHIP_ONLY_STEPS.has(s.slug)),
      items: s.items.map((it) => (it.builtin && (APPRENTICESHIP_ONLY_ITEMS.has(it.key) || it.key === 'ild.title' || it.key === 'ild.currentPostcode') ? { ...it, hidden: true } : it)),
    })),
  };
}

/** Default active apprenticeship journey; defaultLayout remains the shared builder catalog. */
export const DEFAULT_LAYOUT: WizardLayout = layoutForLearner(defaultLayout(), false);

/** The published layout, made whole against the registry. */
export function resolveLayout(saved: WizardLayout | null | undefined): WizardLayout {
  const radar = { ...(saved?.skillsRadarCatalogues ? { skillsRadarCatalogues: saved.skillsRadarCatalogues } : {}),
    ...(saved?.skillsRadarImportApprovals ? { skillsRadarImportApprovals: saved.skillsRadarImportApprovals } : {}) };
  if (!saved || !Array.isArray(saved.steps) || saved.steps.length === 0) return {
    ...defaultLayout(), ...radar, ...(saved?.programmeSuitability ? { programmeSuitability: saved.programmeSuitability } : {}),
    ...(saved?.programmeModules ? { programmeModules: saved.programmeModules } : {}),
  };

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
      items.push({ ...resolveLegacyCustomType(item), hidden: Boolean(item.hidden) });
    }
    steps.push({ ...raw, label: raw.label || raw.slug, hidden: Boolean(raw.hidden), items });
  }

  // Built-in steps the layout doesn't mention go back at their default index.
  BUILTIN_STEPS.forEach((def, index) => {
    if (steps.some((s) => s.slug === def.slug)) return;
    const cvIndex = steps.findIndex((s) => s.slug === 'cv-job');
    const at = def.slug === SUITABILITY_STEP.slug && cvIndex >= 0 ? cvIndex : Math.min(index, steps.length);
    steps.splice(at, 0, { slug: def.slug, label: def.label, builtin: true, hidden: false, items: [] });
  });

  if (saved.programmeSuitability?.enabled) {
    const assessment = steps.find((s) => s.slug === SUITABILITY_STEP.slug)!;
    assessment.hidden = false;
  }

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
  return { steps, ...radar, ...(texts ? { texts } : {}), ...(saved.programmeSuitability ? { programmeSuitability: saved.programmeSuitability } : {}),
    ...(saved.programmeModules ? { programmeModules: saved.programmeModules } : {}) };
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
  if (item.builtin) return BUILTIN_ITEMS[item.key]?.label ?? item.key;
  if (item.type === 'divider') return 'Divider';
  if (item.type === 'heading') return item.label || 'Heading';
  if (item.type === 'description') return item.label || 'Description';
  return item.label || 'Untitled field';
}

export function isBlankAnswer(value: CustomAnswer | undefined): boolean {
  if (value == null) return true;
  if (Array.isArray(value)) return value.length === 0;
  return value.trim() === '';
}

/** What one custom field is still missing. */
export function customMissing(item: LayoutItem, draft: WizardDraft): string[] {
  if (isContentFieldType(item.type)) return [];
  const label = itemLabel(item);
  const value = draft.custom?.[item.key];
  if (isBlankAnswer(value)) return isRequired(item) ? [label] : [];
  if (item.type === 'number' && typeof value === 'string' && Number.isNaN(Number(value.trim()))) {
    return [`${label} — Enter a number`];
  }
  if (item.type === 'dropdown' && typeof value === 'string' && item.options && !item.options.includes(value)) {
    return isRequired(item) ? [label] : [];
  }
  if (item.type === 'radio' && typeof value === 'string' && item.options && !item.options.includes(value)) {
    return isRequired(item) ? [label] : [];
  }
  if (item.type === 'checkbox' && Array.isArray(value) && value.some((option) => typeof option !== 'string' || !item.options?.includes(option))) {
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
      if (step.slug === 'ilr-details' && item.key === 'pd.signature') {
        if (!hasConfirmedSignature(draft.personalDetails, layout.texts)) out.push('Your signature');
      } else if (def?.missing) out.push(...def.missing(draft, isRequired(item)));
    } else {
      out.push(...customMissing(item, draft));
    }
  }
  return out;
}
