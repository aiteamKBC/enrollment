import { Fragment, type ReactNode } from 'react';
import type { ReviewFieldDefinition, ReviewSectionDefinition } from '@/api/reviewInstances';
import { adaptDefinitionSection } from './presentation';
import { ImportedSectionContent } from './ImportedReviewSection';

const keepRespondentControl = (field: ReviewFieldDefinition) => {
  const roles = field.configuration.respondentRoles;
  return Boolean(Array.isArray(roles) && (roles.includes('participant') || roles.includes('employer')));
};

/** Keep the existing permission-aware controls for respondent questions,
 * conditional branches and actions. Callers can preserve their role's controls
 * without duplicating the imported content adapter or changing permissions. */
export function renderImportedSection(
  section: ReviewSectionDefinition,
  answers: Record<string, unknown>,
  renderFields: (fields: ReviewFieldDefinition[]) => ReactNode,
  keepControl: (field: ReviewFieldDefinition) => boolean = keepRespondentControl,
) {
  const groups: { controls: boolean; fields: ReviewFieldDefinition[] }[] = [];
  const hasStructuredFields = section.fields.some(field => !field.id.startsWith('aptem-text:'));
  section.fields.filter(field => section.preserveRawText || !hasStructuredFields || !field.id.startsWith('aptem-text:'))
    .slice().sort((a, b) => a.displayOrder - b.displayOrder).forEach(field => {
    const controls = field.fieldType === 'action_button' || field.fieldType === 'boolean_case_block'
      || keepControl(field);
    if (groups.at(-1)?.controls === controls) groups[groups.length - 1].fields.push(field);
    else groups.push({ controls, fields: [field] });
  });
  return groups.map((group, index) => <Fragment key={index}>{group.controls
    ? renderFields(group.fields)
    : <ImportedSectionContent section={adaptDefinitionSection({ ...section, fields: group.fields }, answers)} />}</Fragment>);
}
