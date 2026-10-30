import { flattenReviewFields, type ReviewFieldDefinition, type ReviewSectionDefinition } from '@/api/reviewInstances';


export function fieldAnswered(field: ReviewFieldDefinition, answers: Record<string, unknown>) {
  const value = answers[field.id];
  if (value === undefined || value === null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}


export function fieldAllowsRespondent(field: ReviewFieldDefinition, role: 'participant' | 'employer') {
  const roles = field.configuration?.respondentRoles;
  return Array.isArray(roles) && roles.includes(role);
}


export function importedTableRows(configuration: Record<string, unknown>): string[][] | null {
  let value = configuration.importedTable;
  if (value == null && configuration.imported === true && typeof configuration.description === 'string') {
    try { value = JSON.parse(configuration.description); } catch { return null; }
  }
  if (!Array.isArray(value) || value.length < 2 || !value.every(Array.isArray)) return null;
  const width = value[0].length;
  if (!width || !value.every(row => row.length === width)) return null;
  return value.map(row => row.map(cell => cell == null ? '' : String(cell)));
}


/** Visible required fields with no answer -- a hidden conditional child
 * (its parent boolean_case_block answered the other way) is never included,
 * mirroring the backend's own completion validation. */
/**
 * Every required field the coach can actually see and answer right now: an
 * enabled section's field, not a display-only heading, and -- for a
 * conditional child -- only while its case block's answer reveals it. The
 * set shrinks and grows as case blocks are answered, which is why progress
 * counts and the missing-field check must both be derived from it rather
 * than from the template's flat field list.
 */
export function computeVisibleRequiredFields(sections: ReviewSectionDefinition[], answers: Record<string, unknown>): Set<string> {
  const required = new Set<string>();

  const walk = (fields: ReviewFieldDefinition[], visible: boolean) => {
    for (const field of fields) {
      const isDisplayOnly = field.fieldType === 'title_description' || field.fieldType === 'action_button';
      if (visible && field.required && !isDisplayOnly) {
        required.add(field.id);
      }
      if (field.fieldType === 'boolean_case_block') {
        const answer = answers[field.id];
        walk(field.yesFields || [], visible && answer === 'yes');
        walk(field.noFields || [], visible && answer === 'no');
      }
    }
  };

  for (const section of sections) {
    if (!section.enabled) continue;
    walk(section.fields, true);
  }
  return required;
}


/**
 * Field ids this respondent role ('participant' i.e. Learner, or 'employer')
 * was opted into answering by the Curriculum template, at any nesting depth.
 * Mirrors the backend's review_instances.writable_field_ids_for_role exactly
 * -- the Coach is never checked against this and can always answer every
 * field regardless, so this is only ever used for the Learner/Employer
 * surfaces that share this same renderer read-only by default.
 */
export function computeWritableFieldIds(sections: ReviewSectionDefinition[], role: 'participant' | 'employer'): Set<string> {
  const ids = new Set<string>();
  const walk = (fields: ReviewFieldDefinition[]) => {
    for (const field of fields) {
      const roles = (field.configuration?.respondentRoles as string[] | undefined) || [];
      if (roles.includes(role)) ids.add(field.id);
      walk(field.yesFields || []);
      walk(field.noFields || []);
    }
  };
  for (const section of sections) walk(section.fields);
  return ids;
}


/** Fields for a respondent that are currently visible, including the active
 * branch of conditional questions only. */
export function computeVisibleWritableFieldIds(
  sections: ReviewSectionDefinition[],
  answers: Record<string, unknown>,
  role: 'participant' | 'employer',
): Set<string> {
  const ids = new Set<string>();
  const walk = (fields: ReviewFieldDefinition[], visible: boolean) => {
    for (const field of fields) {
      const displayOnly = field.fieldType === 'title_description' || field.fieldType === 'action_button';
      if (visible && !displayOnly && fieldAllowsRespondent(field, role)) ids.add(field.id);
      if (field.fieldType === 'boolean_case_block') {
        const answer = answers[field.id];
        walk(field.yesFields || [], visible && answer === 'yes');
        walk(field.noFields || [], visible && answer === 'no');
      }
    }
  };
  for (const section of sections) {
    if (section.enabled) walk(section.fields, true);
  }
  return ids;
}


export function computeMissingRequiredFieldsForRole(
  sections: ReviewSectionDefinition[],
  answers: Record<string, unknown>,
  role: 'participant' | 'employer',
): Set<string> {
  const writable = computeVisibleWritableFieldIds(sections, answers, role);
  const missing = new Set<string>();
  const byId = new Map(flattenReviewFields(sections).map((field) => [field.id, field]));
  for (const fieldId of computeVisibleRequiredFields(sections, answers)) {
    if (!writable.has(fieldId)) continue;
    const field = byId.get(fieldId);
    if (field && !fieldAnswered(field, answers)) missing.add(fieldId);
  }
  return missing;
}


export function computeMissingRequiredFields(sections: ReviewSectionDefinition[], answers: Record<string, unknown>): Set<string> {
  const missing = new Set<string>();
  const byId = new Map(flattenReviewFields(sections).map((field) => [field.id, field]));
  for (const fieldId of computeVisibleRequiredFields(sections, answers)) {
    const field = byId.get(fieldId);
    if (field && !fieldAnswered(field, answers)) missing.add(fieldId);
  }
  return missing;
}
