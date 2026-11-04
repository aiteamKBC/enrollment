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

/**
 * `text` is retained for layouts published before the text fields were split.
 * New fields should use the explicit single-line or multi-line type.
 */
export type CustomFieldType =
  | 'text'
  | 'single_line'
  | 'multi_line'
  | 'number'
  | 'upload'
  | 'dropdown'
  | 'heading'
  | 'description'
  | 'divider'
  | 'radio'
  | 'checkbox';

export const CUSTOM_FIELD_TYPES: { value: CustomFieldType; label: string }[] = [
  { value: 'single_line', label: 'Single-line text' },
  { value: 'multi_line', label: 'Multi-line text' },
  { value: 'number', label: 'Number' },
  { value: 'dropdown', label: 'Dropdown' },
  { value: 'upload', label: 'File upload' },
  { value: 'heading', label: 'Heading' },
  { value: 'description', label: 'Description' },
  { value: 'divider', label: 'Divider' },
  { value: 'radio', label: 'Radio (single choice)' },
  { value: 'checkbox', label: 'Checkbox (multiple choice)' },
];

/** Keep an already-published legacy `text` field selectable without offering it for new fields. */
export function fieldTypeOptions(current?: CustomFieldType): { value: CustomFieldType; label: string }[] {
  return current === 'text'
    ? [{ value: 'text', label: 'Single-line text (existing)' }, ...CUSTOM_FIELD_TYPES]
    : CUSTOM_FIELD_TYPES;
}

export const CONTENT_FIELD_TYPES: CustomFieldType[] = ['heading', 'description', 'divider'];
export const CHOICE_FIELD_TYPES: CustomFieldType[] = ['radio', 'checkbox'];

export function isContentFieldType(type?: CustomFieldType): boolean {
  return Boolean(type && CONTENT_FIELD_TYPES.includes(type));
}

export function answerFieldTypeOptions(current?: CustomFieldType): { value: CustomFieldType; label: string }[] {
  return fieldTypeOptions(current).filter((option) => !isContentFieldType(option.value));
}

export function isChoiceFieldType(type?: CustomFieldType): boolean {
  return Boolean(type && CHOICE_FIELD_TYPES.includes(type));
}

export function storesCustomAnswer(type?: CustomFieldType): boolean {
  return Boolean(type && !isContentFieldType(type));
}

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
  skillsRadarCatalogues?: import('@/features/programme-suitability/skills-radar/catalogue/types').RadarCatalogues;
  skillsRadarImportApprovals?: import('@/features/programme-suitability/skills-radar/catalogue/types').CatalogueDraft['skillsRadarImportApprovals'];
  programmeModules?: import('@/features/programme-suitability/modules/configuration').ModulesConfiguration;
  programmeSuitability?: import('@/features/programme-suitability/types').Configuration;
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
  /** Live global field visibility, independent of published layout versions. */
  fieldVisibility?: Record<string, boolean>;
  /** Signed learner-bound reference, present only on learner-specific reads. */
  layoutToken?: string | null;
  updatedAt: string;
  updatedBy: string;
}

/** A stored file on a custom upload field — what the draft keeps for validation. */
export interface CustomUploadRef {
  id: string;
  filename: string;
}

export type CustomAnswer = string | string[] | CustomUploadRef[] | null;
