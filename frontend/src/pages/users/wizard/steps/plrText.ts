/** Choices on the Add/Edit PLR form. */

export const QUALIFICATION_TYPE_OPTIONS = [
  'Degree',
  'Higher Education',
  'Access to HE',
  'A Level',
  'AS Level',
  'BTEC',
  'GCSE',
  'CSE',
  'O-Level',
  'NVQ',
  'Key Skill',
  'Functional Skill',
  'Short course',
  'Overseas Qualification',
  'Basic Skill',
  'Other',
];

export const SUBJECT_OPTIONS = ['Maths', 'English', 'ICT', 'Other'];

export const OTHER = 'Other';

/**
 * Split a stored value into the dropdown choice and the "Please specify" text.
 *
 * "Other" is never stored on its own: the typed text is, so the table shows
 * what the learner wrote. Reading it back, anything not in the list — including
 * imported records with free-text subjects — is shown as Other plus that text.
 */
export function splitChoice(value: string, options: readonly string[]): { choice: string; other: string } {
  if (!value) return { choice: '', other: '' };
  if (options.includes(value) && value !== OTHER) return { choice: value, other: '' };
  return { choice: OTHER, other: value === OTHER ? '' : value };
}

export function joinChoice(choice: string, other: string): string {
  return choice === OTHER ? other.trim() : choice;
}
