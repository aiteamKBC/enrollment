/** DfE LLDDCat values; code 15 expired on 31 July 2025, retained for history. */
export const HEALTH_CONDITIONS = [
  { code: '4', label: 'Vision impairment' },
  { code: '5', label: 'Hearing impairment' },
  { code: '6', label: 'Disability affecting mobility' },
  { code: '7', label: 'Profound complex disabilities' },
  { code: '8', label: 'Social and emotional difficulties' },
  { code: '9', label: 'Mental health difficulty' },
  { code: '10', label: 'Moderate learning difficulty' },
  { code: '11', label: 'Severe learning difficulty' },
  { code: '12', label: 'Dyslexia' },
  { code: '13', label: 'Dyscalculia' },
  { code: '14', label: 'Autism spectrum disorder' },
  { code: '15', label: 'Asperger’s syndrome', expired: true },
  { code: '16', label: 'Temporary disability after illness (for example post-viral) or accident' },
  { code: '17', label: 'Speech, Language and Communication Needs' },
  { code: '18', label: 'Down Syndrome' },
  { code: '93', label: 'Other physical disability' },
  { code: '94', label: 'Other specific learning difficulty (e.g. Dyspraxia)' },
  { code: '95', label: 'Other medical condition (for example epilepsy, asthma, diabetes)' },
  { code: '96', label: 'Other learning difficulty' },
  { code: '97', label: 'Other disability' },
  { code: '98', label: 'Prefer not to say' },
  { code: '99', label: 'Not provided' },
] as const;

export const NONDISCLOSURE_CODES = new Set<string>(['98', '99']);
export const OTHER_HEALTH_CODES = new Set<string>(['93', '94', '95', '96', '97']);
export const healthLabel = (code: string) => HEALTH_CONDITIONS.find(option => option.code === code)?.label ?? `Previously saved code ${code}`;
