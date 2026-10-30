export interface TenantConfig {
  branding: {
    logoUrl: string;
    primaryColor: string;
    accentColor: string;
    terminology: Record<string, string>;
  };
  programmes: {
    apprenticeshipStandards: string[];
    ksbFrameworks: string[];
  };
  onboarding: {
    requireEmployerContract: boolean;
    requireEligibilityCheck: boolean;
    requireSkillsScan: boolean;
    requireCompliancePack: boolean;
    requireDigitalSignatures: boolean;
    requireDASCheck: boolean;
    requireILRReadiness: boolean;
    requireQAReview: boolean;
  };
  attendance: {
    minimumAttendancePercent: number;
    autoFlagThreshold: number;
    catchupWindowDays: number;
  };
  otjh: {
    minimumWeeklyHours: number;
    monthlyTarget: number;
    validationRequired: boolean;
    autoReminderDays: number;
  };
  ksb: {
    frameworkType: string;
    validationRequired: boolean;
    requireWorkplaceEvidence: boolean;
  };
  monthlyCycle: {
    checkpointFrequency: 'monthly' | 'bi-monthly' | 'quarterly';
    assignmentDueDay: number;
    coachingFrequency: 'weekly' | 'bi-weekly' | 'monthly';
    progressReviewFrequency: 'monthly' | 'bi-monthly' | 'quarterly';
  };
  notifications: {
    emailEnabled: boolean;
    smsEnabled: boolean;
    whatsappEnabled: boolean;
    employerNotifications: boolean;
    coachFollowUpDays: number;
  };
  ai: {
    enabled: boolean;
    proofreadingEnabled: boolean;
    reflectionQualityCheckEnabled: boolean;
    ksbSuggestionsEnabled: boolean;
    evidenceCheckerEnabled: boolean;
    revisionSuggestionsEnabled: boolean;
    markingSuggestionsEnabled: boolean;
    otjhRiskDetectionEnabled: boolean;
    coachingSummariesEnabled: boolean;
    progressReviewDraftsEnabled: boolean;
    coachingAgendaSuggestionsEnabled: boolean;
    reportSummariesEnabled: boolean;
    employerSummaryDraftsEnabled: boolean;
    ofstedEvidenceSummariesEnabled: boolean;
    learnerRiskPatternSummariesEnabled: boolean;
    sarQipEvidenceSummariesEnabled: boolean;
    quizGenerationEnabled: boolean;
    xmlQuizAssistantEnabled: boolean;
    requireHumanApproval: boolean;
    auditTrailEnabled: boolean;
    outputHistoryVisibleToTutorAdmin: boolean;
  };
  compliance: {
    dataRetentionMonths: number;
    auditLogEnabled: boolean;
    ofstedReadyMode: boolean;
  };
  rewards: {
    enabled: boolean;
    pointsPerEvidence: number;
    pointsPerQuiz: number;
    clubThresholds: { bronze: number; silver: number; gold: number };
  };
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  type: 'college' | 'provider' | 'employer-provider' | 'training-organisation' | 'corporate-academy' | 'multi-brand-group' | 'international-partner';
  config: TenantConfig;
  organisations: Organisation[];
  createdAt: string;
  status: 'active' | 'trial' | 'suspended';
}

export interface Organisation {
  id: string;
  tenantId: string;
  name: string;
  type: 'provider' | 'employer' | 'department';
  parentId: string | null;
}

export const defaultTenantConfig: TenantConfig = {
  branding: {
    logoUrl: '',
    primaryColor: '#6C2BD9',
    accentColor: '#D4A017',
    terminology: {
      learner: 'Apprentice Learner',
      coach: 'Progress Coach',
      tutor: 'Curriculum Tutor',
      employer: 'Employer',
      programme: 'Apprenticeship Programme',
      evidence: 'Learning Evidence',
      otjh: 'Off-the-Job Training Hours',
      ksb: 'Knowledge, Skills & Behaviours',
      progressReview: 'Progress Review',
      gateway: 'Gateway Readiness',
    },
  },
  programmes: {
    apprenticeshipStandards: [
      'Business Administrator L3',
      'Data Analyst L4',
      'Digital Marketer L3',
      'Software Developer L4',
      'Accountancy L3',
      'Customer Service L2',
      'Team Leader L3',
      'Operations Manager L5',
    ],
    ksbFrameworks: ['Standard KSB Framework v2.1', 'IfATE Standard Mapping'],
  },
  onboarding: {
    requireEmployerContract: true,
    requireEligibilityCheck: true,
    requireSkillsScan: true,
    requireCompliancePack: true,
    requireDigitalSignatures: true,
    requireDASCheck: true,
    requireILRReadiness: true,
    requireQAReview: true,
  },
  attendance: { minimumAttendancePercent: 85, autoFlagThreshold: 80, catchupWindowDays: 7 },
  otjh: { minimumWeeklyHours: 6, monthlyTarget: 40, validationRequired: true, autoReminderDays: 3 },
  ksb: { frameworkType: 'Standard', validationRequired: true, requireWorkplaceEvidence: true },
  monthlyCycle: { checkpointFrequency: 'monthly', assignmentDueDay: 28, coachingFrequency: 'monthly', progressReviewFrequency: 'monthly' },
  notifications: { emailEnabled: true, smsEnabled: false, whatsappEnabled: false, employerNotifications: true, coachFollowUpDays: 3 },
  ai: {
    enabled: true,
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
  },
  compliance: { dataRetentionMonths: 84, auditLogEnabled: true, ofstedReadyMode: true },
  rewards: { enabled: true, pointsPerEvidence: 10, pointsPerQuiz: 25, clubThresholds: { bronze: 200, silver: 500, gold: 800 } },
};

export const kbcTenant: Tenant = {
  id: 't_kbc_001', name: 'Kent Business College', slug: 'kbc', type: 'college', status: 'active',
  config: defaultTenantConfig,
  organisations: [
    { id: 'org_kbc_main', tenantId: 't_kbc_001', name: 'KBC Main Campus', type: 'provider', parentId: null },
    { id: 'org_dept_biz', tenantId: 't_kbc_001', name: 'Business & Professional Studies', type: 'department', parentId: 'org_kbc_main' },
    { id: 'org_dept_digital', tenantId: 't_kbc_001', name: 'Digital & IT', type: 'department', parentId: 'org_kbc_main' },
    { id: 'org_dept_health', tenantId: 't_kbc_001', name: 'Health & Social Care', type: 'department', parentId: 'org_kbc_main' },
    { id: 'org_dept_eng', tenantId: 't_kbc_001', name: 'Engineering', type: 'department', parentId: 'org_kbc_main' },
    { id: 'org_emp_kcc', tenantId: 't_kbc_001', name: 'Kent County Council', type: 'employer', parentId: null },
    { id: 'org_emp_medway', tenantId: 't_kbc_001', name: 'Medway NHS Trust', type: 'employer', parentId: null },
    { id: 'org_emp_canterbury', tenantId: 't_kbc_001', name: 'Canterbury Creative Ltd', type: 'employer', parentId: null },
    { id: 'org_emp_techkent', tenantId: 't_kbc_001', name: 'Tech Kent Ltd', type: 'employer', parentId: null },
    { id: 'org_emp_ashford', tenantId: 't_kbc_001', name: 'Ashford Accounting LLP', type: 'employer', parentId: null },
  ],
  createdAt: '2024-09-01',
};

export const demoProviderTenant: Tenant = {
  id: 't_nat_002', name: 'National Apprenticeship Training Co.', slug: 'natco', type: 'training-organisation', status: 'trial',
  config: { ...defaultTenantConfig },
  organisations: [
    { id: 'org_nat_main', tenantId: 't_nat_002', name: 'NATC HQ', type: 'provider', parentId: null },
    { id: 'org_emp_bt', tenantId: 't_nat_002', name: 'British Telecom', type: 'employer', parentId: null },
    { id: 'org_emp_tesco', tenantId: 't_nat_002', name: 'Tesco PLC', type: 'employer', parentId: null },
  ],
  createdAt: '2025-03-15',
};

export const tenants: Tenant[] = [kbcTenant, demoProviderTenant];