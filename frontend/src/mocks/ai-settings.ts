// ============================================================
// KBC LearningOS — AI Settings & Configuration
// ============================================================

export interface AiFeatureToggle {
  slug: string;
  label: string;
  description: string;
  category: 'learner-support' | 'staff-support' | 'reporting' | 'quiz-content';
  enabled: boolean;
  requiresHumanApproval: boolean;
  /** What this AI feature is allowed to do */
  allowedActions: string[];
  /** What this AI feature must NEVER do */
  forbiddenActions: string[];
}

export interface AiSettings {
  // Master switch
  aiEnabled: boolean;

  // Learner support features
  proofreadingEnabled: boolean;
  reflectionQualityCheckEnabled: boolean;
  ksbSuggestionsEnabled: boolean;
  evidenceCheckerEnabled: boolean;
  revisionSuggestionsEnabled: boolean;

  // Staff support features
  markingSuggestionsEnabled: boolean;
  otjhRiskDetectionEnabled: boolean;
  coachingSummariesEnabled: boolean;
  progressReviewDraftsEnabled: boolean;
  coachingAgendaSuggestionsEnabled: boolean;

  // Reporting features
  reportSummariesEnabled: boolean;
  employerSummaryDraftsEnabled: boolean;
  ofstedEvidenceSummariesEnabled: boolean;
  learnerRiskPatternSummariesEnabled: boolean;
  sarQipEvidenceSummariesEnabled: boolean;

  // Quiz & content features
  quizGenerationEnabled: boolean;
  xmlQuizAssistantEnabled: boolean;

  // Governance (not toggles — these are hard requirements)
  requireHumanApproval: boolean; // MUST always be true per spec
  auditTrailEnabled: boolean; // MUST always be true
  outputHistoryVisibleToTutorAdmin: boolean;

  // Never-allowed actions — hardcoded restrictions enforced in code
  // These are not toggles; they are absolute rules:
  // - AI must never validate KSBs automatically
  // - AI must never accept OTJH automatically
  // - AI must never approve evidence automatically
  // - AI must never approve eligibility
  // - AI must never approve RPL
  // - AI must never approve QA
  // - AI must never approve funding risk decisions
  // - AI must never approve learner activation
  // - AI must never approve gateway readiness
  // - AI must never approve compliance documents
  // - AI must never replace tutor professional judgement
  // - AI must never replace coach professional judgement
  // - AI must never replace employer confirmation
  // - AI must never replace compliance review
  // - AI must never replace QA review
  // - AI must never invent evidence
  // - AI must never invent workplace application
  // - AI must never invent employer benefit
  // - AI must never invent learner experience
}

export const AI_FEATURE_TOGGLES: AiFeatureToggle[] = [
  {
    slug: 'proofreading',
    label: 'AI Proofreading',
    description: 'Proofread learner reflections and written work for spelling, grammar, and clarity improvements.',
    category: 'learner-support',
    enabled: true,
    requiresHumanApproval: true,
    allowedActions: ['Proofread learner reflection text', 'Suggest grammar improvements', 'Suggest clearer phrasing'],
    forbiddenActions: ['Change meaning of learner content', 'Add content not written by learner', 'Override learner voice'],
  },
  {
    slug: 'reflection-quality',
    label: 'AI Reflection Quality Check',
    description: 'Check learner reflections for depth, completeness, and quality of self-assessment.',
    category: 'learner-support',
    enabled: true,
    requiresHumanApproval: true,
    allowedActions: ['Identify shallow reflections', 'Flag missing detail', 'Suggest areas to expand'],
    forbiddenActions: ['Write reflections for learner', 'Score or grade reflections', 'Override tutor assessment'],
  },
  {
    slug: 'ksb-suggestions',
    label: 'AI KSB Suggestions',
    description: 'Suggest KSB alignment for evidence items based on content analysis.',
    category: 'learner-support',
    enabled: true,
    requiresHumanApproval: true,
    allowedActions: ['Suggest KSB mapping based on evidence content', 'Identify potentially missed KSBs', 'Flag evidence that might not match claimed KSBs'],
    forbiddenActions: ['Automatically assign KSBs', 'Validate KSBs without human review', 'Override tutor KSB assessment'],
  },
  {
    slug: 'evidence-checker',
    label: 'AI Evidence Checker',
    description: 'Identify weak evidence submissions and missing workplace context.',
    category: 'learner-support',
    enabled: true,
    requiresHumanApproval: true,
    allowedActions: ['Flag weak evidence', 'Identify missing workplace application', 'Identify missing employer benefit'],
    forbiddenActions: ['Automatically reject evidence', 'Approve evidence', 'Invent missing context'],
  },
  {
    slug: 'revision-suggestions',
    label: 'AI Revision Suggestions',
    description: 'Suggest revision materials and study focus areas based on quiz performance and KSB gaps.',
    category: 'learner-support',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Suggest topics for revision', 'Identify weak areas from quiz results', 'Recommend study sequence'],
    forbiddenActions: ['Create mandatory study plans', 'Override tutor-directed learning', 'Access learner data beyond quiz results'],
  },
  {
    slug: 'marking-suggestions',
    label: 'AI Marking Suggestions',
    description: 'Draft tutor feedback, suggest areas for improvement, and prepare assessment comments.',
    category: 'staff-support',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Draft feedback comments', 'Suggest improvement areas', 'Pre-fill assessment templates'],
    forbiddenActions: ['Auto-submit grades', 'Replace tutor judgement', 'Finalise marks without human review'],
  },
  {
    slug: 'otjh-risk',
    label: 'AI OTJH Risk Detection',
    description: 'Detect potential OTJH shortfalls and flag learners at risk of not meeting minimum hours.',
    category: 'staff-support',
    enabled: true,
    requiresHumanApproval: true,
    allowedActions: ['Flag learners below OTJH targets', 'Predict future shortfalls', 'Suggest intervention timing'],
    forbiddenActions: ['Automatically adjust OTJH records', 'Approve OTJH claims', 'Replace coach monitoring'],
  },
  {
    slug: 'coaching-summaries',
    label: 'AI Coaching Summaries',
    description: 'Summarise coaching meeting notes and suggest agenda items for upcoming sessions.',
    category: 'staff-support',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Summarise coaching notes', 'Draft meeting minutes', 'Suggest agenda items based on learner context'],
    forbiddenActions: ['Write official coaching records', 'Replace coach documentation', 'Make decisions about learner progression'],
  },
  {
    slug: 'progress-review-drafts',
    label: 'AI Progress Review Drafts',
    description: 'Draft progress review discussion points and summarise period achievements.',
    category: 'staff-support',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Draft review discussion points', 'Summarise period achievements', 'Highlight KSB progression'],
    forbiddenActions: ['Finalise review documents', 'Sign progress reviews', 'Replace coach/tutor review judgement'],
  },
  {
    slug: 'coaching-agenda',
    label: 'AI Coaching Agenda Suggestions',
    description: 'Suggest coaching agenda items based on learner risk patterns and KSB progress.',
    category: 'staff-support',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Suggest agenda items', 'Prioritise coaching topics', 'Link to learner risk data'],
    forbiddenActions: ['Set mandatory coaching agendas', 'Replace coach professional planning', 'Exclude coach input'],
  },
  {
    slug: 'report-summaries',
    label: 'AI Report Summaries',
    description: 'Generate summary text for reports from stored data without AI commentary.',
    category: 'reporting',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Summarise data trends', 'Generate report narrative', 'Compile statistical summaries'],
    forbiddenActions: ['Add AI commentary to reports', 'Make recommendations without human review', 'Replace human-authored content'],
  },
  {
    slug: 'employer-summaries',
    label: 'AI Employer Summary Drafts',
    description: 'Prepare employer-facing summary drafts of learner progress and achievements.',
    category: 'reporting',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Draft employer progress summaries', 'Highlight learner achievements', 'Compile attendance and OTJH stats'],
    forbiddenActions: ['Send summaries without human approval', 'Include sensitive learner data', 'Replace employer communication'],
  },
  {
    slug: 'ofsted-summaries',
    label: 'AI Ofsted Evidence Summaries',
    description: 'Prepare Ofsted inspection evidence summaries from verified records.',
    category: 'reporting',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Compile evidence summaries', 'Organise Ofsted-ready data packs', 'Cross-reference evidence with standards'],
    forbiddenActions: ['Create evidence', 'Modify evidence records', 'Submit Ofsted data without QA sign-off'],
  },
  {
    slug: 'risk-patterns',
    label: 'AI Learner Risk Pattern Summaries',
    description: 'Summarise learner risk patterns across attendance, OTJH, engagement and KSB data.',
    category: 'reporting',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Identify risk clusters', 'Summarise risk trends', 'Flag learners for intervention'],
    forbiddenActions: ['Make intervention decisions', 'Override human risk assessment', 'Flag without supporting data'],
  },
  {
    slug: 'sar-qip',
    label: 'AI SAR/QIP Evidence Insights',
    description: 'Produce evidence insights for Self-Assessment Reports and Quality Improvement Plans.',
    category: 'reporting',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Compile SAR evidence', 'Cross-reference QIP actions', 'Highlight quality trends'],
    forbiddenActions: ['Write final SAR', 'Determine quality ratings', 'Replace human quality assessment'],
  },
  {
    slug: 'quiz-generation',
    label: 'AI Quiz Generation',
    description: 'Generate draft quiz questions based on curriculum content and KSB frameworks.',
    category: 'quiz-content',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Generate draft questions', 'Suggest distractor answers', 'Map questions to KSBs'],
    forbiddenActions: ['Publish quizzes without review', 'Replace tutor-authored assessment', 'Auto-grade without validation'],
  },
  {
    slug: 'xml-quiz',
    label: 'AI XML Quiz Assistant',
    description: 'Improve XML quiz content structure, formatting, and accessibility compliance.',
    category: 'quiz-content',
    enabled: false,
    requiresHumanApproval: true,
    allowedActions: ['Validate XML structure', 'Suggest formatting improvements', 'Check accessibility compliance'],
    forbiddenActions: ['Modify quiz content meaning', 'Change assessment difficulty', 'Publish XML without review'],
  },
];

// ============================================================
// Default AI settings for a tenant
// ============================================================

export const defaultAiSettings: AiSettings = {
  aiEnabled: true,
  proofreadingEnabled: true,
  reflectionQualityCheckEnabled: true,
  ksbSuggestionsEnabled: true,
  evidenceCheckerEnabled: true,
  revisionSuggestionsEnabled: false,
  markingSuggestionsEnabled: false,
  otjhRiskDetectionEnabled: true,
  coachingSummariesEnabled: false,
  progressReviewDraftsEnabled: false,
  coachingAgendaSuggestionsEnabled: false,
  reportSummariesEnabled: false,
  employerSummaryDraftsEnabled: false,
  ofstedEvidenceSummariesEnabled: false,
  learnerRiskPatternSummariesEnabled: false,
  sarQipEvidenceSummariesEnabled: false,
  quizGenerationEnabled: false,
  xmlQuizAssistantEnabled: false,
  requireHumanApproval: true,
  auditTrailEnabled: true,
  outputHistoryVisibleToTutorAdmin: true,
};

// ============================================================
// Never-allowed AI actions — hardcoded absolute rules
// ============================================================

export const AI_NEVER_ALLOWED_ACTIONS: string[] = [
  'Validate KSBs automatically',
  'Accept OTJH automatically',
  'Approve evidence automatically',
  'Approve eligibility',
  'Approve RPL',
  'Approve QA',
  'Approve funding risk decisions',
  'Approve learner activation',
  'Approve gateway readiness',
  'Approve compliance documents',
  'Replace tutor professional judgement',
  'Replace coach professional judgement',
  'Replace employer confirmation',
  'Replace compliance review',
  'Replace QA review',
  'Invent evidence',
  'Invent workplace application',
  'Invent employer benefit',
  'Invent learner experience',
];

// ============================================================
// AI fallback — what happens when AI is off or fails
// ============================================================

export const AI_FALLBACK_RULES = {
  whenDisabled: 'All workflows revert to fully manual operation. No AI suggestions appear. All marking, validation, and feedback is human-only.',
  whenFails: 'System automatically falls back to manual workflow. Affected AI feature is temporarily disabled for the affected record. Staff are notified. No data is lost.',
  gracePeriod: 'If AI becomes unavailable, all in-progress AI suggestions are preserved for 24 hours for human review before being discarded.',
};