// ============================================================================
// Option lists for the ILR Learner Details step, from the DfE Individualised
// Learner Record (ILR) specification.
//
// The label is what is stored and printed; the ILR code travels with it so an
// ILR return can be produced without re-deriving it. Check these against the
// current year's ILR specification when it is republished.
// https://www.gov.uk/government/collections/individualised-learner-record-ilr
// ============================================================================

export interface IlrOption {
  code: string;
  label: string;
}

/** ILR "Ethnicity". */
export const ETHNICITY_OPTIONS: IlrOption[] = [
  { code: '31', label: 'English / Welsh / Scottish / Northern Irish / British' },
  { code: '32', label: 'Irish' },
  { code: '33', label: 'Gypsy or Irish Traveller' },
  { code: '34', label: 'Any other White background' },
  { code: '35', label: 'White and Black Caribbean' },
  { code: '36', label: 'White and Black African' },
  { code: '37', label: 'White and Asian' },
  { code: '38', label: 'Any other Mixed / Multiple ethnic background' },
  { code: '39', label: 'Indian' },
  { code: '40', label: 'Pakistani' },
  { code: '41', label: 'Bangladeshi' },
  { code: '42', label: 'Chinese' },
  { code: '43', label: 'Any other Asian background' },
  { code: '44', label: 'African' },
  { code: '45', label: 'Caribbean' },
  { code: '46', label: 'Any other Black / African / Caribbean background' },
  { code: '47', label: 'Arab' },
  { code: '98', label: 'Any other ethnic group' },
  { code: '99', label: 'Prefer not to say' },
];

/** ILR "PriorAttain" — highest level of qualification achieved. */
export const PRIOR_ATTAINMENT_OPTIONS: IlrOption[] = [
  { code: '99', label: 'No qualifications' },
  { code: '9', label: 'Entry level' },
  { code: '1', label: 'Level 1' },
  { code: '2', label: 'Full level 2' },
  { code: '3', label: 'Full level 3' },
  { code: '4', label: 'Level 4' },
  { code: '5', label: 'Level 5' },
  { code: '6', label: 'Level 6' },
  { code: '7', label: 'Level 7 and above' },
  { code: '97', label: 'Other qualification, level not known' },
  { code: '98', label: 'Not known' },
];

/** ILR "EmpStat" — employment status before starting. */
export const EMPLOYMENT_STATUS_OPTIONS: IlrOption[] = [
  { code: '10', label: 'In paid employment' },
  { code: '11', label: 'Not in paid employment, looking for work and available to start work' },
  { code: '12', label: 'Not in paid employment, not looking for work and/or not available to start work' },
  { code: '98', label: 'Not known / not provided' },
];

/** The EmpStat answer that asks about the job itself. */
export const IN_PAID_EMPLOYMENT = 'In paid employment';

/** The EmpStat answers that ask about unemployment instead. */
export const NOT_IN_PAID_EMPLOYMENT = [
  'Not in paid employment, looking for work and available to start work',
  'Not in paid employment, not looking for work and/or not available to start work',
];

/** ILR Employment Status Monitoring "LOU" — length of unemployment. */
export const LENGTH_OF_UNEMPLOYMENT_OPTIONS: IlrOption[] = [
  { code: '1', label: 'Learner has been unemployed for less than 6 months' },
  { code: '2', label: 'Learner has been unemployed for 6-11 months' },
  { code: '3', label: 'Learner has been unemployed for 12-23 months' },
  { code: '4', label: 'Learner has been unemployed for 24-35 months' },
  { code: '5', label: 'Learner has been unemployed for 36 months or more' },
];

/** Whether a claimed benefit is the learner's own, or shared (not as a dependant). */
export const BENEFIT_CLAIM_BASIS_OPTIONS = ['In my own right', 'As part a joint claim'];

/** The "no benefits" answer, which the form preselects. */
export const NO_BENEFITS = 'None';

/** ILR Employment Status Monitoring "BSI" — benefit status indicator. */
export const STATE_BENEFIT_OPTIONS: IlrOption[] = [
  { code: '', label: NO_BENEFITS },
  { code: '1', label: 'Jobseeker’s Allowance (JSA)' },
  { code: '2', label: 'Employment and Support Allowance – Work Related Activity Group (ESA WRAG)' },
  { code: '4', label: 'Universal Credit' },
  { code: '3', label: 'Another state benefit' },
];

/** ILR "Sex". */
export const LEGAL_SEX_OPTIONS = ['Male', 'Female'];

export const labels = (options: IlrOption[]): string[] => options.map((o) => o.label);
