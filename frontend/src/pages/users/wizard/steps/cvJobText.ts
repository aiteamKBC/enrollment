/** Wording of the CV/Job Description step. */

export const CvJobText = {
  intro:
    'It is important that we match your previous experience and current job role and responsibilities to ensure this apprenticeship is the most appropriate route for you.',
  cvUpload: 'Please upload an up to date CV which includes your current job role:',
  experience:
    'If you do not have a CV to upload, please list your previous experience and description of your current job role and responsibilities (If you uploaded the CV, please write N/A):',
  highestQualification: 'Please specify the highest-level qualification you have achieved to date:',
  highestQualificationField: 'In which field have you achieved this highest-level qualification?',
  transcript: 'Please upload the transcript of this degree (To consider any prior learning)',
  gcseNote:
    'Please note regarding your GCSE qualifications: If you selected "Yes" to confirm that you have the certificates, you must either upload a clear copy of the certificates or ensure the qualification is recorded within your PLR (Personal Learning Record) so that it is formally documented. If this evidence is not available in your PLR and you are unable to provide a copy of the certificate, we will update your selection to "No" to ensure the information remains accurate and to avoid delays in processing.',
  gcseEnglish: 'Do you have GCSE in English',
  gcseEnglishUpload: 'Please upload your English GCSE evidence:',
  gcseMaths: 'Do you have GCSE in Maths',
  gcseMathsUpload: 'Please upload your Maths GCSE evidence:',
  functionalSkillsNote:
    'Functional Skills are English and maths qualifications that support everyday life and work. If you are 16 to 18 and do not already hold GCSE English and/or maths at grade 4/C or above, you will normally be required to complete them. If you are 19 or over, you may usually choose to opt in or opt out. If you already hold the required GCSEs, you will normally be exempt.',
  functionalSkills:
    'If you do not have GCSEs available, or if you do not meet the required grades in English and Maths, would you like to enrol in a funded Functional Skills course in these subjects?',
  functionalSkillsOptions: [
    'Yes, I would like to enrol in the funded English and Maths Functional Skills courses',
    'I would like to enrol in Maths functional skills course only',
    'I would like to enrol in English functional skills course only',
    'No, I would prefer to opt out',
    'I have uploaded my GCSE certificates',
  ],
} as const;

/**
 * The subject area the "field" questions ask about, from the learner's
 * programme: marketing, project controls or project management. Null for any
 * other (or no) programme, which gets neutral wording instead.
 */
export function programmeField(programme?: string | null): string | null {
  const p = (programme ?? '').toLowerCase();
  if (p.includes('marketing')) return 'marketing';
  if (p.includes('project controls')) return 'project controls';
  if (p.includes('project manage')) return 'project management';
  return null;
}

export function fieldQualificationLabels(programme?: string | null) {
  const field = programmeField(programme);
  return field
    ? {
        hasFieldQualification: `Do you have any ${field} qualifications?`,
        highestFieldQualification: `Please specify your highest qualification in the field of ${field}`,
      }
    : {
        hasFieldQualification: 'Do you have any qualifications in the field of your programme?',
        highestFieldQualification: 'Please specify your highest qualification in the field of your programme',
      };
}
