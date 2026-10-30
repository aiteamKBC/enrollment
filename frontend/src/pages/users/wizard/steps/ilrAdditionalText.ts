/**
 * Wording of the Extended ILR's "Additional Information" section, shared by the
 * wizard step and the generated PDF so the two can never read differently.
 */
export const IlrText = {
  jobRoleRelevance:
    'Please explain how your current job role relates to the apprenticeship programme you are applying for. In your answer, include your job title, main duties, and how this programme will help you develop new knowledge, skills and behaviours in your role.',
  residenceNotForFullTimeEducation:
    'Please confirm that your ordinary residence in the UK for the last 3 years has not been mainly or wholly for the purpose of full-time education.',
  ehcpSharing:
    'I understand that if my EHCP status affects apprenticeship funding, employer co-investment, or any related employer payment, Kent Business College may need to inform my employer that a funding exemption or adjustment applies. Kent Business College will only share the minimum necessary information and will not share my full EHCP document with my employer unless there is a clear lawful reason and I have given my explicit consent.',
  ehcp: 'Do you currently have an Education, Health and Care Plan EHCP issued by a local authority?',
  ehcpUse:
    'This EHCP information helps us confirm any funding eligibility, reasonable adjustments and learning support needs. You may be asked to provide evidence. We will handle this information confidentially and only share it where necessary, lawful and proportionate',
  over50PercentEngland:
    'Could you confirm whether you expect to spend more than 50% of your working hours in England? (The measure of 50% should exclude any time expected to be spent outside of England in remote and/or hybrid working)',
  wageRateBand: 'Please confirm your "current wage rate per hour" is equal to or higher than the minimum wage rates:',
  wageRateLink: 'https://www.gov.uk/national-minimum-wage-rates',
  /** The wage-rate choices; the answer is stored as the label itself. */
  wageRateOptions: [
    'Apprentice Rate £8.00',
    '18–20 Year Old Rate £10.85',
    'National Living Wage (21 and over) £12.71',
    'Above £12.71',
  ],
  knownByOtherName: 'Have you ever been known by any other name?',
  otherNames: 'Please list any other names you have been known by:',
  plrRecord:
    'Your personal learning record (PLR) is a permanent online record of your qualifications and achievements. Your Training Provider needs to access your PLR records to identify if you have any qualifications that could be considered as Recognised Prior Learning or exemptions.',
  plrSharing:
    'Your Personal Learning Record (PLR) data may be shared with authorised organisations where required for apprenticeship delivery, compliance, funding, quality assurance, assessment, and audit purposes. This may include the Department for Education (DfE), End-Point Assessment Organisations (EPAOs), Ofsted, awarding bodies, employers, auditors, and other authorised partners. Your data will be retained for a period of 6 years, in line with relevant funding, our compliance record, and record-keeping requirements.',
  plrAccessAware: 'Please confirm that you are aware your training provider will need to access your PLR:',
} as const;
