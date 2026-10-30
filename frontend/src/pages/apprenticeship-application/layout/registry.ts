/**
 * The eligibility form's built-in content: its four sections, the questions
 * they have always asked and the representative note, with their standard
 * wording. A published layout (types.ts) only moves, hides, requires or
 * re-words these; it cannot change what a built-in question stores.
 *
 * Keys, kinds, limits and the three answer lists match the backend
 * (enrolment_api/eligibility_form.py FIELDS and eligibility_layout.py
 * BUILTIN_SECTIONS).
 */
import { Briefcase, ClipboardList, GraduationCap, House, UserRound, type LucideIcon } from 'lucide-react';
import {
  EMPLOYMENT_STATUSES,
  PROGRAMMES,
  RESIDENCY_STATUSES,
  type ApplicationField,
} from '@/api/apprenticeshipApplication';
import type { TextSlot } from '@/pages/users/wizard/layout/texts';

export type BuiltinKind = 'text' | 'email' | 'tel' | 'date' | 'choice' | 'select' | 'yesno';

export interface BuiltinQuestion {
  key: ApplicationField;
  kind: BuiltinKind;
  label: string;
  hint?: string;
  required: boolean;
  maxLength?: number;
  autoComplete?: string;
  /** Choice and select questions: the standard answer list. */
  options?: readonly string[];
  /** Always asked and required: it identifies the applicant. */
  locked?: boolean;
  /** Looked up as a UK postcode while it is typed and when the section is continued. */
  postcode?: boolean;
  /** Asked only after this answer to an earlier question. */
  followUpOf?: { key: ApplicationField; value: boolean };
}

export interface BuiltinNote {
  key: string;
  kind: 'note';
  text: string;
}

export type BuiltinItem = BuiltinQuestion | BuiltinNote;

export interface BuiltinSection {
  slug: string;
  title: string;
  description: string;
  icon: LucideIcon;
  items: BuiltinItem[];
}

export const BUILTIN_SECTIONS: BuiltinSection[] = [
  {
    slug: 'personal-details',
    title: 'Personal Details',
    description: 'Please provide your personal information.',
    icon: UserRound,
    items: [
      { kind: 'text', key: 'firstName', label: 'First Name', hint: 'Please enter your legal first name', required: true, maxLength: 100, autoComplete: 'given-name', locked: true },
      { kind: 'text', key: 'surname', label: 'Surname', hint: 'Please enter your legal surname', required: true, maxLength: 100, autoComplete: 'family-name', locked: true },
      { kind: 'email', key: 'workEmail', label: 'Work Email Address', hint: 'Please enter your email address within your organisation domain', required: true, maxLength: 254, autoComplete: 'work email', locked: true },
      { kind: 'tel', key: 'phoneNumber', label: 'Phone Number', hint: 'Enter your contact phone number.', required: false, maxLength: 40, autoComplete: 'tel' },
      { kind: 'text', key: 'fullAddress', label: 'Full Address', hint: 'Enter your full address', required: true, maxLength: 500, autoComplete: 'street-address' },
      { kind: 'text', key: 'postcode', label: 'Postcode', hint: 'Enter your postcode', required: true, maxLength: 12, autoComplete: 'postal-code', postcode: true },
      { kind: 'date', key: 'dateOfBirth', label: 'Date of Birth', hint: 'Please enter your date of birth (You must be 16 or over to be eligible)', required: true, autoComplete: 'bday' },
    ],
  },
  {
    slug: 'employment',
    title: 'Employment',
    description: 'Tell us about your current employment situation.',
    icon: Briefcase,
    items: [
      { kind: 'choice', key: 'employmentStatus', label: 'Your Employment Status:', hint: 'Choose your employment status', required: true, options: EMPLOYMENT_STATUSES },
      {
        kind: 'yesno',
        key: 'majorityHoursInEngland',
        label: 'Could you confirm whether you expect to spend more than 50% of your working hours in England? (The measure of 50% should exclude any time expected to be spent outside of England in remote and/or hybrid working)',
        hint: 'Choose your answer',
        required: true,
      },
      { kind: 'text', key: 'employerName', label: 'Employer', hint: 'Please enter your current company name', required: true, maxLength: 200, autoComplete: 'organization' },
      { kind: 'text', key: 'employerAddress', label: 'Employer Full Address', hint: "Please enter your company's address", required: true, maxLength: 500 },
      { kind: 'text', key: 'employerPostcode', label: 'Employer Postcode', hint: "Please enter your company's postcode", required: true, maxLength: 12, postcode: true },
      {
        kind: 'note',
        key: 'note.representative',
        text: 'Please provide the details of a representative who will support you with your application. This could be your Head of Apprenticeships, HR representative, Finance contact, or Manager.',
      },
      { kind: 'text', key: 'representativeName', label: 'Employer Representative Name', required: true, maxLength: 200 },
      { kind: 'email', key: 'representativeEmail', label: 'Employer Representative Email', required: true, maxLength: 254 },
    ],
  },
  {
    slug: 'residency',
    title: 'Residency',
    description: 'Help us assess your residency status for apprenticeship funding.',
    icon: House,
    items: [
      { kind: 'yesno', key: 'livesInUk', label: 'Do you currently live in United Kingdom?', hint: 'You must be living in England to be eligible for funding.', required: true },
      {
        kind: 'yesno',
        key: 'residenceNotForEducation',
        label: 'Please confirm that your ordinary residence in the UK for the last 3 years has not been mainly or wholly for the purpose of full-time education.',
        required: true,
      },
      { kind: 'select', key: 'residencyStatus', label: 'Please provide your residency status?', hint: 'Select the option that best describes your residency status.', required: true, options: RESIDENCY_STATUSES },
    ],
  },
  {
    slug: 'programme',
    title: 'Programme Suitability',
    description: 'Please briefly explain how the programme aligns with your role, experience, and career goals.',
    icon: GraduationCap,
    items: [
      { kind: 'select', key: 'programme', label: 'Choose Your Applying Programme', hint: 'You must confirm before submitting your application.', required: true, options: PROGRAMMES },
      { kind: 'yesno', key: 'attendedFundedTraining', label: 'Have you attended any other government funded training programmes in the last 12 months?', required: true },
      { kind: 'date', key: 'fundedTrainingCompletedOn', label: 'When was it completed?', required: true, followUpOf: { key: 'attendedFundedTraining', value: true } },
      { kind: 'yesno', key: 'notPreviouslyCompletedProgramme', label: 'Please confirm that you have not previously completed this programme as part of a funded apprenticeship.', required: true },
    ],
  },
];

/** Sections added in the builder. */
export const CUSTOM_SECTION_ICON: LucideIcon = ClipboardList;

/** Every built-in item by key, with the section it sits in on the standard form. */
export const BUILTIN_ITEMS: Record<string, BuiltinItem & { section: string }> = Object.fromEntries(
  BUILTIN_SECTIONS.flatMap((section) => section.items.map((item) => [item.key, { ...item, section: section.slug }])),
);

/** The built-in answer fields, in the form's standard order. */
export const APPLICATION_FIELDS: ApplicationField[] = BUILTIN_SECTIONS.flatMap((section) =>
  section.items.filter((item): item is BuiltinQuestion => item.kind !== 'note').map((item) => item.key),
);

export const isBuiltinQuestion = (item: BuiltinItem | undefined): item is BuiltinQuestion & { section: string } =>
  Boolean(item) && item!.kind !== 'note';

export const builtinSection = (slug: string) => BUILTIN_SECTIONS.find((section) => section.slug === slug);

// ── editable wording ────────────────────────────────────────────────────────

const slot = (key: string, item: string, label: string, kind: TextSlot['kind'], def: string): TextSlot =>
  ({ key, item, label, kind, default: def });

/** The heading at the top of the form page. */
export const PAGE_TEXT_SLOTS: TextSlot[] = [
  slot('page.eyebrow', 'page', 'Small heading', 'line', 'APPRENTICESHIP FUNDING ELIGIBILITY'),
  slot('page.title', 'page', 'Page title', 'line', 'Eligibility Form'),
  slot('page.intro', 'page', 'Introduction', 'paragraph', 'Assess learner eligibility against the England apprenticeship funding rules. All fields marked with * are required.'),
];

/** Each built-in item's wording, in the order it appears on the form. */
export const ITEM_TEXT_SLOTS: Record<string, TextSlot[]> = Object.fromEntries(
  Object.values(BUILTIN_ITEMS).map((item) => [
    item.key,
    item.kind === 'note'
      ? [slot(`${item.key}.text`, item.key, 'Information shown to applicants', 'paragraph', item.text)]
      : [
          slot(`${item.key}.label`, item.key, 'Question', 'line', item.label),
          slot(`${item.key}.hint`, item.key, 'Help text', 'line', item.hint ?? ''),
        ],
  ]),
);

export const TEXT_SLOTS: Record<string, TextSlot> = Object.fromEntries(
  [...PAGE_TEXT_SLOTS, ...Object.values(ITEM_TEXT_SLOTS).flat()].map((s) => [s.key, s]),
);

/** The wording for `key`: the published edit, else the standard wording. */
export function textFor(texts: Record<string, string> | undefined, key: string): string {
  const edited = texts?.[key];
  if (typeof edited === 'string' && edited.trim() !== '') return edited;
  return TEXT_SLOTS[key]?.default ?? '';
}
