import type { CustomAnswer } from './wizard/layout/types';
// ============================================================================
// Enrolment Console — shared types
// Frontend only. Every shape is named so it can be mapped to the API later.
// ============================================================================
import type { TrainingPlan } from '@/api/trainingPlan';

// ---- Screen A: Users List ----
export type UserType = 'User' | 'Employer' | 'Referrer' | 'Admin' | 'Caseowner';
// Mirrors PROGRAMME_STATUS_OPTIONS. Stays open (`| string`) because rows saved
// before the list was trimmed can still carry a retired status.
export type ProgrammeStatus =
  | 'Fresh user'
  | 'Onboarding'
  | 'Delivery'
  | 'Ready to enrol'
  | 'Active'
  | 'Withdrawn'
  | 'On break'
  | 'Completed'
  | string;

export interface UserListRow {
  id: string;
  /** Permanent identifier, the same value before and after the learner becomes
   *  active. `id` remains the integer the current routes are built on; prefer
   *  this when something new needs to name a user. Null only on rows written
   *  before the uuid backfill. */
  uuid: string | null;
  name: string;
  /** Display labels include staff positions and combined roles in the directory. */
  type: string;
  email: string;
  group: string;
  subscriptionStatus: string; // "FullUser"
  subscriptionVerified: boolean; // false -> red ✗
  learningPlan: boolean; // show "Learning plan" link (User rows only)
  /** Whether a learning plan has actually been saved — drives Add vs Edit. */
  hasLearningPlan?: boolean;
  /** True when this person has a sign-in account at all. Creating somebody
   *  provisions one without emailing them, so false here means provisioning
   *  never happened — the record predates that change, or it failed. */
  hasAccount?: boolean;
  /** True once they have set a password and can sign in. An invitation no
   *  longer applies at that point; a password reset does. */
  hasSignedIn?: boolean;
  programmeStatus?: ProgrammeStatus;
  /**
   * The programme the learner is on. Absent for staff, admin and employer rows,
   * which have no programme — the directory lists all four kinds in one table.
   */
  programme?: string;
  /**
   * The cohort within that programme. Absent for staff, admin and employer
   * rows, same as `programme`.
   */
  cohort?: string;
  /**
   * Who owns this learner's case. Absent for staff, admin and employer rows.
   * The Case owner filter builds its options from the values present across
   * the loaded rows, so it always offers the owners who really exist.
   */
  caseOwner?: string;
  notesCount?: number;
  hasTasks?: boolean;
  reference?: string;
  /**
   * Which table the row came from. The single enrolment section lists
   * apprenticeship learners, commercial learners, staff/admin accounts and
   * employer contacts, and their ids live in separate tables — so row actions
   * must be routed by source, not id alone.
   */
  source?: 'apprenticeship' | 'commercial' | 'staff' | 'employer';
  /**
   * Present on create, where an invitation is always issued. The record is
   * saved whether or not the account and email got as far as existing, so the
   * invitation's outcome is reported separately — see backend/login/services.py.
   */
  invitation?: InvitationOutcome;
  /**
   * Present on create when a first session was requested. Reported separately
   * from the record for the same reason the invitation is: the learner is saved
   * whether or not the booking and its Teams meeting got as far as existing.
   */
  firstSession?: FirstSessionOutcome;
}

/** What became of the first session booked while the learner was created. */
export interface FirstSessionOutcome {
  /** True only when the booking itself was reserved. */
  booked: boolean;
  /** 'aptem' when the learner was skipped — they already have a start date. */
  skipped?: string;
  /**
   * The booking exists and is recoverable, but Microsoft has not confirmed the
   * meeting, so nobody has been invited to it yet. Not the same as a failure,
   * and must not be reported as a confirmed meeting.
   */
  warning?: string;
  /** Why nothing was booked, when nothing was. */
  error?: string;
  scheduledDate?: string;
  scheduledTime?: string;
}

/** Result of an invitation attempt attached to a just-created record. */
export interface InvitationOutcome {
  /** An invitation row was written — the link exists and can be re-sent. */
  invited: boolean;
  /** The email reached the mail transport (false when Azure is unconfigured). */
  emailSent: boolean;
  accountCreated: boolean;
  /**
   * The account was provisioned but deliberately not emailed — the normal
   * outcome of creating somebody. The invitation is sent from Accounts in the
   * Super Admin workspace once the record has been checked, so this is not a
   * failure and must not be reported as one.
   */
  awaitingInvitation?: boolean;
  /** Why the invitation did not go out, when it did not. */
  error: string | null;
  expiresAt: string | null;
  /**
   * The caller was not allowed to issue this invitation — e.g. not signed in,
   * or a staff member trying to create an administrator. No account was
   * created. Distinct from a mail failure: the record still saved, but nobody
   * will ever be able to sign in as them until an authorised user re-invites.
   */
  forbidden?: boolean;
}

export interface UsersFilter {
  userName?: string;
  groups?: string[];
  email?: string;
  statuses?: string[]; // FullUser, Invited, Prospect...
  type?: UserType | 'all';
  programme?: string;
  /**
   * A single cohort, not a list: the curriculum group lookup takes one
   * programme + one cohort, so a multi-select here would have nothing to
   * narrow the group options with.
   */
  cohort?: string;
  programmeStatus?: string;
  niNumber?: string;
  caseOwner?: string | 'any';
  referenceNumber?: string;
  /** '' is unset. Learners only: staff and employers have no attendance mode. */
  attendanceMode?: '' | 'live' | 'lazy';
  page: number; // 1-based
  pageSize: number;
}

// ---- Screen B: Enrolment Details Board ----
export interface FsAssessmentRecord {
  id: string;
  name: string;
  level: string;
  date: string;
  result?: string;
}
export interface FsBlock {
  assessments: FsAssessmentRecord[];
  exempt: boolean;
  evidence: DocRow[];
}

export interface ManagedJob {
  id: string;
  employer: string;
  title: string;
  categories: string;
  availableFrom: string;
  availableTo: string;
  hoursPlanned: string; // "12:30"
  hoursLogged: string; // "00:00"
  status: string;
  date: string;
  comments: string;
  currentStep?: string;
  canUnverify?: boolean;
}

export interface TrackerRow {
  id: string;
  type: string;
  status: string;
  programme: string;
  description: string;
  documents: string;
}
export interface MilestoneRow {
  id: string;
  programme: string;
  description: string;
  date: string;
  empWksLeft: string;
  alwWksLeft: string;
  status: string;
  claimed: string;
}
export interface NoteRow {
  id: string;
  text: string;
  administrator: string;
  dateTime: string;
}
export interface CourseProgressRow {
  id: string;
  isLocked: boolean;
  courseName: string;
  progress: string; // "3 / 60% completed"
  status: string;
}
export interface ContactRow {
  id: string;
  name: string;
  type: string;
  phone: string;
  email: string;
  role: string;
  notes: string;
}
export interface ActivityRow {
  id: string;
  date: string;
  timeAndStatus: string;
  event: string;
}
export interface DocRow {
  id: string;
  uploaded: string;
  description: string;
  fileName?: string;
  url?: string;
}
export interface ReviewRow {
  id: string;
  label: string; // "Review - Progress Review 02/12/2025 (0)"
  action?: 'create' | 'needs-signing';
}
export interface ReviewGroup {
  programme: string;
  reviews: ReviewRow[];
}
export interface CompetencyRow {
  id: string;
  name: string;
  version: string;
}
export interface AuditChange {
  property: string;
  value: string;
}
export interface AuditEntry {
  id: string;
  date: string;
  admin: string;
  action: string;
  changes: AuditChange[];
}
export interface SubProgrammeRow {
  name: string;
  startDate: string;
  endDate: string;
}
export interface AimRow {
  aimRef: string;
  qualification: string;
  startDate: string;
  endDate: string;
  exempt: boolean;
}

export interface EnrolmentBoard {
  reviewHandover?: import('@/api/reviewHandover').ReviewHandover;
  user: {
    id: string;
    name: string;
    reference: string;
    owner: string;
    /** Resolved from Employer_id where set, else the learner's own text. */
    employer?: string;
    employerId?: number | null;
  };
  contact: {
    email: string;
    phone: string;
    dob: string;
    groupMembership: string;
    signatureUrl?: string;
    hasMandate: boolean;
    /** Home address on the learner's record, as one line ('' when none). */
    address?: string;
    /** The learner's reusable signature (PNG data URL), saved at first sign-in. */
    savedSignature?: string;
    /** "YYYY-MM-DD" the reusable signature was saved. */
    savedSignatureDate?: string;
  };
  activity: {
    aptemUsage: string;
    daysTillNextReporting: number;
    lastLoggedIn?: string;
    logins: number;
    tasksAddedByUser: number;
    uncompletedTasks: number;
    adviceItemsAccessed: number;
    adviceLastAccessed?: string;
    actionPlans: string;
  };
  programme: {
    type: string;
    name: string;
    cohort: string;
    status: ProgrammeStatus;
    learnerStartDate?: string;
    learnerEndDate?: string;
    startDate: string;
    endDate: string;
    enrolledAt: string;
    enrolledBy: string;
    onboardingStatus: string;
    onboardingCompletedAt?: string;
  };
  subProgrammes: SubProgrammeRow[];
  aims: AimRow[];
  previousProgrammes: string[];
  functionalSkills: { english: FsBlock; maths: FsBlock; ict: FsBlock };
  managedJobs: ManagedJob[];
  tracker: TrackerRow[];
  milestones: MilestoneRow[];
  notes: NoteRow[];
  courseProgress: CourseProgressRow[];
  contacts: ContactRow[];
  activities: ActivityRow[];
  complianceDocuments: DocRow[];
  reviewDocuments: ReviewGroup[];
  documents: DocRow[];
  competencies: CompetencyRow[];
  subscription: { startDate: string; endDate: string; status: string };
  auditTrail: AuditEntry[];
  trainingPlan: TrainingPlan;
}

// ---- Wizard ----
export interface PersonalDetails {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  address: string;
  dob: string;
  age?: number;
  sex: string;
  /** PNG data URL — drawn or uploaded via SignaturePad. */
  signature?: string;
  /** ISO date the signature was captured. */
  signatureDate?: string;
}

/**
 * The ILR step (slug 'ilr-details'): what the Individualised Learner Record asks that
 * the learner record does not already hold. Name, DOB, current address, phone
 * and email are shown read-only from the record instead. Stored in its own
 * table (enrolment."Wizard_Ilr_Learner_Details"), separate from the Extended ILR.
 * Dropdown answers are the DfE ILR option labels (see ilrLearnerDetailsOptions).
 */
export interface IlrLearnerDetails {
  yearsAtAddress: number | null;
  sinceBirth: boolean | null;
  postcodePriorToEnrolment: string;
  niNumber: string;
  /** Ticked when the learner has no NI number and has applied for one. */
  niApplied: boolean | null;
  legalSex: string;
  pronouns: string;
  ethnicity: string;
  longTermDisability: boolean | null;
  highestQualification: string;
  employmentStatus: string;
  /** In paid employment only. ISO date. */
  employmentStartDate?: string;
  /** In paid employment only. */
  jobTitle?: string;
  /** In paid employment only. */
  selfEmployed?: boolean | null;
  fullTimeEducation: boolean | null;
  /** In full-time education or training only. ISO date. */
  expectedLeavingDate?: string;
  /** Not in paid employment only. */
  lengthOfUnemployment?: string;
  /** Not in paid employment only. */
  volunteers?: boolean | null;
  stateBenefits: string;
  /** When a benefit is claimed: in the learner's own right or a joint claim. */
  benefitClaimBasis?: string;
  /** PNG data URL — the "information on this form is correct" certification. */
  signature?: string | null;
  signatureDate?: string;
}

export type CompetenceKind = 'Knowledge' | 'Skill' | 'Behaviour';
/**
 * Self-assessment scale for the Skills Radar questionnaire (9 points, scored 9
 * down to 1 — see COMPETENCE_LEVELS).
 *
 * The five original RAG values are kept in the union so assessments saved before
 * the scale was widened still load and display instead of coming back as null.
 */
export type RagLevel =
  | 'champion'
  | 'mastery'
  | 'expert'
  | 'proficient'
  | 'consistently'
  | 'frequently'
  | 'occasionally'
  | 'rarely'
  | 'never'
  // Legacy values (pre-8-point scale).
  | 'always'
  | 'often'
  | 'sometimes';

export interface Ksb {
  id: string; // e.g. "PCP-K1"
  theme: string;
  kind: CompetenceKind;
  codes: string[]; // ["K1"] or ["B3","B4"]
  title: string;
}
export interface KsbAssessment {
  ksbId: string;
  level: RagLevel | null;
  evidenceFiles: string[];
  actionPlan: {
    text: string;
    action?: string;
    goal?: string;
    dueDate?: string;
  } | null;
  note?: string;
}
export interface SkillsRadarState {
  standardId: string;
  assessments: Record<string, KsbAssessment>;
  /**
   * The KSBs of the learner's programme profile — the questions the step asks —
   * read when the wizard opens. Completeness is measured against these alone:
   * saved answers can hold rows for KSBs no longer on the programme (a changed
   * programme or a re-authored profile), which the learner is never shown and
   * so could never rate. Never saved; undefined until the profile has loaded.
   */
  programmeKsbIds?: string[];
}

/**
 * Extended ILR — the learner-facing declaration form.
 *
 * Mirrors the printed "Extended ILR" pages of the OnBoarding-ILR document:
 * contact preferences, next of kin, eligibility, employer details, other
 * training, personal circumstances, programme understanding, additional
 * information, media consent and the learning declarations. The statutory
 * Learner Details capture (name / address / ethnicity / prior attainment)
 * is not part of this step.
 */
export interface IlrForm {
  contact: { byPost: boolean | null; byPhone: boolean | null; byEmail: boolean | null };
  nextOfKin: {
    fullName: string;
    relationship: string;
    email: string;
    phone: string;
    sameAddressAsLearner: boolean | null;
    /** Only asked when the address is not the learner's; optional so older saved answers still load. */
    postcode?: string;
    address?: string;
  };
  eligibility: {
    employedInEngland: boolean | null;
    countryOfResidence: string;
    ukEeaNational: boolean | null;
    nationality: string;
    residentPrev3Years: boolean | null;
    yearsInUk?: number;
    requiresWorkPermit: boolean | null;
    evidenceDescription: string;
    evidenceFiles: string[];
  };
  employer: {
    organisationName: string;
    postcode: string;
    address: string;
    city: string;
    lineManagerName: string;
    lineManagerEmail: string;
    lineManagerPhone: string;
  };
  otherTraining: { attended12m: boolean | null; completedWhen: string };
  circumstances: { caringResponsibilities: string; other: string; careLeaver: boolean | null };
  understanding: { programmeUnderstanding: string; careerProgression: string };
  /**
   * The "Additional Information" section. It replaced the age questions, media
   * consent, the declarations below and both signature blocks on the form; those
   * keys stay in this type so answers saved before the change still load, but
   * they are no longer asked or printed. Of `declarations`, only
   * over50PercentEngland, wageRateBand, knownByOtherName and plrAccessAware are
   * still asked — in this section.
   */
  additionalInformation: {
    jobRoleRelevance: string;
    /** 'Yes' | 'No' | '' — residence in the UK for 3 years not mainly for full-time education. */
    residenceNotForFullTimeEducation: string;
    /** 'Yes' | 'No' | '' — holds an EHCP issued by a local authority. */
    ehcp: string;
    /** Only asked when knownByOtherName is true; cleared when it is not. */
    otherNames: string;
  };
  additional: { aged16to18: boolean | null; aged19to24: boolean | null };
  media: { consent: boolean | null };
  declarations: {
    plrShared: boolean | null;
    dfeContact: boolean | null;
    epaoDetails: boolean | null;
    kbcHoldsCerts: boolean | null;
    infoAccurate: boolean | null;
    over50PercentEngland: boolean | null;
    wageRateBand: string;
    knownByOtherName: boolean | null;
    plrAccessAware: boolean | null;
  };
  /** Learning declaration signature block (learner). */
  learnerSignature: { firstNames: string; surname: string; signatureUrl?: string; date: string };
  /** Provider / sub-contractor declaration signature block. */
  providerSignature: { printName: string; signatureUrl?: string; date: string };
}

export interface PlrRecord {
  id: string;
  placeOfStudy: string;
  qualificationType: string;
  subject: string;
  level?: string;
  startDate?: string;
  endDate?: string;
  awardDate?: string;
  credits: number;
  grade: string;
  recordType: 'Imported' | 'Manual' | string;
}
export interface PlrState {
  /** No longer asked on the step; kept so a saved ULN is carried through unchanged. */
  uln: string;
  records: PlrRecord[];
}

export interface CvJobForm {
  /** Legacy: a file name only. Uploads are now stored in Azure (see cvJobDocuments). */
  cvFile?: string;
  /** Previous experience, or "N/A" when a CV was uploaded. */
  experienceText?: string;
  /** Legacy: no longer asked; kept so saved answers still load. */
  pmQualifications: string;
  functionalSkillsEnrol?: string;
  highestQualification?: string;
  highestQualificationField?: string;
  /** "Do you have any <programme field> qualifications?" */
  hasFieldQualification?: boolean | null;
  /** Only asked when hasFieldQualification is true; cleared when it is not. */
  highestFieldQualification?: string;
  gcseEnglish?: boolean | null;
  gcseMaths?: boolean | null;
}

export interface PolicyDoc {
  id: string;
  label: string;
  url: string;
  requiresAck: boolean;
}
export interface PoliciesState {
  acknowledged: Record<string, boolean>;
}

export interface WizardDraft {
  personalDetails: PersonalDetails;
  ilrDetails: IlrLearnerDetails;
  skillsRadar: SkillsRadarState;
  ilr: IlrForm;
  plr: PlrState;
  cvJob: CvJobForm;
  policies: PoliciesState;
  /**
   * Answers to the fields added in the wizard builder, by field key. Optional:
   * drafts saved before the builder existed have none. Projected server-side
   * into each field's own column (see enrolment_api/wizard_layout.py).
   */
  custom?: Record<string, CustomAnswer>;
  /**
   * The Inclusion step's status, read from the Safeguarding database each time
   * the wizard opens (see enrolment_api/wizard_inclusion.py). Never saved with
   * the draft: absent until the check comes back.
   */
  inclusion?: InclusionState;
}

/** Whether the learner has answered the inclusion assessment on the Safeguarding site. */
export interface InclusionState {
  /** False for commercial learners, who do not take the step. */
  applicable: boolean;
  completed: boolean;
  respondedAt: string | null;
}

export interface WizardStepDef {
  slug: string;
  label: string;
}

export const WIZARD_STEPS: WizardStepDef[] = [
  // Slugs are the page addresses and never change; only the order and the
  // labels follow the onboarding flow.
  { slug: 'introduction', label: 'Welcome' },
  { slug: 'before-you-begin', label: 'Before You Begin' },
  { slug: 'personal-details', label: 'Personal Details' },
  { slug: 'ilr-details', label: 'ILR' },
  { slug: 'ilr', label: 'Extended ILR' },
  { slug: 'cv-job', label: 'CV/Job Description' },
  { slug: 'plr', label: 'Personal Learning Record' },
  { slug: 'skills-radar', label: 'Skills Radar' },
  { slug: 'inclusion', label: 'Inclusion' },
  { slug: 'policies', label: 'Policies' },
  { slug: 'next-steps', label: 'What Happens Now?' },
];
