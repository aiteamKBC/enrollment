export interface Role {
  id: string;
  name: string;
  slug: string;
  description: string;
  category: 'learner' | 'delivery' | 'employer' | 'compliance' | 'management' | 'admin';
  defaultPermissions: string[];
  isSystem: boolean;
}

export interface Permission {
  id: string;
  name: string;
  slug: string;
  category: string;
  description: string;
}

export const allPermissions: Permission[] = [
  { id: 'perm_dash_view', name: 'View Dashboard', slug: 'dash.view', category: 'Dashboard', description: 'Access to the main dashboard view' },
  { id: 'perm_learning_view', name: 'View Learning', slug: 'learning.view', category: 'Learning', description: 'View learning content and modules' },
  { id: 'perm_learning_manage', name: 'Manage Learning', slug: 'learning.manage', category: 'Learning', description: 'Create and manage learning content' },
  { id: 'perm_evidence_create', name: 'Create Evidence', slug: 'evidence.create', category: 'Evidence', description: 'Upload and create evidence items' },
  { id: 'perm_evidence_validate', name: 'Validate Evidence', slug: 'evidence.validate', category: 'Evidence', description: 'Validate learner evidence submissions' },
  { id: 'perm_evidence_view_all', name: 'View All Evidence', slug: 'evidence.view_all', category: 'Evidence', description: 'View evidence across all learners' },
  { id: 'perm_otjh_claim', name: 'Claim OTJH', slug: 'otjh.claim', category: 'OTJH', description: 'Log off-the-job training hours' },
  { id: 'perm_otjh_validate', name: 'Validate OTJH', slug: 'otjh.validate', category: 'OTJH', description: 'Validate OTJH claims' },
  { id: 'perm_ksb_view', name: 'View KSB Progress', slug: 'ksb.view', category: 'KSB', description: 'View KSB progression data' },
  { id: 'perm_ksb_assess', name: 'Assess KSB', slug: 'ksb.assess', category: 'KSB', description: 'Assess and grade KSB competencies' },
  { id: 'perm_coaching_manage', name: 'Manage Coaching', slug: 'coaching.manage', category: 'Coaching', description: 'Schedule and manage coaching sessions' },
  { id: 'perm_reviews_create', name: 'Create Reviews', slug: 'reviews.create', category: 'Reviews', description: 'Create progress reviews' },
  { id: 'perm_reviews_sign', name: 'Sign Reviews', slug: 'reviews.sign', category: 'Reviews', description: 'Sign and approve progress reviews' },
  { id: 'perm_attendance_view', name: 'View Attendance', slug: 'attendance.view', category: 'Attendance', description: 'View attendance records' },
  { id: 'perm_attendance_manage', name: 'Manage Attendance', slug: 'attendance.manage', category: 'Attendance', description: 'Take register and manage attendance' },
  { id: 'perm_compliance_view', name: 'View Compliance', slug: 'compliance.view', category: 'Compliance', description: 'View compliance documents and status' },
  { id: 'perm_compliance_manage', name: 'Manage Compliance', slug: 'compliance.manage', category: 'Compliance', description: 'Manage compliance documents and workflows' },
  { id: 'perm_qa_review', name: 'QA Review', slug: 'qa.review', category: 'QA', description: 'Perform quality assurance reviews' },
  { id: 'perm_audit_view', name: 'View Audit Trail', slug: 'audit.view', category: 'Audit', description: 'View the audit trail' },
  { id: 'perm_audit_export', name: 'Export Audit', slug: 'audit.export', category: 'Audit', description: 'Export audit data' },
  { id: 'perm_reports_view', name: 'View Reports', slug: 'reports.view', category: 'Reports', description: 'View reports' },
  { id: 'perm_reports_create', name: 'Create Reports', slug: 'reports.create', category: 'Reports', description: 'Generate and customise reports' },
  { id: 'perm_users_view', name: 'View Users', slug: 'users.view', category: 'Users', description: 'View user records' },
  { id: 'perm_users_manage', name: 'Manage Users', slug: 'users.manage', category: 'Users', description: 'Create, edit and deactivate users' },
  { id: 'perm_roles_manage', name: 'Manage Roles', slug: 'roles.manage', category: 'Users', description: 'Manage roles and permissions' },
  { id: 'perm_programmes_view', name: 'View Programmes', slug: 'programmes.view', category: 'Programmes', description: 'View programme data' },
  { id: 'perm_programmes_manage', name: 'Manage Programmes', slug: 'programmes.manage', category: 'Programmes', description: 'Create and manage programmes' },
  { id: 'perm_curriculum_manage', name: 'Manage Curriculum', slug: 'curriculum.manage', category: 'Curriculum', description: 'Build and edit curriculum' },
  { id: 'perm_settings_view', name: 'View Settings', slug: 'settings.view', category: 'Settings', description: 'View tenant settings' },
  { id: 'perm_settings_manage', name: 'Manage Settings', slug: 'settings.manage', category: 'Settings', description: 'Modify tenant configuration' },
  { id: 'perm_finance_view', name: 'View Finance', slug: 'finance.view', category: 'Finance', description: 'View financial data' },
  { id: 'perm_finance_manage', name: 'Manage Finance', slug: 'finance.manage', category: 'Finance', description: 'Manage invoices and funding' },
  { id: 'perm_employer_view', name: 'Employer View', slug: 'employer.view', category: 'Employer', description: 'View employer-related data' },
  { id: 'perm_ai_toggle', name: 'Toggle AI Mode', slug: 'ai.toggle', category: 'AI', description: 'Switch between manual and AI-assisted modes' },
  { id: 'perm_tenant_admin', name: 'Tenant Admin', slug: 'tenant.admin', category: 'Admin', description: 'Full tenant-level administration' },
  { id: 'perm_super_admin', name: 'Super Admin', slug: 'super.admin', category: 'Admin', description: 'Cross-tenant super administration' },
];

export const allRoles: Role[] = [
  { id: 'role_learner', name: 'Apprentice Learner', slug: 'learner', description: 'Apprenticeship learner accessing learning, evidence, and coaching', category: 'learner', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_learning_view', 'perm_evidence_create', 'perm_otjh_claim', 'perm_ksb_view', 'perm_attendance_view'] },
  { id: 'role_coach', name: 'Progress Coach', slug: 'coach', description: 'Progress coach managing learner caseload and reviews', category: 'delivery', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_learning_view', 'perm_evidence_validate', 'perm_otjh_validate', 'perm_ksb_view', 'perm_ksb_assess', 'perm_coaching_manage', 'perm_reviews_create', 'perm_reviews_sign', 'perm_attendance_view', 'perm_reports_view', 'perm_users_view'] },
  { id: 'role_tutor', name: 'Curriculum Tutor', slug: 'tutor', description: 'Curriculum tutor delivering sessions and validating evidence', category: 'delivery', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_learning_view', 'perm_learning_manage', 'perm_evidence_validate', 'perm_ksb_view', 'perm_ksb_assess', 'perm_attendance_view', 'perm_attendance_manage', 'perm_curriculum_manage', 'perm_reports_view'] },
  { id: 'role_employer', name: 'Employer / Line Manager', slug: 'employer', description: 'Employer monitoring apprentice progress', category: 'employer', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_ksb_view', 'perm_reviews_sign', 'perm_employer_view', 'perm_attendance_view'] },
  { id: 'role_engagement', name: 'Engagement Manager', slug: 'engagement', description: 'Managing lead pipeline and employer accounts', category: 'management', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_users_view', 'perm_employer_view', 'perm_reports_view', 'perm_reports_create'] },
  { id: 'role_compliance', name: 'Compliance Officer', slug: 'compliance', description: 'Overseeing documents, DAS, ILR, and audit', category: 'compliance', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_compliance_view', 'perm_compliance_manage', 'perm_audit_view', 'perm_audit_export', 'perm_reports_view', 'perm_users_view'] },
  { id: 'role_qa', name: 'QA Officer', slug: 'qa', description: 'Quality assurance reviews and spot checks', category: 'compliance', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_qa_review', 'perm_compliance_view', 'perm_reports_view', 'perm_evidence_view_all', 'perm_users_view'] },
  { id: 'role_mis', name: 'MIS User', slug: 'mis', description: 'Data management and ILR exports', category: 'compliance', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_compliance_view', 'perm_reports_view', 'perm_reports_create', 'perm_users_view', 'perm_audit_view'] },
  { id: 'role_curriculum', name: 'Curriculum Developer', slug: 'curriculum', description: 'Programme and curriculum design', category: 'management', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_curriculum_manage', 'perm_programmes_view', 'perm_programmes_manage', 'perm_learning_manage'] },
  { id: 'role_leadership', name: 'Senior Leader', slug: 'leadership', description: 'Organisation-wide overview', category: 'management', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_reports_view', 'perm_reports_create', 'perm_users_view', 'perm_compliance_view', 'perm_finance_view'] },
  { id: 'role_finance', name: 'Finance User', slug: 'finance', description: 'Funding overview and financial management', category: 'management', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_finance_view', 'perm_finance_manage', 'perm_reports_view', 'perm_reports_create'] },
  { id: 'role_auditor', name: 'Auditor', slug: 'auditor', description: 'External audit evidence review', category: 'compliance', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_audit_view', 'perm_audit_export', 'perm_evidence_view_all', 'perm_reports_view'] },
  { id: 'role_programme_manager', name: 'Programme Manager', slug: 'programme-manager', description: 'Programme and cohort management', category: 'management', isSystem: true, defaultPermissions: ['perm_dash_view', 'perm_programmes_view', 'perm_programmes_manage', 'perm_curriculum_manage', 'perm_reports_view', 'perm_users_view'] },
  { id: 'role_tenant_admin', name: 'Tenant Admin', slug: 'tenant-admin', description: 'Full tenant-level administration', category: 'admin', isSystem: true, defaultPermissions: ['perm_tenant_admin', 'perm_settings_view', 'perm_settings_manage', 'perm_users_manage', 'perm_roles_manage', 'perm_programmes_manage', 'perm_curriculum_manage', 'perm_compliance_manage', 'perm_reports_create', 'perm_ai_toggle'] },
  { id: 'role_super_admin', name: 'Super Admin', slug: 'super-admin', description: 'Cross-tenant super administration', category: 'admin', isSystem: false, defaultPermissions: ['perm_super_admin', 'perm_tenant_admin', 'perm_settings_view', 'perm_settings_manage', 'perm_users_manage', 'perm_roles_manage'] },
];

export interface TenantUser {
  id: string;
  tenantId: string;
  email: string;
  fullName: string;
  roles: string[];
  organisationId: string;
  status: 'active' | 'inactive' | 'invited';
  lastLogin: string;
}

// ============================================================
// KBC LearningOS — 11 Demo Test Accounts
// Password for all: Password123
// ============================================================

export const kbcUsers: TenantUser[] = [
  // Learner — Sophie Williams, Marketing Executive L4 at Tim Hortons UK
  { id: 'u_learner', tenantId: 't_kbc_001', email: 'learner@kbc.test', fullName: 'Sophie Williams', roles: ['role_learner'], organisationId: 'org_emp_timhortons', status: 'active', lastLogin: '2026-06-10T08:30:00Z' },

  // Coach — Martin Reeves, Progress Coach with 8-learner caseload
  { id: 'u_coach', tenantId: 't_kbc_001', email: 'coach@kbc.test', fullName: 'Martin Reeves', roles: ['role_coach'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-10T07:45:00Z' },

  // Tutor — Helen Curtis, Curriculum Tutor
  { id: 'u_tutor', tenantId: 't_kbc_001', email: 'tutor@kbc.test', fullName: 'Helen Curtis', roles: ['role_tutor'], organisationId: 'org_dept_biz', status: 'active', lastLogin: '2026-06-09T16:20:00Z' },

  // Employer — James Thompson, Line Manager at Tim Hortons UK
  { id: 'u_employer', tenantId: 't_kbc_001', email: 'employer@kbc.test', fullName: 'James Thompson', roles: ['role_employer'], organisationId: 'org_emp_timhortons', status: 'active', lastLogin: '2026-06-10T09:00:00Z' },

  // Compliance Officer — Rebecca Holmes
  { id: 'u_compliance', tenantId: 't_kbc_001', email: 'compliance@kbc.test', fullName: 'Rebecca Holmes', roles: ['role_compliance'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-10T08:00:00Z' },

  // QA Officer — Tom Bradley
  { id: 'u_qa', tenantId: 't_kbc_001', email: 'qa@kbc.test', fullName: 'Tom Bradley', roles: ['role_qa'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-09T11:45:00Z' },

  // MIS User — Lisa Nguyen
  { id: 'u_mis', tenantId: 't_kbc_001', email: 'mis@kbc.test', fullName: 'Lisa Nguyen', roles: ['role_mis'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-10T06:30:00Z' },

  // Admin / Tenant Admin — Alex Carter
  { id: 'u_admin', tenantId: 't_kbc_001', email: 'admin@kbc.test', fullName: 'Alex Carter', roles: ['role_tenant_admin'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-10T07:00:00Z' },

  // Leadership / Senior Leader — Dr. Rachel Okonkwo, CEO
  { id: 'u_leadership', tenantId: 't_kbc_001', email: 'leadership@kbc.test', fullName: 'Dr. Rachel Okonkwo', roles: ['role_leadership'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-09T17:00:00Z' },

  // Finance — David Morgan, Finance Director
  { id: 'u_finance', tenantId: 't_kbc_001', email: 'finance@kbc.test', fullName: 'David Morgan', roles: ['role_finance'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-10T08:15:00Z' },

  // Auditor — Patricia Stone, External Auditor
  { id: 'u_auditor', tenantId: 't_kbc_001', email: 'auditor@kbc.test', fullName: 'Patricia Stone', roles: ['role_auditor'], organisationId: 'org_kbc_main', status: 'active', lastLogin: '2026-06-09T14:00:00Z' },
];

// Legacy alias for backwards compatibility
export { kbcUsers as kbcDemoUsers };