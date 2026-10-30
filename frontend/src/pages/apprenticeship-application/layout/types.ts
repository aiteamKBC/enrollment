/**
 * The eligibility form's editable layout -- what the "Edit eligibility form"
 * builder publishes and /apprenticeship_application renders from.
 *
 * Built-in questions (the ones the form has always asked) are addressed by
 * their answer key from the registry; the layout only says which section each
 * sits in, whether it is shown and required, and for the choice questions
 * their answer list. Custom questions carry their own definition, and the
 * server assigns each one a column in enrolment.eligibility_form (see
 * enrolment_api/eligibility_layout.py) -- the client never chooses storage.
 */

export type EligibilityQuestionType = 'text' | 'number' | 'date' | 'yesno' | 'dropdown';

export const QUESTION_TYPES: { value: EligibilityQuestionType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'date', label: 'Date' },
  { value: 'yesno', label: 'Yes / No' },
  { value: 'dropdown', label: 'Dropdown' },
];

/** Show a question only while another question's answer is one of `values` ("Yes"/"No" for a yes/no question). */
export interface EligibilityCondition {
  field: string;
  values: string[];
}

export interface EligibilityItem {
  key: string;
  builtin: boolean;
  /** Removed from the form. Kept in the layout so its column and answers survive. */
  hidden: boolean;
  /** Undefined on a built-in question = its registry default. */
  required?: boolean;
  /** A built-in choice question's answer list (undefined = the standard list), or a custom dropdown's options. */
  options?: string[];
  // ── custom questions only ──
  label?: string;
  type?: EligibilityQuestionType;
  helpText?: string;
  condition?: EligibilityCondition | null;
  /** Server-assigned column; read-only on the client. */
  column?: string;
}

export interface EligibilitySection {
  slug: string;
  title: string;
  /** Undefined on a built-in section = its standard description. */
  description?: string;
  builtin: boolean;
  hidden: boolean;
  items: EligibilityItem[];
}

export interface EligibilityLayout {
  sections: EligibilitySection[];
  /** Edited wording, by text slot (see registry.ts). A slot not listed here shows its standard wording. */
  texts?: Record<string, string>;
}

/** What the layout endpoints return. `layout` is null until one is published; the public one carries no names. */
export interface EligibilityLayoutResponse {
  layout: EligibilityLayout | null;
  version: number | null;
  updatedAt?: string;
  updatedBy?: string;
}
