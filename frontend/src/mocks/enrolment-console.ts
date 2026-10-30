// ============================================================================
// Enrolment Console — reference/config data (frontend only)
// User rows and boards come from the API (see src/api/enrolmentUsers.ts); this
// file holds only static reference data the wizard needs (KSBs, RAG levels,
// policy docs, select option lists).
// ============================================================================
import type {
  Ksb,
  PolicyDoc,
  RagLevel,
} from '@/pages/users/types';

// ---- RAG levels meta (Skills Radar) — best → worst ----
export interface RagLevelMeta {
  level: RagLevel;
  label: string;
  help: string;
  cellFill: string; // filled radar cell
  dot: string; // corner marker
  tintBg: string;
  tintBorder: string;
  tintText: string;
}

/**
 * The learner-facing self-assessment scale: 9 options scored 9 (Champion) down to
 * 1 (Never), highest first, as presented in the questionnaire. Champion was added
 * above Mastery in October 2026; every other option kept its score, so ratings
 * saved before then mean what they always did.
 *
 * RAG_LEVELS below is the older 5-point scale, kept only so assessments saved
 * against it still render (see the legacy values on the RagLevel union).
 */
export interface CompetenceLevelMeta extends RagLevelMeta {
  /** 9..1 — shown as the pill beside each option and used for scoring. */
  score: number;
}

export const COMPETENCE_LEVELS: CompetenceLevelMeta[] = [
  { level: 'champion', score: 9, label: 'Champion', help: 'I set the standard in this area: I am recognised as a role model, train others and lead improvements across the organisation.', cellFill: 'bg-emerald-700', dot: 'bg-emerald-700', tintBg: 'bg-emerald-50', tintBorder: 'border-emerald-400', tintText: 'text-emerald-800' },
  { level: 'mastery', score: 8, label: 'Mastery', help: 'I do not need any training as I am fully competent and can evidence my abilities.', cellFill: 'bg-emerald-600', dot: 'bg-emerald-600', tintBg: 'bg-emerald-50', tintBorder: 'border-emerald-300', tintText: 'text-emerald-700' },
  { level: 'expert', score: 7, label: 'Expert', help: 'I coach others, solve complex problems, and drive improvements with minimal oversight.', cellFill: 'bg-emerald-500', dot: 'bg-emerald-500', tintBg: 'bg-emerald-50', tintBorder: 'border-emerald-300', tintText: 'text-emerald-700' },
  { level: 'proficient', score: 6, label: 'Proficient', help: 'I work independently on typical tasks; targeted training would enhance advanced competence.', cellFill: 'bg-teal-500', dot: 'bg-teal-500', tintBg: 'bg-teal-50', tintBorder: 'border-teal-300', tintText: 'text-teal-700' },
  { level: 'consistently', score: 5, label: 'Consistently', help: 'I am confident in my ability but would benefit from further training to be fully competent.', cellFill: 'bg-amber-400', dot: 'bg-amber-400', tintBg: 'bg-amber-50', tintBorder: 'border-amber-300', tintText: 'text-amber-700' },
  { level: 'frequently', score: 4, label: 'Frequently', help: 'I can perform routine tasks with occasional support; additional training would improve consistency.', cellFill: 'bg-amber-500', dot: 'bg-amber-500', tintBg: 'bg-amber-50', tintBorder: 'border-amber-300', tintText: 'text-amber-700' },
  { level: 'occasionally', score: 3, label: 'Occasionally', help: 'I can support simple tasks but still need training and close supervision.', cellFill: 'bg-orange-500', dot: 'bg-orange-500', tintBg: 'bg-orange-50', tintBorder: 'border-orange-300', tintText: 'text-orange-700' },
  { level: 'rarely', score: 2, label: 'Rarely', help: 'I have some basic experience but still need training.', cellFill: 'bg-rose-500', dot: 'bg-rose-500', tintBg: 'bg-rose-50', tintBorder: 'border-rose-300', tintText: 'text-rose-700' },
  { level: 'never', score: 1, label: 'Never', help: 'I have no experience and need training.', cellFill: 'bg-red-500', dot: 'bg-red-500', tintBg: 'bg-red-50', tintBorder: 'border-red-300', tintText: 'text-red-700' },
];

/** The top of the scale, for anything drawn in proportion to it. */
export const MAX_COMPETENCE_SCORE = Math.max(...COMPETENCE_LEVELS.map((l) => l.score));

/** Score for a stored level, mapping legacy 5-point values onto the new scale. */
const LEGACY_SCORES: Record<string, number> = { always: 8, often: 5, sometimes: 3 };

export function competenceScore(level?: string | null): number | null {
  if (!level) return null;
  const found = COMPETENCE_LEVELS.find((l) => l.level === level);
  return found ? found.score : LEGACY_SCORES[level] ?? null;
}

export function competenceMeta(level?: string | null): CompetenceLevelMeta | null {
  if (!level) return null;
  const found = COMPETENCE_LEVELS.find((l) => l.level === level);
  if (found) return found;
  // Legacy value — show it on the nearest new option so old data still renders.
  const score = LEGACY_SCORES[level];
  return score ? COMPETENCE_LEVELS.find((l) => l.score === score) ?? null : null;
}

export const RAG_LEVELS: RagLevelMeta[] = [
  { level: 'always', label: 'Always', help: 'I do not need any training as I am fully competent and can evidence my abilities.', cellFill: 'bg-blue-500', dot: 'bg-blue-500', tintBg: 'bg-blue-50', tintBorder: 'border-blue-300', tintText: 'text-blue-700' },
  { level: 'often', label: 'Often', help: 'I am confident in my ability but would benefit from further training to be fully competent.', cellFill: 'bg-emerald-500', dot: 'bg-emerald-500', tintBg: 'bg-emerald-50', tintBorder: 'border-emerald-300', tintText: 'text-emerald-700' },
  { level: 'sometimes', label: 'Sometimes', help: 'I have limited experience and need further training.', cellFill: 'bg-amber-400', dot: 'bg-amber-400', tintBg: 'bg-amber-50', tintBorder: 'border-amber-300', tintText: 'text-amber-700' },
  { level: 'rarely', label: 'Rarely', help: 'I have some basic experience but still need training.', cellFill: 'bg-orange-500', dot: 'bg-orange-500', tintBg: 'bg-orange-50', tintBorder: 'border-orange-300', tintText: 'text-orange-700' },
  { level: 'never', label: 'Never', help: 'I have no experience and need training.', cellFill: 'bg-red-500', dot: 'bg-red-500', tintBg: 'bg-red-50', tintBorder: 'border-red-300', tintText: 'text-red-700' },
];

// ---- Skills Radar: PCP Level 6 KSB dataset ----
export const PCP_STANDARD = { id: 'pcp-l6', label: 'Project Controls Professional Level 6' };

export const PCP_KSBS: Ksb[] = [
  // Strategic Project Management
  { id: 'PCP-K1', theme: 'Strategic Project Management', kind: 'Knowledge', codes: ['K1'], title: 'Organisational and business strategies' },
  { id: 'PCP-K30', theme: 'Strategic Project Management', kind: 'Knowledge', codes: ['K30'], title: 'Leadership strategies' },
  { id: 'PCP-B3B4', theme: 'Strategic Project Management', kind: 'Behaviour', codes: ['B3', 'B4'], title: 'Commercial astuteness; Pre-emptive thinking' },
  // Scope
  { id: 'PCP-K2K6', theme: 'Scope', kind: 'Knowledge', codes: ['K2', 'K6'], title: 'Principles of project control and project life cycle; Breakdown and coding structures' },
  { id: 'PCP-S18S20', theme: 'Scope', kind: 'Skill', codes: ['S18', 'S20'], title: 'Preparing estimating framework; Preparing planning and scheduling strategic frameworks' },
  // Time & Cost
  { id: 'PCP-K22K23K20', theme: 'Time & Cost', kind: 'Knowledge', codes: ['K22', 'K23', 'K20'], title: 'Planning and scheduling practice; Modelling techniques; Estimating techniques' },
  { id: 'PCP-S23S21S19', theme: 'Time & Cost', kind: 'Skill', codes: ['S23', 'S21', 'S19'], title: 'Applying cost engineering practice; Creating credible control schedules; Evidence-based estimating' },
  // Integration & CMS
  { id: 'PCP-K7K8', theme: 'Integration & CMS', kind: 'Knowledge', codes: ['K7', 'K8'], title: 'Project Control Plans and reporting frameworks; Strategic principles of change management systems' },
  { id: 'PCP-S28S26', theme: 'Integration & CMS', kind: 'Skill', codes: ['S28', 'S26'], title: 'Steering project controls functions and mentoring team members; Identifying and explaining integration' },
  // Quality
  { id: 'PCP-K21K31', theme: 'Quality', kind: 'Knowledge', codes: ['K21', 'K31'], title: 'Assurance techniques; Continuous improvement' },
  { id: 'PCP-S29', theme: 'Quality', kind: 'Skill', codes: ['S29'], title: 'Applying continuous improvement approaches' },
  { id: 'PCP-B9', theme: 'Quality', kind: 'Behaviour', codes: ['B9'], title: 'Innovation; learning from innovative solutions and seeking out new ideas to deliver' },
  // Risk and Health & Safety
  { id: 'PCP-K15K18', theme: 'Risk and Health & Safety', kind: 'Knowledge', codes: ['K15', 'K18'], title: 'Risk management and risk process; Environmental impact and sustainability' },
  { id: 'PCP-B1', theme: 'Risk and Health & Safety', kind: 'Behaviour', codes: ['B1'], title: 'Safety culture' },
  { id: 'PCP-S14S9', theme: 'Risk and Health & Safety', kind: 'Skill', codes: ['S14', 'S9'], title: 'Risk management and analysis; Ensuring project control work adheres to standards' },
  // Communication and Engagement
  { id: 'PCP-K14', theme: 'Communication and Engagement', kind: 'Knowledge', codes: ['K14'], title: 'Approaches to communicating with stakeholders' },
  { id: 'PCP-S27S13', theme: 'Communication and Engagement', kind: 'Skill', codes: ['S27', 'S13'], title: 'Communicating and justifying conclusions and recommendations; Identifying stakeholders' },
  { id: 'PCP-B8', theme: 'Communication and Engagement', kind: 'Behaviour', codes: ['B8'], title: 'Collaboration; interacting within a wide, multi-disciplinary team' },
  // Procurement and Contracting
  { id: 'PCP-K18K19', theme: 'Procurement and Contracting', kind: 'Knowledge', codes: ['K18', 'K19'], title: 'Commercial matters; Key principles of invitations to tender' },
  { id: 'PCP-S16S17', theme: 'Procurement and Contracting', kind: 'Skill', codes: ['S16', 'S17'], title: 'Commercial matters and subcontractor/supplier performance management' },
];

// ---- Policies (Tab 8) ----
// The Kent Business College policies each learner confirms they have read. The
// PDFs are in Azure (enrolment-docs/policies/kbc/, uploaded by
// `manage.py upload_policy_documents`) and open through the backend, which
// checks the viewer is signed in and redirects to a short-lived link.
//
// The ids are what acknowledgements are stored against, and must match
// backend/enrolment_api/policy_documents.py. Never reuse one for a different
// document: a replaced policy gets a new id, so an earlier tick is not counted
// for a file the learner never saw (the old kbc-0…kbc-10 ids belong to the
// previous set and are kept in learners' records as they were).
export const POLICY_DOCS_KBC: PolicyDoc[] = ([
  ['kbc-policy-attendance-engagement', 'Kent Business College - Apprentice Attendance and Engagement Policy.pdf'],
  ['kbc-policy-british-values', 'Kent Business College - British Values.pdf'],
  ['kbc-policy-business-continuity', 'Kent Business College - Business Continuity Policy.pdf'],
  ['kbc-policy-complaint-procedures', 'Kent Business College - Complaint Procedures Policy.pdf'],
  ['kbc-policy-equality-diversity-inclusion', 'Kent Business College - Equality, Diversity and Inclusion Policy.pdf'],
  ['kbc-policy-harassment-bullying', 'Kent Business College - Harassment and Bullying Policy.pdf'],
  ['kbc-policy-health-safety', 'Kent Business College - Health and Safety.pdf'],
  ['kbc-policy-learner-code-of-conduct', 'Kent Business College - Learner Code of Conduct.pdf'],
  ['kbc-policy-safeguarding-prevent', 'Kent Business College - Safeguarding and Prevent Policy.pdf'],
  ['kbc-policy-safeguarding-prevent-handbook', 'Safeguarding and Prevent Handbook Kent Business College.pdf'],
  ['kbc-policy-manager-handbook', 'Manager_Handbook Kent Business College.pdf'],
  ['kbc-policy-library', 'A1_-_Library_Policy.pdf'],
  ['kbc-policy-events-management', 'A2_-_Events_Management_Policy.pdf'],
  ['kbc-policy-events-travel-reimbursement', 'C4_Reimbursement_for_Events_Travel_and_Mileage_Claims_Policy.pdf'],
  ['kbc-policy-events-travel-short-guide', 'C4a - Travel_Reimbursement_for_KBC_Events_Short_Guide (updated).pdf'],
] as const).map(([id, label]) => ({ id, label, url: `/enrolment_api/policy-documents/${id}/`, requiresAck: true }));

// ---- Option lists (selects) ----
export const ETHNICITY_OPTIONS = [
  '31 - English / Welsh / Scottish / Northern Irish / British',
  '32 - Irish',
  '33 - Any other White background',
  '34 - Any other White background',
  '35 - White and Black Caribbean',
  '36 - White and Black African',
  '37 - White and Asian',
  '38 - Any other Mixed background',
  '39 - Indian',
  '40 - Pakistani',
  '41 - Bangladeshi',
  '42 - Chinese',
  '43 - Any other Asian background',
  '44 - African',
  '45 - Caribbean',
  '46 - Any other Black background',
  '47 - Arab',
  '98 - Any other ethnic group',
];

// Keep the mock/export surface compatible with existing imports while using
// the same complete list as every country field in the application.
export { COUNTRY_OPTIONS, NATIONALITY_OPTIONS } from '@/lib/countries';
export const SEX_OPTIONS = ['Male', 'Female', 'Prefer not to say'];
export const GENDER_IDENTITY_OPTIONS = ['Man', 'Woman', 'Non-binary', 'Prefer to self-describe', 'Prefer not to say'];
export const WAGE_BAND_OPTIONS = [
  'National Living Wage (23 and over)',
  'National Minimum Wage (21 to 22)',
  'National Minimum Wage (18 to 20)',
  'Apprentice rate',
  'Other',
];
export const YES_NO_SELECT = ['Yes', 'No'];
export const CASE_OWNER_OPTIONS = ['Ayman Badewi', 'Amgad Badewi', 'Afaan Khan', 'Adeyemi Adeshina'];
