export type DocumentKey = 'apprenticeship-agreement' | 'ilr' | 'training-plan' | 'written-agreement' | 'review-eligibility' | 'review-rpl' | 'review-health-safety' | 'review-uln';
export type ProgrammeId = 'PCP_L6' | 'MM_L6' | 'ME_L4' | 'APM_L4';
import type { CustomFieldType, LayoutCondition } from '@/pages/users/wizard/layout/types';
export interface DocumentQuestion {
  id: string; label: string; type: CustomFieldType; helpText: string; required: boolean;
  condition: LayoutCondition | null; options: { id: string; label: string }[];
  value: string; defaultOptionIds: string[]; systemLinked: boolean;
  binding: { kind: 'value' | 'review' | 'cost'; value?: string; source?: string; key?: string } | null;
  min?: number; max?: number;
}
export interface TextRun { text: string; bold?: boolean; italic?: boolean; underline?: boolean; fontSize?: number }
export interface PageLayout { size: 'a4'; margins: { top: number; right: number; bottom: number; left: number }; header: string; footer: string; pageNumbers: boolean }
export interface BlockStyle { bold?: boolean; italic?: boolean; underline?: boolean; align?: 'left' | 'center' | 'right'; fontSize?: number; spaceAfter?: number }
export interface DocumentBlock {
  id: string; type: 'heading' | 'paragraph' | 'declaration' | 'date' | 'field' | 'table' | 'signature' | 'native' | 'page-break' | 'questions' | 'list';
  text?: string; level?: number; label?: string; value?: string; columns?: string[]; rows?: string[][];
  role?: string; condition?: 'employer-required'; source?: string; labels?: Record<string, string>; style?: BlockStyle;
  fields?: DocumentQuestion[];
  runs?: TextRun[]; ordered?: boolean; items?: TextRun[][];
}
export interface DocumentTemplate {
  schemaVersion: 1; templateId: string; documentKey: DocumentKey; title: string; category: 'Compliance' | 'Review';
  programmes: ProgrammeId[]; blocks: DocumentBlock[]; signatureRoles: string[]; reviewType?: string;
  pageLayout?: PageLayout;
  designTheme?: 'classic' | 'lavender-v1' | 'purple-reference-v2';
}
export interface FieldDefinition { key: string; label: string; group: string; format: 'text' | 'date' | 'number' | 'hours' | 'weeks' | 'yesno'; fallback?: string }
export interface TemplateVersion {
  originalPdfVersion?: 1 | 2;
  /** Frozen on newly generated documents, separate from published wording. */
  presentationTheme?: 'kbc-dfe-purple-v3' | 'kbc-dfe-purple-v4';
  /** Binding/rendering fixes apply to future snapshots; absent means historical v1. */
  bindingVersion?: 1 | 2;
  id: string; number: number; content: DocumentTemplate; contentHash: string; legacyEquivalent: boolean;
  publishedAt: string | null; publishedBy: string; approvalNote: string;
}
export interface IssuedTemplate extends TemplateVersion {
  fields: Record<string, string | number | boolean | null>; issueId?: string; legacy?: boolean;
}
export interface TemplateListItem {
  templateId: string; documentKey: DocumentKey; title: string; category: 'Compliance' | 'Review'; programmes: ProgrammeId[];
  publishedVersion: TemplateVersion | null; draft: { revision: number; updatedAt: string; updatedBy: string } | null; updatedAt: string | null;
}
export interface TemplateIndex { results: TemplateListItem[]; programmes: Record<ProgrammeId, string>; setupRequired: boolean }
export interface TemplateDetail { published: TemplateVersion | null; draft: DocumentTemplate | null; revision: number; baseVersion: string; setupRequired?: boolean }
export interface DraftSaved { content: DocumentTemplate; revision: number; baseVersion: string }
export interface PreviewResponse {
  pdfBase64?: string;
  pdfUnavailable?: string;
  /** Authenticated, learner-scoped persisted PDF; absent uses the saved render snapshot. */
  pdfUrl?: string | null;
  bindingWarnings?: {field:string;code:string;message:string}[];
  template: IssuedTemplate; data: Record<string, any> & { template: IssuedTemplate };
  missingFields: { field: string; label: string }[]; sample: boolean; notice: string; previewToken: string | null;
}
export interface ReviewPrintRow { kind: 'section' | 'text' | 'note' | 'signature'; title?: string; label?: string; text?: string; value?: string; signed?: string; name?: string; date?: string }
