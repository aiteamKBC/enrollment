import type { LucideIcon } from 'lucide-react';
import {
  type ApplicationField,
  type ApplicationSubmission,
  type ApprenticeshipApplication,
  type CustomAnswer,
} from '@/api/apprenticeshipApplication';
import { SYSTEM_TIME_ZONE } from '@/lib/format';
import { APPLICATION_FIELDS, BUILTIN_ITEMS, CUSTOM_SECTION_ICON, builtinSection, isBuiltinQuestion, textFor } from './layout/registry';
import { builtinOptions, isLocked, sectionDescription } from './layout/resolve';
import type { EligibilityCondition, EligibilityItem, EligibilityLayout } from './layout/types';

export type AnswerValue = string | boolean | null;
/** What the form holds while it is being filled in, by question key: dates stay '' until chosen, yes/no stays null. */
export type Answers = Record<string, AnswerValue>;
export type Errors = Record<string, string>;

type Base = {
  key: string;
  label: string;
  hint?: string;
  required: boolean;
  /** Added in the builder: its answer travels under `custom`. */
  custom?: boolean;
  shown?: (answers: Answers, depth: number) => boolean;
};
export type Question =
  | (Base & { kind: 'text' | 'email' | 'tel'; maxLength: number; autoComplete?: string; postcode?: boolean })
  | (Base & { kind: 'number' })
  | (Base & { kind: 'date'; autoComplete?: string; allowFuture?: boolean })
  | (Base & { kind: 'choice' | 'select'; options: readonly string[] })
  | (Base & { kind: 'yesno' });
export type Note = { kind: 'note'; key: string; text: string };

export interface Section {
  slug: string;
  title: string;
  description: string;
  icon: LucideIcon;
  items: (Question | Note)[];
}

export interface FormPage {
  eyebrow: string;
  title: string;
  intro: string;
}

// Limits and messages match the backend (enrolment_api/eligibility_form.py).
const MAX_CUSTOM_TEXT = 500;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
export const REQUIRED = 'This field is required.';

export const isQuestion = (item: Question | Note): item is Question => item.kind !== 'note';
export const isShown = (question: Question, answers: Answers, depth = 0) => !question.shown || question.shown(answers, depth);
export const emptyAnswer = (question: Question): AnswerValue => (question.kind === 'yesno' ? null : '');
export const answerFor = (answers: Answers, question: Question): AnswerValue => answers[question.key] ?? emptyAnswer(question);
export const isPostcodeQuestion = (question: Question) => 'postcode' in question && Boolean(question.postcode);

/** How a condition names an answer: Yes/No for a yes/no question. */
const answerLabel = (value: AnswerValue | undefined) => (typeof value === 'boolean' ? (value ? 'Yes' : 'No') : value ?? '');

export function formPage(layout: EligibilityLayout): FormPage {
  return {
    eyebrow: textFor(layout.texts, 'page.eyebrow'),
    title: textFor(layout.texts, 'page.title'),
    intro: textFor(layout.texts, 'page.intro'),
  };
}

/** Shown while `condition`'s question is itself shown and answered with one of its values. */
function conditionShown(condition: EligibilityCondition, questions: Map<string, Question>) {
  return (answers: Answers, depth: number) => {
    const trigger = questions.get(condition.field);
    if (!trigger || depth > 10 || !isShown(trigger, answers, depth + 1)) return false;
    return condition.values.includes(answerLabel(answers[condition.field]));
  };
}

function toItem(item: EligibilityItem, layout: EligibilityLayout, questions: Map<string, Question>): Question | Note | null {
  if (!item.builtin) {
    const base = {
      key: item.key,
      label: item.label ?? '',
      hint: item.helpText || undefined,
      required: Boolean(item.required),
      custom: true,
      shown: item.condition ? conditionShown(item.condition, questions) : undefined,
    };
    switch (item.type) {
      case 'number': return { ...base, kind: 'number' };
      // A custom date may be a future one, such as a planned start.
      case 'date': return { ...base, kind: 'date', allowFuture: true };
      case 'yesno': return { ...base, kind: 'yesno' };
      case 'dropdown': return { ...base, kind: 'select', options: item.options ?? [] };
      default: return { ...base, kind: 'text', maxLength: MAX_CUSTOM_TEXT };
    }
  }

  const def = BUILTIN_ITEMS[item.key];
  if (!def) return null;
  if (def.kind === 'note') return { kind: 'note', key: def.key, text: textFor(layout.texts, `${def.key}.text`) };
  if (!isBuiltinQuestion(def)) return null;
  const followUp = def.followUpOf;
  const base = {
    key: def.key,
    label: textFor(layout.texts, `${def.key}.label`),
    hint: textFor(layout.texts, `${def.key}.hint`) || undefined,
    required: def.locked ? true : item.required ?? def.required,
    shown: followUp ? conditionShown({ field: followUp.key, values: [followUp.value ? 'Yes' : 'No'] }, questions) : undefined,
  };
  switch (def.kind) {
    case 'choice':
    case 'select':
      return { ...base, kind: def.kind, options: builtinOptions(item) ?? [] };
    case 'date':
      return { ...base, kind: 'date', autoComplete: def.autoComplete };
    case 'yesno':
      return { ...base, kind: 'yesno' };
    default:
      return { ...base, kind: def.kind, maxLength: def.maxLength ?? MAX_CUSTOM_TEXT, autoComplete: def.autoComplete, postcode: def.postcode };
  }
}

/**
 * The sections an applicant goes through, in order: hidden sections, removed
 * questions and sections left with nothing to ask are skipped.
 */
export function formSections(layout: EligibilityLayout): Section[] {
  // Every question asked, by key, for conditions to look their answers up.
  const questions = new Map<string, Question>();
  const sections: Section[] = [];
  for (const section of layout.sections) {
    if (section.hidden) continue;
    const items: (Question | Note)[] = [];
    for (const item of section.items) {
      if (item.hidden && !isLocked(item)) continue;
      const built = toItem(item, layout, questions);
      if (!built) continue;
      if (isQuestion(built)) questions.set(built.key, built);
      items.push(built);
    }
    if (!items.some(isQuestion)) continue;
    sections.push({
      slug: section.slug,
      title: section.title,
      description: sectionDescription(section),
      icon: builtinSection(section.slug)?.icon ?? CUSTOM_SECTION_ICON,
      items,
    });
  }
  return sections;
}

/** Today in the business time zone, as the YYYY-MM-DD a date input expects. */
export const businessToday = () =>
  new Intl.DateTimeFormat('en-CA', { timeZone: SYSTEM_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());

export function validateSection(section: Section, answers: Answers, today = businessToday()): Errors {
  const errors: Errors = {};
  for (const question of section.items.filter(isQuestion)) {
    if (!isShown(question, answers)) continue;
    const value = answers[question.key] ?? null;
    const empty = value === null || (typeof value === 'string' && !value.trim());
    if (empty) {
      if (question.required) errors[question.key] = REQUIRED;
      continue;
    }
    const text = String(value).trim();
    if (question.kind === 'email' && !EMAIL.test(text)) {
      errors[question.key] = 'Enter a valid email address.';
    } else if (question.kind === 'date') {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || text < '1900-01-01') errors[question.key] = 'Enter a valid date.';
      else if (!question.allowFuture && text > today) errors[question.key] = 'The date cannot be in the future.';
    } else if (question.kind === 'number' && !NUMBER.test(text)) {
      errors[question.key] = 'Enter a number.';
    }
    // A postcode is never a form error: the server decides eligibility from it on submit.
  }
  return errors;
}

/**
 * The body the backend expects: every built-in answer, the custom answers, and
 * the form version. An answer to a question not shown is never sent.
 */
export function toPayload(answers: Answers, sections: Section[], layoutVersion: number | null): ApplicationSubmission {
  const asked = new Map(sections.flatMap((section) => section.items.filter(isQuestion)).map((q) => [q.key, q]));
  const shown = (key: string) => {
    const question = asked.get(key);
    return Boolean(question) && isShown(question!, answers);
  };
  const builtin = {} as Record<ApplicationField, AnswerValue>;
  for (const key of APPLICATION_FIELDS) {
    const def = BUILTIN_ITEMS[key];
    builtin[key] = answers[key] ?? (isBuiltinQuestion(def) && def.kind === 'yesno' ? null : '');
  }
  const custom: Record<string, CustomAnswer> = {};
  for (const question of asked.values()) {
    if (question.custom) custom[question.key] = shown(question.key) ? answers[question.key] ?? null : null;
  }
  return {
    ...(builtin as unknown as ApprenticeshipApplication),
    fundedTrainingCompletedOn: shown('fundedTrainingCompletedOn') ? String(answers.fundedTrainingCompletedOn ?? '') || null : null,
    custom,
    layoutVersion,
  };
}
