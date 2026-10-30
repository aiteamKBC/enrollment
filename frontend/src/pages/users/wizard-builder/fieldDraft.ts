import type { CustomFieldType, LayoutCondition, LayoutItem } from '../wizard/layout/types';
import type { FollowUpDraft } from './builderOps';

/** The builder's working copy of one custom field while it is being added or edited. */
export interface FieldDraft {
  label: string;
  type: CustomFieldType;
  options: string[];
  helpText: string;
  required: boolean;
  condition: LayoutCondition | null;
  /** Dropdowns: the follow-up question for each answer that has one. */
  followUps: FollowUpDraft[];
  /** Keys of follow-ups the admin unticked or whose answer was deleted. */
  removedFollowUps: string[];
}

export function fieldDraftFrom(item?: LayoutItem): FieldDraft {
  return {
    label: item?.label ?? '',
    type: item?.type ?? 'text',
    options: item?.options ?? [],
    helpText: item?.helpText ?? '',
    required: item?.required ?? false,
    condition: item?.condition ?? null,
    followUps: [],
    removedFollowUps: [],
  };
}
