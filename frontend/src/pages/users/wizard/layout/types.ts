/**
 * The enrolment wizard's editable layout — what the "Edit apprenticeship
 * wizard" builder publishes and every wizard (staff and learner) renders from.
 *
 * Built-in content (the fields, section headings and blocks the wizard has
 * always had) is addressed by stable keys from the registry; the layout only
 * says where each one sits, whether it is shown and whether it is required.
 * Custom fields carry their own definition, and the server assigns each one a
 * real column in its step's table in the enrolment schema (see
 * enrolment_api/wizard_layout.py) — the client never chooses storage.
 */

export type CustomFieldType = 'text' | 'number' | 'upload' | 'dropdown';

export const CUSTOM_FIELD_TYPES: { value: CustomFieldType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'dropdown', label: 'Dropdown' },
  { value: 'upload', label: 'File upload' },
];

/** Show an item only while a dropdown answer is one of `values`. */
export interface LayoutCondition {
  /** Key of the dropdown item whose answer decides. */
  field: string;
  values: string[];
  /**
   * Where that answer lives in the draft, for a built-in dropdown (e.g.
   * "ilrDetails.employmentStatus"). Lets the server null a hidden answer
   * without knowing the registry. Absent for a custom dropdown.
   */
  path?: string;
}

export interface LayoutItem {
  key: string;
  builtin: boolean;
  /** Removed from the wizard. Kept in the layout so its column and answers survive. */
  hidden: boolean;
  /** Undefined on a built-in item = its registry default. */
  required?: boolean;
  condition?: LayoutCondition | null;
  // ── custom fields only ──
  label?: string;
  type?: CustomFieldType;
  options?: string[];
  helpText?: string;
  /** Server-assigned storage; read-only on the client. */
  table?: string;
  column?: string;
}

export interface LayoutStep {
  slug: string;
  label: string;
  builtin: boolean;
  hidden: boolean;
  items: LayoutItem[];
  /** Custom steps: their own table, server-assigned. */
  table?: string;
}

export interface WizardLayout {
  steps: LayoutStep[];
  /**
   * Edited wording, by text slot (see texts.ts). A slot not listed here shows
   * its standard wording.
   */
  texts?: Record<string, string>;
}

/** What GET /enrolment_api/wizard-layout/ returns. `layout` is null until one is published. */
export interface WizardLayoutResponse {
  layout: WizardLayout | null;
  version: number | null;
  updatedAt: string;
  updatedBy: string;
}

/** A stored file on a custom upload field — what the draft keeps for validation. */
export interface CustomUploadRef {
  id: string;
  filename: string;
}

export type CustomAnswer = string | CustomUploadRef[] | null;
