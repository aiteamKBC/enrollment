/**
 * Every built-in piece of the enrolment wizard, by stable key.
 *
 * The step components render these in whatever order the published layout
 * gives (see StepItems), and validation.ts asks each visible item what it is
 * still missing. Each item owns its own completeness rule, so making one
 * optional, hiding it or moving it can never leave another item's rule behind.
 *
 * `missing(draft, required)`:
 *   required = true  → everything the item has always demanded;
 *   required = false → only format problems with something actually typed
 *                      (an optional email must still be an email).
 *
 * Keys never change once published — the layout refers to them.
 */
import { POLICY_DOCS_KBC, SEX_OPTIONS, YES_NO_SELECT } from '@/mocks/enrolment-console';
import { COUNTRY_OPTIONS, NATIONALITY_OPTIONS } from '@/lib/countries';
import { formatError } from "../steps/fields.helpers";
import { IlrText } from '../steps/ilrAdditionalText';
import { CvJobText } from '../steps/cvJobText';
import {
  EMPLOYMENT_STATUS_OPTIONS,
  ETHNICITY_OPTIONS,
  IN_PAID_EMPLOYMENT,
  LEGAL_SEX_OPTIONS,
  PRIOR_ATTAINMENT_OPTIONS,
  STATE_BENEFIT_OPTIONS,
  labels,
} from '../ilrLearnerDetailsOptions';
import type { IlrLearnerDetails, WizardDraft } from '../../types';

/**
 * field   — a question (or a question with its follow-ups), tied to its step;
 * heading — a section heading on its step, grouping the items after it;
 * content — explanatory text or controls tied to its step;
 * block   — a self-contained part (Skills Radar, PLR, Policies, a page of
 *           guidance) that can be placed on any step.
 */
export type ItemKind = 'field' | 'heading' | 'block' | 'content';

export interface BuiltinItemDef {
  key: string;
  /** The step the item belongs to. Fields and headings never leave it; blocks and content can. */
  step: string;
  kind: ItemKind;
  /** How the builder names it, and the missing-list entry when an optional field is made required. */
  label: string;
  /** Whether the builder offers the optional/mandatory switch. */
  requirable: boolean;
  defaultRequired: boolean;
  missing?: (d: WizardDraft, required: boolean) => string[];
  /** Printed on the generated ILR document — the builder warns before it is hidden or made optional. */
  onIlrDocument?: boolean;
  /** A dropdown other items can be shown or hidden by. */
  options?: () => string[];
  /** Where a dropdown's answer lives in the draft, as a dotted path. */
  path?: string;
}

export interface BuiltinStepDef {
  slug: string;
  label: string;
  /** How a section heading groups the items after it. */
  sectionStyle?: 'fieldset' | 'section';
  items: string[];
}

/** The ILR's missing label for its certification signature. */
export const ILR_SIGNATURE_LABEL = 'Declaration signature';

// ── shared checks ───────────────────────────────────────────────────────────

export const isBlank = (v: unknown): boolean =>
  v === null || v === undefined || (typeof v === 'string' && v.trim() === '');

/** A Yes/No question is answered when it is true or false — never null. */
const isUnanswered = (v: boolean | null | undefined): boolean => v !== true && v !== false;

/**
 * Typed fields must also be well-formed, not merely non-empty — `type="email"`
 * and `type="tel"` do not enforce anything outside a native form submit.
 * Shares formatError with the input itself so the inline message and the
 * blocking rule can never disagree.
 */
function badFormat(type: 'email' | 'tel', value: string | undefined, label: string): string | null {
  if (isBlank(value)) return null;
  const why = formatError(type, String(value));
  return why ? `${label} — ${why}` : null;
}

/** Required text: blank → label; filled → its format complaint, if any. */
function text(label: string, value: string | undefined, required: boolean, format?: 'email' | 'tel'): string[] {
  if (isBlank(value)) return required ? [label] : [];
  const bad = format ? badFormat(format, value, label) : null;
  return bad ? [bad] : [];
}

const yesNo = (label: string, value: boolean | null | undefined, required: boolean): string[] =>
  required && isUnanswered(value) ? [label] : [];

// Drafts saved before the ILR step existed have no section at all.
const ild = (d: WizardDraft): Partial<IlrLearnerDetails> => d.ilrDetails ?? {};

// Same loose check as the input (steps/IlrLearnerDetails.tsx).
export const NI_RE = /^[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]$/i;
export const NI_OR_APPLIED_KEY = 'National Insurance number, or confirm you have applied for one';

// ── items ───────────────────────────────────────────────────────────────────

type Def = Omit<BuiltinItemDef, 'step'>;

const heading = (key: string, label: string): Def => ({ key, kind: 'heading', label, requirable: false, defaultRequired: false });
const content = (key: string, label: string): Def => ({ key, kind: 'content', label, requirable: false, defaultRequired: false });
const page = (key: string, label: string): Def => ({ key, kind: 'block', label, requirable: false, defaultRequired: false });

/** A plain field: required by default unless `optional`. */
function field(key: string, label: string, missing: Def['missing'], extra: Partial<Def> = {}): Def {
  return { key, kind: 'field', label, requirable: true, defaultRequired: true, missing, ...extra };
}

const STEPS: { slug: string; label: string; sectionStyle?: 'fieldset' | 'section'; ilrDocument?: boolean; items: Def[] }[] = [
  {
    slug: 'introduction',
    label: 'Welcome',
    items: [page('block.introduction', 'Welcome text')],
  },
  {
    slug: 'before-you-begin',
    label: 'Before You Begin',
    items: [page('block.beforeYouBegin', 'Before you begin guidance')],
  },
  {
    slug: 'personal-details',
    label: 'Personal Details',
    ilrDocument: true,
    items: [
      field('pd.firstName', 'First Name', (d, r) => text('First Name', d.personalDetails.firstName, r)),
      field('pd.lastName', 'Last Name', (d, r) => text('Last Name', d.personalDetails.lastName, r)),
      field('pd.email', 'Email', (d, r) => text('Email', d.personalDetails.email, r, 'email')),
      field('pd.phone', 'Phone', (d, r) => text('Phone', d.personalDetails.phone, r, 'tel')),
      field('pd.address', 'Address', (d, r) => text('Address', d.personalDetails.address, r)),
      field('pd.dob', 'Date of Birth (and Age)', (d, r) => {
        if (!r) return [];
        const pd = d.personalDetails;
        const out: string[] = [];
        if (isBlank(pd.dob)) out.push('Date of Birth');
        if (pd.age == null || Number.isNaN(pd.age)) out.push('Age');
        return out;
      }),
      field('pd.sex', 'Sex', (d, r) => text('Sex', d.personalDetails.sex, r), {
        options: () => SEX_OPTIONS,
        path: 'personalDetails.sex',
      }),
      field('pd.signature', 'Your signature', (d, r) => text('Your signature', d.personalDetails.signature, r)),
    ],
  },
  {
    slug: 'ilr-details',
    label: 'ILR',
    sectionStyle: 'section',
    ilrDocument: true,
    items: [
      heading('ild.h.learner', 'Learner Details'),
      content('ild.record', 'Details from the learner record (name, date of birth, address)'),
      field('ild.yearsAtAddress', 'How long have you been at this address (years)?', (d, r) => {
        const i = ild(d);
        return r && i.yearsAtAddress == null && i.sinceBirth !== true ? ['How long have you been at this address (years)?'] : [];
      }, { defaultRequired: false }),
      content('ild.telephone', 'Telephone number (from the learner record)'),
      field('ild.postcodePrior', 'Postcode prior to enrolment', (d, r) =>
        text('Postcode prior to enrolment', ild(d).postcodePriorToEnrolment, r)),
      field('ild.ni', 'National Insurance number', (d, r) => {
        const i = ild(d);
        const ni = (i.niNumber ?? '').trim();
        if (!ni) return r && i.niApplied !== true ? [NI_OR_APPLIED_KEY] : [];
        return NI_RE.test(ni) ? [] : ['National Insurance number — enter a valid number, for example AB 12 34 56 C'];
      }),
      content('ild.email', 'Email address (from the learner record)'),
      field('ild.legalSex', 'Legal Sex', (d, r) => text('Legal Sex', ild(d).legalSex, r), {
        options: () => LEGAL_SEX_OPTIONS,
        path: 'ilrDetails.legalSex',
      }),
      field('ild.pronouns', 'What pronouns do you use?', (d, r) => text('What pronouns do you use?', ild(d).pronouns, r), {
        defaultRequired: false,
      }),
      field('ild.ethnicity', 'Ethnicity', (d, r) => text('Ethnicity', ild(d).ethnicity, r), {
        options: () => labels(ETHNICITY_OPTIONS),
        path: 'ilrDetails.ethnicity',
      }),
      heading('ild.h.health', 'Health'),
      field('ild.disability', 'Long-term disability, health problem or learning difficulty', (d, r) =>
        yesNo('Long-term disability, health problem or learning difficulty', ild(d).longTermDisability, r)),
      heading('ild.h.education', 'Education level'),
      field('ild.highestQualification', 'Highest qualification achieved', (d, r) =>
        text('Highest qualification achieved', ild(d).highestQualification, r), {
        options: () => labels(PRIOR_ATTAINMENT_OPTIONS),
        path: 'ilrDetails.highestQualification',
      }),
      heading('ild.h.employment', 'Employment Status'),
      field('ild.employmentStatus', 'Employment status (and its follow-up questions)', (d, r) => {
        const i = ild(d);
        if (!r) return [];
        const out = isBlank(i.employmentStatus) ? ['Employment status'] : [];
        // Only asked of learners in paid employment.
        if (i.employmentStatus === IN_PAID_EMPLOYMENT && isBlank(i.employmentStartDate)) out.push('Start date with employer');
        return out;
      }, {
        options: () => labels(EMPLOYMENT_STATUS_OPTIONS),
        path: 'ilrDetails.employmentStatus',
      }),
      field('ild.fullTimeEducation', 'Full-time education or training', (d, r) =>
        yesNo('Full-time education or training', ild(d).fullTimeEducation, r)),
      field('ild.stateBenefits', 'State benefits', (d, r) => text('State benefits', ild(d).stateBenefits, r), {
        defaultRequired: false,
        options: () => labels(STATE_BENEFIT_OPTIONS),
        path: 'ilrDetails.stateBenefits',
      }),
      heading('ild.h.declaration', 'Declaration'),
      content('ild.declaration', 'Privacy notice and declaration text'),
      field('ild.signature', ILR_SIGNATURE_LABEL, (d, r) => text(ILR_SIGNATURE_LABEL, ild(d).signature ?? undefined, r)),
    ],
  },
  {
    slug: 'ilr',
    label: 'Extended ILR',
    sectionStyle: 'fieldset',
    ilrDocument: true,
    items: [
      heading('xilr.h.contact', 'Contact Preferences'),
      field('xilr.contactPost', 'Contact by post', (d, r) => yesNo('Contact by post', d.ilr.contact.byPost, r)),
      field('xilr.contactPhone', 'Contact by phone', (d, r) => yesNo('Contact by phone', d.ilr.contact.byPhone, r)),
      field('xilr.contactEmail', 'Contact by e-mail', (d, r) => yesNo('Contact by e-mail', d.ilr.contact.byEmail, r)),
      heading('xilr.h.nextOfKin', 'Emergency contact details / Next of kin'),
      field('xilr.nokName', 'Next of kin — full name', (d, r) => text('Next of kin — full name', d.ilr.nextOfKin.fullName, r)),
      field('xilr.nokRelationship', 'Next of kin — relationship', (d, r) =>
        text('Next of kin — relationship', d.ilr.nextOfKin.relationship, r)),
      field('xilr.nokEmail', 'Next of kin — email', (d, r) => text('Next of kin — email', d.ilr.nextOfKin.email, r, 'email')),
      field('xilr.nokPhone', 'Next of kin — phone', (d, r) => text('Next of kin — phone', d.ilr.nextOfKin.phone, r, 'tel')),
      field('xilr.nokSameAddress', 'Next of kin — same address (and their address)', (d, r) => {
        if (!r) return [];
        const k = d.ilr.nextOfKin;
        const out = yesNo('Next of kin — same address', k.sameAddressAsLearner, true);
        if (k.sameAddressAsLearner === false) {
          if (isBlank(k.postcode)) out.push('Next of kin — postcode');
          if (isBlank(k.address)) out.push('Next of kin — address');
        }
        return out;
      }),
      heading('xilr.h.eligibility', 'Eligibility'),
      field('xilr.employedInEngland', 'Employed in England', (d, r) => yesNo('Employed in England', d.ilr.eligibility.employedInEngland, r)),
      field('xilr.countryOfResidence', 'Country of residence', (d, r) =>
        text('Country of residence', d.ilr.eligibility.countryOfResidence, r), {
        options: () => COUNTRY_OPTIONS,
        path: 'ilr.eligibility.countryOfResidence',
      }),
      field('xilr.ukEeaNational', 'UK / EEA national', (d, r) => yesNo('UK / EEA national', d.ilr.eligibility.ukEeaNational, r)),
      field('xilr.nationality', 'Nationality', (d, r) => text('Nationality', d.ilr.eligibility.nationality, r), {
        options: () => NATIONALITY_OPTIONS,
        path: 'ilr.eligibility.nationality',
      }),
      field('xilr.resident3Years', 'Resident for the previous 3 years', (d, r) =>
        yesNo('Resident for the previous 3 years', d.ilr.eligibility.residentPrev3Years, r)),
      // Only asked of learners who have NOT been resident for 3 years.
      field('xilr.yearsInUk', 'Years in the UK', (d, r) =>
        r && d.ilr.eligibility.residentPrev3Years === false && d.ilr.eligibility.yearsInUk == null ? ['Years in the UK'] : []),
      // Only asked of non-UK/EEA nationals.
      field('xilr.workPermit', 'Requires a work permit', (d, r) =>
        r && d.ilr.eligibility.ukEeaNational === false && isUnanswered(d.ilr.eligibility.requiresWorkPermit) ? ['Requires a work permit'] : []),
      field('xilr.evidence', 'Proof of identification and residency', (d, r) =>
        r && isBlank(d.ilr.eligibility.evidenceDescription) ? ['Proof of identification and residency'] : [], {
        defaultRequired: false,
      }),
      heading('xilr.h.employer', 'Employer Details'),
      field('xilr.employerName', 'Employer — organisation name', (d, r) => text('Employer — organisation name', d.ilr.employer.organisationName, r)),
      field('xilr.employerPostcode', 'Employer — postcode', (d, r) => text('Employer — postcode', d.ilr.employer.postcode, r)),
      field('xilr.employerAddress', 'Employer — address', (d, r) => text('Employer — address', d.ilr.employer.address, r)),
      field('xilr.employerCity', 'Employer — city', (d, r) => text('Employer — city', d.ilr.employer.city, r)),
      field('xilr.lineManagerName', 'Line manager — name', (d, r) => text('Line manager — name', d.ilr.employer.lineManagerName, r)),
      field('xilr.lineManagerEmail', 'Line manager — email', (d, r) =>
        text('Line manager — email', d.ilr.employer.lineManagerEmail, r, 'email')),
      field('xilr.lineManagerPhone', 'Line manager — phone', (d, r) =>
        text('Line manager — phone', d.ilr.employer.lineManagerPhone, r, 'tel')),
      heading('xilr.h.otherTraining', 'Other training'),
      field('xilr.otherTraining', 'Other training in the last 12 months (and when completed)', (d, r) => {
        if (!r) return [];
        const t = d.ilr.otherTraining;
        const out = yesNo('Other training in the last 12 months', t.attended12m, true);
        // The date is only asked of those who attended.
        if (t.attended12m === true && isBlank(t.completedWhen)) out.push('Other training — when completed');
        return out;
      }),
      heading('xilr.h.circumstances', 'Personal Circumstances'),
      field('xilr.caring', 'Caring responsibilities', (d, r) => text('Caring responsibilities', d.ilr.circumstances.caringResponsibilities, r)),
      field('xilr.otherCircumstances', 'Other personal circumstances', (d, r) =>
        text('Other personal circumstances', d.ilr.circumstances.other, r), { defaultRequired: false }),
      field('xilr.careLeaver', 'Care leaver', (d, r) => yesNo('Care leaver', d.ilr.circumstances.careLeaver, r)),
      heading('xilr.h.understanding', 'Programme understanding'),
      field('xilr.programmeUnderstanding', 'Understanding of the programme', (d, r) =>
        text('Understanding of the programme', d.ilr.understanding.programmeUnderstanding, r)),
      field('xilr.careerProgression', 'Career progression', (d, r) => text('Career progression', d.ilr.understanding.careerProgression, r)),
      heading('xilr.h.additional', 'Additional Information'),
      // Read defensively: answers saved before the section existed have no
      // `additionalInformation` until the wizard's defaults are merged in.
      field('xilr.jobRoleRelevance', 'Job role and the programme', (d, r) =>
        text('Job role and the programme', d.ilr.additionalInformation?.jobRoleRelevance, r)),
      field('xilr.residenceNotFte', 'Residence not for full-time education', (d, r) =>
        text('Residence not for full-time education', d.ilr.additionalInformation?.residenceNotForFullTimeEducation, r), {
        options: () => YES_NO_SELECT,
        path: 'ilr.additionalInformation.residenceNotForFullTimeEducation',
      }),
      field('xilr.ehcp', 'EHCP', (d, r) => text('EHCP', d.ilr.additionalInformation?.ehcp, r), {
        options: () => YES_NO_SELECT,
        path: 'ilr.additionalInformation.ehcp',
      }),
      field('xilr.over50Percent', 'Declaration — over 50% in England', (d, r) =>
        yesNo('Declaration — over 50% in England', d.ilr.declarations.over50PercentEngland, r)),
      field('xilr.wageRate', 'Wage rate band', (d, r) => text('Wage rate band', d.ilr.declarations.wageRateBand, r), {
        options: () => [...IlrText.wageRateOptions],
        path: 'ilr.declarations.wageRateBand',
      }),
      field('xilr.knownByOtherName', 'Known by another name', (d, r) =>
        yesNo('Known by another name', d.ilr.declarations.knownByOtherName, r)),
      field('xilr.plrAccessAware', 'Aware provider will access PLR', (d, r) =>
        yesNo('Aware provider will access PLR', d.ilr.declarations.plrAccessAware, r)),
      content('xilr.documentActions', 'Download / file the ILR document buttons'),
    ],
  },
  {
    slug: 'cv-job',
    label: 'CV/Job Description',
    sectionStyle: 'section',
    items: [
      heading('cv.h.experience', 'Your Work Experience'),
      field('cv.cvUpload', 'CV upload', undefined, { requirable: false, defaultRequired: false }),
      field('cv.experience', 'Previous experience (or N/A)', (d, r) => text('Previous experience (or N/A)', d.cvJob.experienceText, r)),
      field('cv.highestQualification', 'Highest-level qualification', (d, r) =>
        text('Highest-level qualification', d.cvJob.highestQualification, r)),
      field('cv.highestQualificationField', 'Field of highest qualification', (d, r) =>
        text('Field of highest qualification', d.cvJob.highestQualificationField, r)),
      field('cv.fieldQualification', 'Qualifications in your programme field (and transcript)', (d, r) => {
        if (!r) return [];
        const cv = d.cvJob;
        const out = yesNo('Qualifications in your programme field', cv.hasFieldQualification, true);
        if (cv.hasFieldQualification === true && isBlank(cv.highestFieldQualification)) {
          out.push('Highest qualification in your programme field');
        }
        return out;
      }),
      heading('cv.h.gcse', 'GCSEs'),
      field('cv.gcseEnglish', 'GCSE in English', (d, r) => yesNo('GCSE in English', d.cvJob.gcseEnglish, r)),
      field('cv.gcseMaths', 'GCSE in Maths', (d, r) => yesNo('GCSE in Maths', d.cvJob.gcseMaths, r)),
      heading('cv.h.functionalSkills', 'Functional Skills'),
      field('cv.functionalSkills', 'Functional Skills enrolment', (d, r) =>
        text('Functional Skills enrolment', d.cvJob.functionalSkillsEnrol, r), {
        options: () => [...CvJobText.functionalSkillsOptions],
        path: 'cvJob.functionalSkillsEnrol',
      }),
    ],
  },
  {
    slug: 'plr',
    label: 'Personal Learning Record',
    // Nothing is required: a learner may have no prior learning to record.
    items: [{ key: 'block.plr', kind: 'block', label: 'Personal Learning Record (prior qualifications)', requirable: false, defaultRequired: false }],
  },
  {
    slug: 'skills-radar',
    label: 'Skills Radar',
    items: [{
      key: 'block.skillsRadar',
      kind: 'block',
      label: 'Skills Radar self-assessment',
      requirable: true,
      defaultRequired: true,
      /**
       * Every KSB needs a level. Measured against the assessments in the draft,
       * which the wizard seeds with an unrated row per competency; a programme
       * with no authored competencies has nothing to seed and blocks nobody.
       */
      missing: (d, r) => {
        if (!r) return [];
        const assessments = d.skillsRadar.assessments ?? {};
        const asked = d.skillsRadar.programmeKsbIds;
        // Only the programme's own KSBs count. Before the profile has loaded,
        // fall back to every row the draft holds.
        const unrated = asked
          ? asked.filter((id) => assessments[id]?.level == null).length
          : Object.values(assessments).filter((a) => a.level == null).length;
        return unrated > 0 ? [`${unrated} ${unrated === 1 ? 'competency' : 'competencies'} not yet rated`] : [];
      },
    }],
  },
  {
    slug: 'inclusion',
    label: 'Inclusion',
    items: [{
      key: 'block.inclusion',
      kind: 'block',
      label: 'Inclusion assessment (Safeguarding site)',
      requirable: true,
      defaultRequired: true,
      /**
       * Done once the Safeguarding database holds a response for one of the
       * learner's emails (draft.inclusion, read when the wizard opens). Until
       * that check has come back the answer is unknown, and unknown is not
       * done. Commercial learners never take it.
       */
      missing: (d, r) => {
        if (!r || d.inclusion?.applicable === false || d.inclusion?.completed) return [];
        return ['Inclusion assessment not yet completed'];
      },
    }],
  },
  {
    slug: 'policies',
    label: 'Policies',
    items: [{
      key: 'block.policies',
      kind: 'block',
      label: 'Policy documents to acknowledge',
      requirable: true,
      defaultRequired: true,
      missing: (d, r) => {
        if (!r) return [];
        const ack = d.policies.acknowledged ?? {};
        const outstanding = POLICY_DOCS_KBC.filter((doc) => !ack[doc.id]).length;
        return outstanding > 0 ? [`${outstanding} ${outstanding === 1 ? 'document' : 'documents'} not acknowledged`] : [];
      },
    }],
  },
  {
    slug: 'next-steps',
    label: 'What Happens Now?',
    items: [page('block.nextSteps', 'Next steps and compliance meeting booking')],
  },
];

export const BUILTIN_STEPS: BuiltinStepDef[] = STEPS.map((s) => ({
  slug: s.slug,
  label: s.label,
  sectionStyle: s.sectionStyle,
  items: s.items.map((i) => i.key),
}));

export const BUILTIN_ITEMS: Record<string, BuiltinItemDef> = Object.fromEntries(
  STEPS.flatMap((s) =>
    s.items.map((i) => [i.key, { ...i, step: s.slug, onIlrDocument: Boolean(s.ilrDocument && (i.kind === 'field' || i.kind === 'content')) }])
  )
);

export const BUILTIN_STEP_BY_SLUG: Record<string, BuiltinStepDef> = Object.fromEntries(BUILTIN_STEPS.map((s) => [s.slug, s]));

/** Blocks can be placed on any step; fields, headings and step content stay on their own. */
export const isMovable = (def: BuiltinItemDef | undefined): boolean => def?.kind === 'block';
