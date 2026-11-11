import type { LucideIcon } from 'lucide-react';
import {
  EMPLOYMENT_STATUSES,
  isUkPostcode,
  type ApplicationField,
  type ApplicationSubmission,
  type ApprenticeshipApplication,
  type CustomAnswer,
} from '@/api/apprenticeshipApplication';
import { SYSTEM_TIME_ZONE } from '@/lib/format';
import { ADDRESS_GROUPS, REQUIRED_ADDRESS_FIELDS, STRUCTURED_ADDRESS_FIELDS, APPLICATION_FIELDS, BUILTIN_ITEMS, CUSTOM_SECTION_ICON, WORKPLACE_FIELDS, builtinSection, isBuiltinQuestion, textFor } from './layout/registry';
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
  placeholder?: string;
  required: boolean;
  requiredWhen?: (answers: Answers) => boolean;
  /** Added in the builder: its answer travels under `custom`. */
  custom?: boolean;
  shown?: (answers: Answers, depth: number) => boolean;
};
export type Question =
  | (Base & { kind: 'text' | 'email' | 'tel'; maxLength: number; autoComplete?: string; postcode?: boolean })
  | (Base & { kind: 'number' })
  | (Base & { kind: 'date'; autoComplete?: string; allowFuture?: boolean })
  | (Base & { kind: 'choice' | 'select'; options: readonly string[]; optionLabels?: Readonly<Record<string, string>> })
  | (Base & { kind: 'yesno' });
export type Note = { kind: 'note'; key: string; text: string; shown?: (answers: Answers, depth: number) => boolean };
export type ContentBlock = { kind: 'heading' | 'description'; key: string; text: string };

export interface Section {
  workplaceEnabled?: boolean;
  slug: string;
  title: string;
  description: string;
  icon: LucideIcon;
  items: (Question | Note | ContentBlock)[];
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
export const PAID_EMPLOYMENT_STATUS = 'In paid employment';
export const EMPLOYMENT_RESTRICTION_ERROR = 'You cannot continue with this application at this stage.';
export const INVALID_POSTCODE = 'Enter a valid UK postcode.';
export const UNKNOWN_POSTCODE = 'We could not find this postcode. Check it and try again.';
export const EMPLOYER_POSTCODE_UNAVAILABLE = "We could not verify your employer's postcode just now. Please try again.";
export const WORKPLACE_POSTCODE_UNAVAILABLE = 'We could not verify your main workplace postcode just now. Please try again.';
export const isRequired = (question: Question, answers: Answers) => question.required || Boolean(question.requiredWhen?.(answers));

/** The three selected statuses that are currently blocked from continuing. */
export function isEmploymentRestricted(value: AnswerValue | undefined): boolean {
  return typeof value === 'string' && value !== '' && value !== PAID_EMPLOYMENT_STATUS
    && EMPLOYMENT_STATUSES.includes(value as typeof EMPLOYMENT_STATUSES[number]);
}

export const isQuestion = (item: Question | Note | ContentBlock): item is Question =>
  item.kind !== 'note' && item.kind !== 'heading' && item.kind !== 'description';
export const isShown = (question: Question, answers: Answers, depth = 0) => !question.shown || question.shown(answers, depth);
export const isNoteShown = (note: Note, answers: Answers, depth = 0) => !note.shown || note.shown(answers, depth);
export const emptyAnswer = (question: Question): AnswerValue => (question.kind === 'yesno' ? null : '');
export const answerFor = (answers: Answers, question: Question): AnswerValue => answers[question.key] ?? emptyAnswer(question);
export const isPostcodeQuestion = (question: Question) => 'postcode' in question && Boolean(question.postcode);

/** How a condition names an answer: Yes/No for a yes/no question. */
const answerLabel = (value: AnswerValue | undefined) => (typeof value === 'boolean' ? (value ? 'Yes' : 'No') : value ?? '');

export function formPage(layout: EligibilityLayout): FormPage {
  const intro = textFor(layout.texts, 'page.intro')
    .replace('All fields marked with * are required.', '')
    .replace(/\s{2,}/g, ' ')
    .trim();
  return {
    eyebrow: textFor(layout.texts, 'page.eyebrow'),
    title: textFor(layout.texts, 'page.title'),
    intro,
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

function builtinConditionShown(condition: { key: ApplicationField; value?: string; notValue?: string }, questions: Map<string, Question>) {
  return (answers: Answers, depth: number) => {
    const trigger = questions.get(condition.key);
    if (!trigger || depth > 10 || !isShown(trigger, answers, depth + 1)) return false;
    const value = answerLabel(answers[condition.key]);
    return condition.notValue !== undefined ? Boolean(value) && value !== condition.notValue : value === condition.value;
  };
}

function combineShown(...predicates: Array<((answers: Answers, depth: number) => boolean) | undefined>) {
  const active = predicates.filter(Boolean) as Array<(answers: Answers, depth: number) => boolean>;
  return active.length ? (answers: Answers, depth: number) => active.every(predicate => predicate(answers, depth)) : undefined;
}

function toItem(item: EligibilityItem, layout: EligibilityLayout, questions: Map<string, Question>): Question | Note | ContentBlock | null {
  if (!item.builtin) {
    if (item.type === 'heading' || item.type === 'description') {
      return { kind: item.type, key: item.key, text: item.label ?? '' };
    }
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
  if (def.kind === 'note') {
    return {
      kind: 'note',
      key: def.key,
      text: textFor(layout.texts, `${def.key}.text`),
      shown: def.shownWhen ? builtinConditionShown(def.shownWhen, questions) : undefined,
    };
  }
  if (!isBuiltinQuestion(def)) return null;
  const followUp = def.followUpOf;
  const base = {
    key: def.key,
    label: textFor(layout.texts, `${def.key}.label`),
    // Postcode inputs have a label and placeholder only, including old publications.
    hint: def.key === 'dateOfBirth' ? def.hint : ['employerPostcode', 'postcode'].includes(def.key) ? undefined : textFor(layout.texts, `${def.key}.hint`) || undefined,
    placeholder: def.placeholder,
    required: STRUCTURED_ADDRESS_FIELDS.has(def.key) ? REQUIRED_ADDRESS_FIELDS.has(def.key) : def.key === 'mainWorkplacePostcode' ? false : def.locked || def.key === 'startDateWithEmployer' || def.key === 'jobTitle' || def.key === 'workplaceArrangement'
      ? true
      : item.required ?? def.required,
    requiredWhen: def.key === 'mainWorkplacePostcode' ? (answers: Answers) => ['different', 'remote_hybrid_multiple'].includes(String(answers.workplaceArrangement)) : undefined,
    shown: combineShown(
      followUp ? conditionShown({ field: followUp.key, values: [followUp.value ? 'Yes' : 'No'] }, questions) : undefined,
      def.shownWhen ? builtinConditionShown(def.shownWhen, questions) : undefined,
    ),
  };
  switch (def.kind) {
    case 'choice':
    case 'select':
      return { ...base, kind: def.kind, options: builtinOptions(item) ?? [], optionLabels: def.optionLabels };
    case 'date':
      return { ...base, kind: 'date', autoComplete: def.autoComplete, allowFuture: def.allowFuture };
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
    const items: (Question | Note | ContentBlock)[] = [];
    for (const item of section.items) {
      if (WORKPLACE_FIELDS.has(item.key) && !layout.workplaceEnabled) continue;
      if (item.hidden && !isLocked(item)) continue;
      const built = toItem(item, layout, questions);
      if (!built) continue;
      if (isQuestion(built)) questions.set(built.key, built);
      items.push(built);
    }
    if (items.length === 0) continue;
    sections.push({
      workplaceEnabled: layout.workplaceEnabled,
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

/** Round-trip calendar components: Date alone silently rolls 31 April into May. */
export function isValidDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) || value < '1900-01-01') return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** Native date-picker minimum for date fields whose earliest value depends on another answer. */
export function minimumDate(question: Question, answers: Answers): string {
  if (question.key === 'expectedLeavingDate' && isValidDate(answers.dateOfBirth)) {
    const dayAfterBirth = new Date(`${answers.dateOfBirth}T00:00:00Z`);
    dayAfterBirth.setUTCDate(dayAfterBirth.getUTCDate() + 1);
    return dayAfterBirth.toISOString().slice(0, 10);
  }
  return '1900-01-01';
}

const ageToday = (birth: string, today: string) => Number(today.slice(0, 4)) - Number(birth.slice(0, 4))
  - Number(today.slice(5) < birth.slice(5));

export const APPLICATION_AGE_REASON = 'Under our application-stage screening policy, you must be at least 16 years old on the date you submit your application. You are currently under 16, so you are not eligible at this stage. Please contact Admissions at office@kentbusinesscollege.org if you have any questions.';
export const APPLICATION_AGE_NOTICE = 'You must be at least 16 years old to be eligible to apply.';

/** Frontend screening guidance; Django independently decides using the UK submission date. */
export function meetsApplicationAge(birth: unknown, today: string): boolean | null {
  return isValidDate(birth) && birth <= today ? ageToday(birth, today) >= 16 : null;
}

/** Application policy is distinct from official eligibility at training start. */
export function dateNotice(key: string, answers: Answers, today: string): string | undefined {
  const value = answers[key];
  if (!isValidDate(value)) return;
  if (key === 'dateOfBirth' && meetsApplicationAge(value, today) === false) {
    return APPLICATION_AGE_NOTICE;
  }
  if (key === 'startDateWithEmployer' && value > today) {
    return 'This is a future employment start date. Admissions must confirm the planned employment arrangements before funding eligibility is established.';
  }
}

export function validateSection(section: Section, answers: Answers, today = businessToday(), sections?: Section[]): Errors {
  const errors: Errors = {};
  for (const question of section.items.filter(isQuestion)) {
    if (!isShown(question, answers)) continue;
    const value = answers[question.key] ?? null;
    const empty = value === null || (typeof value === 'string' && !value.trim());
    if (empty) {
      if (isRequired(question, answers)) errors[question.key] = REQUIRED;
      continue;
    }
    const text = String(value).trim();
    if (question.kind === 'email' && !EMAIL.test(text)) {
      errors[question.key] = 'Enter a valid email address.';
    } else if (question.kind === 'date') {
      if (!isValidDate(value)) errors[question.key] = 'Enter a valid date.';
      else if (!question.allowFuture && text > today) errors[question.key] = 'The date cannot be in the future.';
    } else if (question.kind === 'number' && !NUMBER.test(text)) {
      errors[question.key] = 'Enter a number.';
    }
    if (question.key === 'postcode' && isPostcodeQuestion(question)
      && ['', 'United Kingdom'].includes(String(answers.countryOfResidence ?? ''))
      && !isUkPostcode(text)) errors[question.key] = INVALID_POSTCODE;
  }
  const visible = new Set(section.items.filter(isQuestion).filter(question => isShown(question, answers)).map(question => question.key));
  const birthAsked = !sections || sections.some(part => part.items.some(question => isQuestion(question) && question.key === 'dateOfBirth' && isShown(question, answers)));
  const birth = birthAsked ? answers.dateOfBirth : null;
  if (isValidDate(birth) && birth <= today) {
    for (const key of ['startDateWithEmployer', 'expectedLeavingDate', 'fundedTrainingCompletedOn']) {
      const value = answers[key];
      if (visible.has(key) && isValidDate(value) && value <= birth) errors[key] = 'Enter a date after your date of birth.';
    }
    const age = ageToday(birth, today);
    if (visible.has('residentPrev3Years') && answers.residentPrev3Years === true && age < 3) {
      errors.residentPrev3Years = 'This answer conflicts with your date of birth. Please check your date of birth and residency history.';
    }
    const years = answers.yearsInUk;
    if (visible.has('yearsInUk') && typeof years === 'string' && /^[0-9]+(?: or more)?$/.test(years) && Number.parseInt(years, 10) > age) {
      errors.yearsInUk = 'The number of full years in the UK cannot exceed your age. Please check your answers.';
    }
  }
  if (section.items.some(question => isQuestion(question) && question.key === 'employmentStatus')
    && isEmploymentRestricted(answers.employmentStatus)) {
    errors.employmentStatus = EMPLOYMENT_RESTRICTION_ERROR;
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
  const conditionalEmployment = new Set<ApplicationField>([
    'startDateWithEmployer', 'jobTitle', 'majorityHoursInEngland', 'employerName', 'employerAddress',
    'employerPostcode', 'representativeName', 'representativeEmail',
    'workplaceArrangement', 'mainWorkplacePostcode', ...ADDRESS_GROUPS.employerAddress,
  ]);
  for (const key of APPLICATION_FIELDS) {
    if (WORKPLACE_FIELDS.has(key) && !asked.has(key)) continue;
    const def = BUILTIN_ITEMS[key];
    const hiddenConditional = (conditionalEmployment.has(key) || STRUCTURED_ADDRESS_FIELDS.has(key)) && !shown(key);
    builtin[key] = hiddenConditional
      ? (isBuiltinQuestion(def) && def.kind === 'yesno' ? null : '')
      : answers[key] ?? (isBuiltinQuestion(def) && def.kind === 'yesno' ? null : '');
  }
  const custom: Record<string, CustomAnswer> = {};
  for (const question of asked.values()) {
    if (question.custom) custom[question.key] = shown(question.key) ? answers[question.key] ?? null : null;
  }
  return {
    ...(builtin as unknown as ApprenticeshipApplication),
    fullAddress: ADDRESS_GROUPS.fullAddress.map(key => String(builtin[key] ?? '').trim()).filter(Boolean).join(', '),
    employerAddress: ADDRESS_GROUPS.employerAddress.map(key => String(builtin[key] ?? '').trim()).filter(Boolean).join(', '),
    notPreviouslyCompletedProgramme: null,
    expectedLeavingDate: shown('expectedLeavingDate') ? String(answers.expectedLeavingDate ?? '') || null : null,
    fundedTrainingCompletedOn: shown('fundedTrainingCompletedOn') ? String(answers.fundedTrainingCompletedOn ?? '') || null : null,
    custom,
    layoutVersion,
  };
}
