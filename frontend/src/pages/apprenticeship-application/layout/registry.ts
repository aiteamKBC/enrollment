/**
 * The eligibility form's built-in content: its three sections, the questions
 * they have always asked and the representative note, with their standard
 * wording. A published layout (types.ts) only moves, hides, requires or
 * re-words these; it cannot change what a built-in question stores.
 *
 * Keys, kinds, limits and the three answer lists match the backend
 * (enrolment_api/eligibility_form.py FIELDS and eligibility_layout.py
 * BUILTIN_SECTIONS).
 */
import { Briefcase, ClipboardList, House, UserRound, type LucideIcon } from 'lucide-react';
import {
  EMPLOYMENT_STATUSES,
  PROGRAMMES,
  RESIDENCY_STATUSES,
  WORKPLACE_ARRANGEMENTS,
  WORKPLACE_LABELS,
  type ApplicationField,
} from '@/api/apprenticeshipApplication';
import { COUNTRY_OPTIONS, NATIONALITY_OPTIONS } from '@/lib/countries';
import { LEARNER_TITLE_OPTIONS } from '@/lib/learnerTitles';
import type { TextSlot } from '@/pages/users/wizard/layout/texts';

export type BuiltinKind = 'text' | 'email' | 'tel' | 'date' | 'choice' | 'select' | 'yesno';

export interface BuiltinQuestion {
  key: ApplicationField;
  kind: BuiltinKind;
  label: string;
  hint?: string;
  placeholder?: string;
  required: boolean;
  maxLength?: number;
  autoComplete?: string;
  allowFuture?: boolean;
  /** Choice and select questions: the standard answer list. */
  options?: readonly string[];
  optionLabels?: Readonly<Record<string, string>>;
  /** Always asked and required: it identifies the applicant. */
  locked?: boolean;
  /** Looked up as a UK postcode while it is typed and when the section is continued. */
  postcode?: boolean;
  /** Asked only after this answer to an earlier question. */
  followUpOf?: { key: ApplicationField; value: boolean };
  /** Built-in conditional content: shown only for the named answer. */
  shownWhen?: { key: ApplicationField; value?: string; notValue?: string };
}

export interface BuiltinNote {
  key: string;
  kind: 'note';
  text: string;
  shownWhen?: { key: ApplicationField; value?: string; notValue?: string };
}

export type BuiltinItem = BuiltinQuestion | BuiltinNote;

export interface BuiltinSection {
  slug: string;
  title: string;
  description: string;
  icon: LucideIcon;
  items: BuiltinItem[];
}

/** Historical answer keys retained in the API and database, never restored by layouts. */
export const RETIRED_PUBLIC_FIELDS = new Set(['notPreviouslyCompletedProgramme', 'employerAddress', 'fullAddress', 'employerAddressLine3', 'residentialAddressLine3']);
export const WORKPLACE_FIELDS = new Set(['workplaceArrangement', 'mainWorkplacePostcode']);
/** Legacy informational content retained for old layouts but no longer shown publicly. */
export const RETIRED_PUBLIC_ITEMS = new Set(['note.representative']);
/** These answers are always present and never answer-dependent. */
export const ALWAYS_VISIBLE_PUBLIC_FIELDS = new Set(['livesInUk', 'nationality', 'dateOfBirth', 'title']);
/** Questions introduced by the Residency/Employment refresh.  If a layout
 * published before that refresh is still in force, these are restored visibly
 * in their canonical section; an admin can hide them explicitly afterwards. */
export const NEW_PUBLIC_FIELDS = new Set([
  'startDateWithEmployer', 'jobTitle', 'countryOfResidence', 'nationality',
  'residentPrev3Years', 'yearsInUk', 'fullTimeEducationPrior',
]);

export const ADDRESS_GROUPS: Readonly<Record<string, readonly ApplicationField[]>> = {
  // Keep the existing town/county API identifiers: these are visible Lines 3/4.
  employerAddress: ['employerAddressLine1', 'employerAddressLine2', 'employerTownCity', 'employerCounty'],
  fullAddress: ['residentialAddressLine1', 'residentialAddressLine2', 'residentialTownCity', 'residentialCounty'],
};
export const STRUCTURED_ADDRESS_FIELDS = new Set<string>(Object.values(ADDRESS_GROUPS).flat());
export const REQUIRED_ADDRESS_FIELDS = new Set(['employerAddressLine1', 'employerTownCity', 'residentialAddressLine1', 'residentialTownCity']);

function addressQuestions(prefix: 'employer' | 'residential'): BuiltinQuestion[] {
  const title = prefix === 'employer' ? 'Employer' : 'Residential';
  return [
    ['AddressLine1', 'Address Line 1', 200, 'address-line1', 'House number and street name'],
    ['AddressLine2', 'Address Line 2', 200, 'address-line2', 'Flat number, building name or area'],
    ['TownCity', 'Address Line 3', 100, 'address-level2', 'Town or City'],
    ['County', 'Address Line 4', 100, 'address-level1', 'County and Country'],
  ].map(([suffix, label, limit, autoComplete, placeholder]) => {
    const key = `${prefix}${suffix}` as ApplicationField;
    return { kind: 'text', key, label: `${title} ${label}`, required: REQUIRED_ADDRESS_FIELDS.has(key),
      maxLength: Number(limit), autoComplete: `${prefix === 'employer' ? 'section-employer' : 'section-residential'} ${autoComplete}`,
      placeholder: String(placeholder),
      ...(prefix === 'employer' ? { shownWhen: { key: 'employmentStatus' as const, value: 'In paid employment' } } : {}),
    };
  });
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
      { kind: 'select', key: 'title', label: 'Title', required: true, options: LEARNER_TITLE_OPTIONS, locked: true },
      { kind: 'email', key: 'workEmail', label: 'Work Email Address', hint: 'Please enter your email address within your organisation domain', required: true, maxLength: 254, autoComplete: 'work email', locked: true },
      { kind: 'tel', key: 'phoneNumber', label: 'Phone Number', hint: 'Enter your contact phone number.', required: false, maxLength: 40, autoComplete: 'tel' },
      { kind: 'date', key: 'dateOfBirth', label: 'Date of Birth', hint: 'Enter your date of birth.', required: true, autoComplete: 'bday', locked: true },
      { kind: 'select', key: 'programme', label: 'Choose Your Applying Programme', hint: 'You must confirm before submitting your application.', required: true, options: PROGRAMMES },
    ],
  },
  {
    slug: 'employment',
    title: 'Employment',
    description: 'Tell us about your current employment situation.',
    icon: Briefcase,
    items: [
      { kind: 'select', key: 'employmentStatus', label: 'Your Employment Status:', hint: 'Choose your employment status', required: true, options: EMPLOYMENT_STATUSES },
      {
        kind: 'note',
        key: 'note.employmentRequirements',
        text: 'Apprenticeship Employment Requirements\n\nAn apprenticeship is a paid job that combines employment with training. To start a government-funded apprenticeship in England, you must normally be employed and paid by an employer who can support your training.\n\nYou cannot continue with this application at this stage.\n\nIf you expect to start paid employment, believe your circumstances may qualify, or have any questions, please contact our admissions team at office@kentbusinesscollege.org.',
        shownWhen: { key: 'employmentStatus', notValue: 'In paid employment' },
      },
      { kind: 'date', key: 'startDateWithEmployer', label: 'Start Date with Employer', required: true, allowFuture: true, shownWhen: { key: 'employmentStatus', value: 'In paid employment' } },
      { kind: 'text', key: 'jobTitle', label: 'Job Title', required: true, maxLength: 200, shownWhen: { key: 'employmentStatus', value: 'In paid employment' } },
      { kind: 'text', key: 'employerName', label: 'Employer', hint: 'Please enter your current company name', required: true, maxLength: 200, autoComplete: 'organization', shownWhen: { key: 'employmentStatus', value: 'In paid employment' } },
      ...addressQuestions('employer'),
      { kind: 'text', key: 'employerPostcode', label: 'Employer Postcode', placeholder: 'Enter postcode', required: true, maxLength: 12, postcode: true, shownWhen: { key: 'employmentStatus', value: 'In paid employment' } },
      { kind: 'select', key: 'workplaceArrangement', label: "Is your employer's address also your usual place of work?", required: true,
        hint: 'Workplace information is preliminary screening evidence, not final government funding approval.',
        options: WORKPLACE_ARRANGEMENTS, optionLabels: WORKPLACE_LABELS, shownWhen: { key: 'employmentStatus', value: 'In paid employment' } },
      { kind: 'text', key: 'mainWorkplacePostcode', label: 'Main Workplace Postcode', required: false, maxLength: 12, postcode: true,
        hint: 'Provide your usual workplace postcode to complete initial screening. If you work remotely, hybrid or across multiple locations and cannot identify a usual workplace, contact Admissions at office@kentbusinesscollege.org. These details are preliminary screening evidence, not final funding approval.',
        shownWhen: { key: 'workplaceArrangement', notValue: 'same' } },
      { kind: 'text', key: 'representativeName', label: 'Employer Representative Name', required: true, maxLength: 200, shownWhen: { key: 'employmentStatus', value: 'In paid employment' } },
      { kind: 'email', key: 'representativeEmail', label: 'Employer Representative Email', required: true, maxLength: 254, shownWhen: { key: 'employmentStatus', value: 'In paid employment' } },
      {
        kind: 'yesno',
        key: 'majorityHoursInEngland',
        label: 'Could you confirm whether you expect to spend at least 50% of your working hours in England? (The measure of 50% should exclude any time expected to be spent outside of England in remote and/or hybrid working)',
        hint: 'Choose your answer',
        required: true,
        shownWhen: { key: 'employmentStatus', value: 'In paid employment' },
      },
      {
        kind: 'note',
        key: 'note.representative',
        text: 'Please provide the details of a representative who will support you with your application. This could be your Head of Apprenticeships, HR representative, Finance contact, or Manager.',
      },
    ],
  },
  {
    slug: 'residency',
    title: 'Residency',
    description: 'Help us assess your residency and training eligibility for apprenticeship funding.',
    icon: House,
    items: [
      { kind: 'select', key: 'countryOfResidence', label: 'Country of Residence', hint: 'Select the country where you currently live.', required: true, options: COUNTRY_OPTIONS },
      { kind: 'yesno', key: 'livesInUk', label: 'Are you a UK/EEA National?', hint: 'Choose Yes or No.', required: true },
      { kind: 'select', key: 'nationality', label: 'Please state your nationality', hint: 'Select your nationality.', required: true, options: NATIONALITY_OPTIONS },
      { kind: 'select', key: 'residencyStatus', label: 'Please provide your residency status?', hint: 'Select the option that best describes your residency status.', required: true, options: RESIDENCY_STATUSES },
      {
        kind: 'yesno',
        key: 'residentPrev3Years',
        label: 'Have you been ordinarily resident in the UK and Islands for the last 3 years?',
        hint: 'Choose your answer.',
        required: true,
      },
      { kind: 'select', key: 'yearsInUk', label: 'How many full years have you lived in the UK?', required: true, options: ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10 or more'], shownWhen: { key: 'residentPrev3Years', value: 'No' } },
      {
        kind: 'yesno',
        key: 'residenceNotForEducation',
        label: 'Please confirm that your ordinary residence in the UK for the last 3 years has not been mainly or wholly for the purpose of full-time education.',
        required: true,
        shownWhen: { key: 'residentPrev3Years', value: 'Yes' },
      },
      ...addressQuestions('residential'),
      { kind: 'text', key: 'postcode', label: 'Residential Postcode / Postal Code', placeholder: 'Enter postcode', required: false, maxLength: 12, autoComplete: 'postal-code', postcode: true },
      { kind: 'yesno', key: 'fullTimeEducationPrior', label: 'Are you in full-time education or training prior to enrolment? (PEI1)', required: true },
      { kind: 'date', key: 'expectedLeavingDate', label: 'Please provide expected leaving date', required: false, allowFuture: true, shownWhen: { key: 'fullTimeEducationPrior', value: 'Yes' } },
      { kind: 'yesno', key: 'attendedFundedTraining', label: 'Have you attended any other government-funded training programmes in the last 12 months?', required: true },
      { kind: 'date', key: 'fundedTrainingCompletedOn', label: 'When was it completed?', required: true, followUpOf: { key: 'attendedFundedTraining', value: true } },
    ],
  },

];

/** Sections added in the builder. */
export const CUSTOM_SECTION_ICON: LucideIcon = ClipboardList;

/** Every built-in item by key, with the section it sits in on the standard form. */
export const BUILTIN_ITEMS: Record<string, BuiltinItem & { section: string }> = Object.fromEntries(
  [
    ...BUILTIN_SECTIONS.flatMap((section) => section.items.map((item) => [item.key, { ...item, section: section.slug }])),
    // Wording/keys from older publications remain recognizable, never applicant inputs.
    ['employerAddress', { key: 'employerAddress', kind: 'text', label: 'Employer Full Address', hint: "Please enter your company's address", required: true, maxLength: 500, section: 'employment' }],
    ['fullAddress', { key: 'fullAddress', kind: 'text', label: 'Full Address', hint: 'Enter your full address', required: false, maxLength: 500, section: 'residency' }],
    ['employerAddressLine3', { key: 'employerAddressLine3', kind: 'text', label: 'Legacy employer additional address details', required: false, maxLength: 200, section: 'employment' }],
    ['residentialAddressLine3', { key: 'residentialAddressLine3', kind: 'text', label: 'Legacy residential additional address details', required: false, maxLength: 200, section: 'residency' }],
  ],
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
  slot('page.intro', 'page', 'Introduction', 'paragraph', 'Assess learner eligibility against the England apprenticeship funding rules.'),
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
  // Some old publications stored the then-standard labels explicitly. Treat
  // those as defaults, while retaining genuinely customised address wording.
  const oldAddressLabels: Record<string, string> = {
    'employerTownCity.label': 'Employer Town or City', 'employerCounty.label': 'Employer County',
    'residentialTownCity.label': 'Residential Town or City', 'residentialCounty.label': 'Residential County',
  };
  if (edited === oldAddressLabels[key] && edited !== undefined) return TEXT_SLOTS[key]?.default ?? '';
  if (typeof edited === 'string' && edited.trim() !== '') return edited;
  return TEXT_SLOTS[key]?.default ?? '';
}
