// ============================================================
// KBC LearningOS — Role-Based Access Control System
// ============================================================

/**
 * Permission Levels — what a user can DO with a resource
 */
export type PermissionLevel =
  | 'none'
  | 'view'
  | 'create'
  | 'edit'
  | 'approve'
  | 'validate'
  | 'reject'
  | 'export'
  | 'delete'
  | 'archive'
  | 'manage_settings'
  | 'full_admin';

export const PERMISSION_LEVELS: { value: PermissionLevel; label: string; rank: number }[] = [
  { value: 'none', label: 'None', rank: 0 },
  { value: 'view', label: 'View', rank: 1 },
  { value: 'create', label: 'Create', rank: 2 },
  { value: 'edit', label: 'Edit', rank: 3 },
  { value: 'approve', label: 'Approve', rank: 4 },
  { value: 'validate', label: 'Validate', rank: 5 },
  { value: 'reject', label: 'Reject', rank: 5 },
  { value: 'export', label: 'Export', rank: 4 },
  { value: 'delete', label: 'Delete', rank: 6 },
  { value: 'archive', label: 'Archive', rank: 6 },
  { value: 'manage_settings', label: 'Manage Settings', rank: 7 },
  { value: 'full_admin', label: 'Full Admin', rank: 8 },
];

/**
 * Access Scopes — the BOUNDARY within which a permission applies
 */
export type AccessScope =
  | 'global'
  | 'tenant'
  | 'organisation'
  | 'employer'
  | 'programme'
  | 'cohort'
  | 'learner'
  | 'own_record'
  | 'assigned_learners_only';

export const ACCESS_SCOPES: { value: AccessScope; label: string }[] = [
  { value: 'global', label: 'Global' },
  { value: 'tenant', label: 'Tenant' },
  { value: 'organisation', label: 'Organisation' },
  { value: 'employer', label: 'Employer' },
  { value: 'programme', label: 'Programme' },
  { value: 'cohort', label: 'Cohort' },
  { value: 'learner', label: 'Learner' },
  { value: 'own_record', label: 'Own Record' },
  { value: 'assigned_learners_only', label: 'Assigned Learners Only' },
];

// ============================================================
// Permission Definitions
// ============================================================

export interface PermissionDef {
  id: string;
  slug: string;
  name: string;
  category: string;
  description: string;
  defaultLevel: PermissionLevel;
  allowedScopes: AccessScope[];
  /** If true, this permission grants implicit access bypassing scope checks */
  isAdminBypass: boolean;
}

export const ALL_PERMISSIONS: PermissionDef[] = [
  // ---- Dashboard ----
  { id: 'perm_dashboard_view', slug: 'dashboard.view', name: 'View Dashboard', category: 'Dashboard', description: 'Access workspace dashboard and summary widgets', defaultLevel: 'view', allowedScopes: ['own_record', 'tenant', 'organisation', 'learner', 'assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_dashboard_manage', slug: 'dashboard.manage', name: 'Customise Dashboard', category: 'Dashboard', description: 'Customise dashboard layout and widgets', defaultLevel: 'manage_settings', allowedScopes: ['own_record'], isAdminBypass: false },

  // ---- Learning ----
  { id: 'perm_learning_view', slug: 'learning.view', name: 'View Learning Content', category: 'Learning', description: 'Access modules, sessions, and learning materials', defaultLevel: 'view', allowedScopes: ['own_record', 'learner', 'assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_learning_create', slug: 'learning.create', name: 'Create Content', category: 'Learning', description: 'Create new learning materials and sessions', defaultLevel: 'create', allowedScopes: ['organisation', 'programme', 'cohort'], isAdminBypass: false },
  { id: 'perm_learning_edit', slug: 'learning.edit', name: 'Edit Content', category: 'Learning', description: 'Edit existing learning materials', defaultLevel: 'edit', allowedScopes: ['organisation', 'programme', 'cohort'], isAdminBypass: false },
  { id: 'perm_learning_delete', slug: 'learning.delete', name: 'Archive Content', category: 'Learning', description: 'Archive and remove learning content', defaultLevel: 'archive', allowedScopes: ['organisation', 'programme'], isAdminBypass: false },

  // ---- Evidence ----
  { id: 'perm_evidence_create', slug: 'evidence.create', name: 'Upload Evidence', category: 'Evidence', description: 'Create and upload evidence items', defaultLevel: 'create', allowedScopes: ['own_record', 'learner', 'assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_evidence_view_own', slug: 'evidence.view_own', name: 'View Own Evidence', category: 'Evidence', description: 'View own submitted evidence', defaultLevel: 'view', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_evidence_view_assigned', slug: 'evidence.view_assigned', name: 'View Assigned Evidence', category: 'Evidence', description: 'View evidence from assigned learners', defaultLevel: 'view', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_evidence_view_all', slug: 'evidence.view_all', name: 'View All Evidence', category: 'Evidence', description: 'View evidence across all learners', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_evidence_validate', slug: 'evidence.validate', name: 'Validate Evidence', category: 'Evidence', description: 'Validate learner evidence submissions', defaultLevel: 'validate', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_evidence_reject', slug: 'evidence.reject', name: 'Reject Evidence', category: 'Evidence', description: 'Reject evidence submissions with feedback', defaultLevel: 'reject', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_evidence_export', slug: 'evidence.export', name: 'Export Evidence', category: 'Evidence', description: 'Export evidence for audit or Ofsted', defaultLevel: 'export', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- OTJH ----
  { id: 'perm_otjh_claim', slug: 'otjh.claim', name: 'Log OTJH', category: 'OTJH', description: 'Log off-the-job training hours', defaultLevel: 'create', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_otjh_view_own', slug: 'otjh.view_own', name: 'View Own OTJH', category: 'OTJH', description: 'View own OTJH records', defaultLevel: 'view', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_otjh_view_assigned', slug: 'otjh.view_assigned', name: 'View Assigned OTJH', category: 'OTJH', description: 'View OTJH of assigned learners', defaultLevel: 'view', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_otjh_validate', slug: 'otjh.validate', name: 'Validate OTJH', category: 'OTJH', description: 'Validate OTJH claims', defaultLevel: 'validate', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_otjh_export', slug: 'otjh.export', name: 'Export OTJH', category: 'OTJH', description: 'Export OTJH data', defaultLevel: 'export', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- KSB ----
  { id: 'perm_ksb_view_own', slug: 'ksb.view_own', name: 'View Own KSB', category: 'KSB', description: 'View own KSB progression', defaultLevel: 'view', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_ksb_view_assigned', slug: 'ksb.view_assigned', name: 'View Assigned KSB', category: 'KSB', description: 'View KSB of assigned learners', defaultLevel: 'view', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_ksb_view_all', slug: 'ksb.view_all', name: 'View All KSB', category: 'KSB', description: 'View KSB across all learners', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },
  { id: 'perm_ksb_assess', slug: 'ksb.assess', name: 'Assess KSB', category: 'KSB', description: 'Assess and grade KSB competencies', defaultLevel: 'validate', allowedScopes: ['assigned_learners_only', 'learner'], isAdminBypass: false },

  // ---- Coaching ----
  { id: 'perm_coaching_view_own', slug: 'coaching.view_own', name: 'View Own Coaching', category: 'Coaching', description: 'View own coaching sessions', defaultLevel: 'view', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_coaching_manage_assigned', slug: 'coaching.manage_assigned', name: 'Manage Coaching', category: 'Coaching', description: 'Schedule and manage coaching for assigned learners', defaultLevel: 'create', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_coaching_view_all', slug: 'coaching.view_all', name: 'View All Coaching', category: 'Coaching', description: 'View all coaching across organisation', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- Reviews ----
  { id: 'perm_reviews_view_own', slug: 'reviews.view_own', name: 'View Own Reviews', category: 'Reviews', description: 'View own progress reviews', defaultLevel: 'view', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_reviews_create', slug: 'reviews.create', name: 'Create Reviews', category: 'Reviews', description: 'Create progress reviews for learners', defaultLevel: 'create', allowedScopes: ['assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_reviews_sign', slug: 'reviews.sign', name: 'Sign Reviews', category: 'Reviews', description: 'Sign and approve progress reviews', defaultLevel: 'approve', allowedScopes: ['own_record', 'learner', 'assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_reviews_view_all', slug: 'reviews.view_all', name: 'View All Reviews', category: 'Reviews', description: 'View all reviews across organisation', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- Attendance ----
  { id: 'perm_attendance_view_own', slug: 'attendance.view_own', name: 'View Own Attendance', category: 'Attendance', description: 'View own attendance records', defaultLevel: 'view', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_attendance_manage', slug: 'attendance.manage', name: 'Manage Attendance', category: 'Attendance', description: 'Take registers and manage attendance', defaultLevel: 'create', allowedScopes: ['cohort', 'assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_attendance_view_all', slug: 'attendance.view_all', name: 'View All Attendance', category: 'Attendance', description: 'View attendance across organisation', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },
  { id: 'perm_attendance_export', slug: 'attendance.export', name: 'Export Attendance', category: 'Attendance', description: 'Export attendance data', defaultLevel: 'export', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- Absence ----
  { id: 'perm_absence_report', slug: 'absence.report', name: 'Report Absence', category: 'Attendance', description: 'Report own absence', defaultLevel: 'create', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_absence_manage', slug: 'absence.manage', name: 'Manage Absences', category: 'Attendance', description: 'Review and approve absence requests', defaultLevel: 'approve', allowedScopes: ['assigned_learners_only', 'cohort'], isAdminBypass: false },

  // ---- Compliance ----
  { id: 'perm_compliance_view', slug: 'compliance.view', name: 'View Compliance', category: 'Compliance', description: 'View compliance documents and status', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme', 'learner'], isAdminBypass: false },
  { id: 'perm_compliance_manage', slug: 'compliance.manage', name: 'Manage Compliance', category: 'Compliance', description: 'Manage compliance documents and workflows', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_compliance_das', slug: 'compliance.das', name: 'DAS Tracking', category: 'Compliance', description: 'Manage DAS apprentice service tracking', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_compliance_ilr', slug: 'compliance.ilr', name: 'ILR Management', category: 'Compliance', description: 'Prepare and manage ILR returns', defaultLevel: 'edit', allowedScopes: ['tenant'], isAdminBypass: false },
  { id: 'perm_compliance_ofsted', slug: 'compliance.ofsted', name: 'Ofsted Evidence', category: 'Compliance', description: 'Manage Ofsted inspection evidence', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_compliance_signatures', slug: 'compliance.signatures', name: 'Digital Signatures', category: 'Compliance', description: 'Request and manage digital signatures', defaultLevel: 'approve', allowedScopes: ['learner', 'own_record', 'assigned_learners_only'], isAdminBypass: false },
  { id: 'perm_compliance_documents', slug: 'compliance.documents', name: 'Document Templates', category: 'Compliance', description: 'Manage compliance document templates', defaultLevel: 'manage_settings', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- QA ----
  { id: 'perm_qa_review', slug: 'qa.review', name: 'QA Review', category: 'QA', description: 'Perform quality assurance reviews', defaultLevel: 'validate', allowedScopes: ['tenant', 'organisation', 'programme', 'learner'], isAdminBypass: false },
  { id: 'perm_qa_spot_check', slug: 'qa.spot_check', name: 'Spot Check', category: 'QA', description: 'Perform spot checks and sampling', defaultLevel: 'validate', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_qa_approve', slug: 'qa.approve', name: 'QA Approve', category: 'QA', description: 'Approve items through QA gate', defaultLevel: 'approve', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },
  { id: 'perm_qa_reject', slug: 'qa.reject', name: 'QA Reject', category: 'QA', description: 'Reject items and request rework', defaultLevel: 'reject', allowedScopes: ['tenant', 'organisation', 'programme', 'learner'], isAdminBypass: false },

  // ---- Audit ----
  { id: 'perm_audit_view', slug: 'audit.view', name: 'View Audit Trail', category: 'Audit', description: 'View the audit trail', defaultLevel: 'view', allowedScopes: ['tenant'], isAdminBypass: false },
  { id: 'perm_audit_export', slug: 'audit.export', name: 'Export Audit', category: 'Audit', description: 'Export audit data', defaultLevel: 'export', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- Reports ----
  { id: 'perm_reports_view', slug: 'reports.view', name: 'View Reports', category: 'Reports', description: 'View reports', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme', 'cohort'], isAdminBypass: false },
  { id: 'perm_reports_create', slug: 'reports.create', name: 'Generate Reports', category: 'Reports', description: 'Generate and customise reports', defaultLevel: 'create', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_reports_export', slug: 'reports.export', name: 'Export Reports', category: 'Reports', description: 'Export reports', defaultLevel: 'export', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- Users ----
  { id: 'perm_users_view', slug: 'users.view', name: 'View Users', category: 'Users', description: 'View user records', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_users_create', slug: 'users.create', name: 'Invite Users', category: 'Users', description: 'Create and invite new users', defaultLevel: 'create', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_users_edit', slug: 'users.edit', name: 'Edit Users', category: 'Users', description: 'Edit user records and assignments', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_users_delete', slug: 'users.delete', name: 'Deactivate Users', category: 'Users', description: 'Deactivate and archive users', defaultLevel: 'archive', allowedScopes: ['tenant'], isAdminBypass: false },
  { id: 'perm_roles_manage', slug: 'roles.manage', name: 'Manage Roles', category: 'Users', description: 'Manage roles and permission assignments', defaultLevel: 'manage_settings', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- Programmes ----
  { id: 'perm_programmes_view', slug: 'programmes.view', name: 'View Programmes', category: 'Programmes', description: 'View programme data', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },
  { id: 'perm_programmes_create', slug: 'programmes.create', name: 'Create Programmes', category: 'Programmes', description: 'Create new apprenticeship programmes', defaultLevel: 'create', allowedScopes: ['tenant'], isAdminBypass: false },
  { id: 'perm_programmes_edit', slug: 'programmes.edit', name: 'Edit Programmes', category: 'Programmes', description: 'Edit programme configuration', defaultLevel: 'edit', allowedScopes: ['tenant'], isAdminBypass: false },
  { id: 'perm_programmes_archive', slug: 'programmes.archive', name: 'Archive Programmes', category: 'Programmes', description: 'Archive programmes', defaultLevel: 'archive', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- Curriculum ----
  { id: 'perm_curriculum_view', slug: 'curriculum.view', name: 'View Curriculum', category: 'Curriculum', description: 'View curriculum structures', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },
  { id: 'perm_curriculum_build', slug: 'curriculum.build', name: 'Build Curriculum', category: 'Curriculum', description: 'Build and edit curriculum modules, weeks, components', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },
  { id: 'perm_curriculum_delete', slug: 'curriculum.delete', name: 'Archive Curriculum', category: 'Curriculum', description: 'Archive curriculum items', defaultLevel: 'archive', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },

  // ---- Cohorts ----
  { id: 'perm_cohorts_view', slug: 'cohorts.view', name: 'View Cohorts', category: 'Cohorts', description: 'View cohort data', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme', 'cohort'], isAdminBypass: false },
  { id: 'perm_cohorts_create', slug: 'cohorts.create', name: 'Create Cohorts', category: 'Cohorts', description: 'Create new learner cohorts', defaultLevel: 'create', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_cohorts_edit', slug: 'cohorts.edit', name: 'Edit Cohorts', category: 'Cohorts', description: 'Edit cohort configuration and dates', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_cohorts_assign', slug: 'cohorts.assign', name: 'Assign Learners', category: 'Cohorts', description: 'Assign learners to cohorts', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- Finance ----
  { id: 'perm_finance_view', slug: 'finance.view', name: 'View Finance', category: 'Finance', description: 'View financial data and funding', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_finance_manage', slug: 'finance.manage', name: 'Manage Finance', category: 'Finance', description: 'Manage invoices, co-investment, and funding', defaultLevel: 'edit', allowedScopes: ['tenant'], isAdminBypass: false },
  { id: 'perm_finance_export', slug: 'finance.export', name: 'Export Finance', category: 'Finance', description: 'Export financial reports', defaultLevel: 'export', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- Employer ----
  { id: 'perm_employer_view_own', slug: 'employer.view_own', name: 'View Own Employer', category: 'Employer', description: 'View own employer details', defaultLevel: 'view', allowedScopes: ['own_record'], isAdminBypass: false },
  { id: 'perm_employer_view_assigned', slug: 'employer.view_assigned', name: 'View Employer Info', category: 'Employer', description: 'View employer-related data for assigned learners', defaultLevel: 'view', allowedScopes: ['assigned_learners_only', 'employer'], isAdminBypass: false },
  { id: 'perm_employer_manage', slug: 'employer.manage', name: 'Manage Employers', category: 'Employer', description: 'Manage employer records and contracting', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },

  // ---- Engagement ----
  { id: 'perm_engagement_view', slug: 'engagement.view', name: 'View Engagement', category: 'Engagement', description: 'View engagement data', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'programme'], isAdminBypass: false },
  { id: 'perm_engagement_manage', slug: 'engagement.manage', name: 'Manage Engagement', category: 'Engagement', description: 'Manage campaigns, leads, and employer accounts', defaultLevel: 'edit', allowedScopes: ['tenant', 'organisation'], isAdminBypass: false },
  { id: 'perm_engagement_risk', slug: 'engagement.risk', name: 'Risk Monitoring', category: 'Engagement', description: 'Monitor and flag at-risk learners', defaultLevel: 'view', allowedScopes: ['tenant', 'organisation', 'assigned_learners_only'], isAdminBypass: false },

  // ---- Settings ----
  { id: 'perm_settings_view', slug: 'settings.view', name: 'View Settings', category: 'Settings', description: 'View tenant settings', defaultLevel: 'view', allowedScopes: ['tenant'], isAdminBypass: false },
  { id: 'perm_settings_manage', slug: 'settings.manage', name: 'Manage Settings', category: 'Settings', description: 'Modify tenant configuration', defaultLevel: 'manage_settings', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- AI ----
  { id: 'perm_ai_toggle', slug: 'ai.toggle', name: 'Toggle AI Mode', category: 'AI', description: 'Switch between manual and AI-assisted modes', defaultLevel: 'edit', allowedScopes: ['own_record', 'tenant'], isAdminBypass: false },
  { id: 'perm_ai_manage', slug: 'ai.manage', name: 'Manage AI Settings', category: 'AI', description: 'Configure AI behaviour and thresholds', defaultLevel: 'manage_settings', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- Notifications ----
  { id: 'perm_notifications_manage', slug: 'notifications.manage', name: 'Manage Notifications', category: 'Settings', description: 'Configure notification templates and rules', defaultLevel: 'manage_settings', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- Integrations ----
  { id: 'perm_integrations_manage', slug: 'integrations.manage', name: 'Manage Integrations', category: 'Settings', description: 'Configure third-party integrations', defaultLevel: 'manage_settings', allowedScopes: ['tenant'], isAdminBypass: false },

  // ---- Admin ----
  { id: 'perm_tenant_admin', slug: 'tenant.admin', name: 'Tenant Admin', category: 'Admin', description: 'Full tenant-level administration — bypasses all scope checks', defaultLevel: 'full_admin', allowedScopes: ['tenant'], isAdminBypass: true },
  { id: 'perm_super_admin', slug: 'super.admin', name: 'Super Admin', category: 'Admin', description: 'Cross-tenant super administration — global access', defaultLevel: 'full_admin', allowedScopes: ['global'], isAdminBypass: true },
];

// ============================================================
// Role Definitions with Permission Assignments
// ============================================================

export interface RolePermissionAssignment {
  permissionSlug: string;
  level: PermissionLevel;
  scope: AccessScope;
}

export interface RoleDef {
  id: string;
  slug: string;
  name: string;
  category: 'learner' | 'delivery' | 'employer' | 'compliance' | 'management' | 'admin';
  description: string;
  isSystem: boolean;
  permissions: RolePermissionAssignment[];
}

export const ALL_ROLES: RoleDef[] = [
  // ---- LEARNER ----
  {
    id: 'role_learner', slug: 'learner', name: 'Apprentice Learner', category: 'learner', isSystem: true,
    description: 'Apprenticeship learner accessing learning, evidence, coaching, and own records.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'own_record' },
      { permissionSlug: 'learning.view', level: 'view', scope: 'own_record' },
      { permissionSlug: 'evidence.create', level: 'create', scope: 'own_record' },
      { permissionSlug: 'evidence.view_own', level: 'view', scope: 'own_record' },
      { permissionSlug: 'otjh.claim', level: 'create', scope: 'own_record' },
      { permissionSlug: 'otjh.view_own', level: 'view', scope: 'own_record' },
      { permissionSlug: 'ksb.view_own', level: 'view', scope: 'own_record' },
      { permissionSlug: 'coaching.view_own', level: 'view', scope: 'own_record' },
      { permissionSlug: 'reviews.view_own', level: 'view', scope: 'own_record' },
      { permissionSlug: 'reviews.sign', level: 'approve', scope: 'own_record' },
      { permissionSlug: 'attendance.view_own', level: 'view', scope: 'own_record' },
      { permissionSlug: 'absence.report', level: 'create', scope: 'own_record' },
      { permissionSlug: 'employer.view_own', level: 'view', scope: 'own_record' },
      { permissionSlug: 'ai.toggle', level: 'view', scope: 'own_record' },
    ],
  },

  // ---- COACH ----
  {
    id: 'role_coach', slug: 'coach', name: 'Progress Coach', category: 'delivery', isSystem: true,
    description: 'Progress coach managing learner caseload, coaching sessions, and progress reviews.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'learning.view', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'evidence.view_assigned', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'evidence.validate', level: 'validate', scope: 'assigned_learners_only' },
      { permissionSlug: 'evidence.reject', level: 'reject', scope: 'assigned_learners_only' },
      { permissionSlug: 'otjh.view_assigned', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'otjh.validate', level: 'validate', scope: 'assigned_learners_only' },
      { permissionSlug: 'ksb.view_assigned', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'ksb.assess', level: 'validate', scope: 'assigned_learners_only' },
      { permissionSlug: 'coaching.manage_assigned', level: 'create', scope: 'assigned_learners_only' },
      { permissionSlug: 'coaching.view_all', level: 'view', scope: 'organisation' },
      { permissionSlug: 'reviews.create', level: 'create', scope: 'assigned_learners_only' },
      { permissionSlug: 'reviews.view_all', level: 'view', scope: 'organisation' },
      { permissionSlug: 'attendance.view_all', level: 'view', scope: 'organisation' },
      { permissionSlug: 'absence.manage', level: 'approve', scope: 'assigned_learners_only' },
      { permissionSlug: 'employer.view_assigned', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'engagement.risk', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'users.view', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'ai.toggle', level: 'edit', scope: 'own_record' },
    ],
  },

  // ---- TUTOR ----
  {
    id: 'role_tutor', slug: 'tutor', name: 'Curriculum Tutor', category: 'delivery', isSystem: true,
    description: 'Curriculum tutor delivering sessions, marking work, and validating evidence.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'learning.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'learning.create', level: 'create', scope: 'programme' },
      { permissionSlug: 'learning.edit', level: 'edit', scope: 'programme' },
      { permissionSlug: 'evidence.view_assigned', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'evidence.validate', level: 'validate', scope: 'assigned_learners_only' },
      { permissionSlug: 'evidence.reject', level: 'reject', scope: 'assigned_learners_only' },
      { permissionSlug: 'ksb.view_assigned', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'ksb.assess', level: 'validate', scope: 'assigned_learners_only' },
      { permissionSlug: 'attendance.manage', level: 'create', scope: 'cohort' },
      { permissionSlug: 'attendance.view_all', level: 'view', scope: 'organisation' },
      { permissionSlug: 'curriculum.view', level: 'view', scope: 'programme' },
      { permissionSlug: 'curriculum.build', level: 'edit', scope: 'programme' },
      { permissionSlug: 'absence.manage', level: 'approve', scope: 'cohort' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'programme' },
      { permissionSlug: 'ai.toggle', level: 'edit', scope: 'own_record' },
      { permissionSlug: 'users.view', level: 'view', scope: 'assigned_learners_only' },
    ],
  },

  // ---- EMPLOYER ----
  {
    id: 'role_employer', slug: 'employer', name: 'Employer / Line Manager', category: 'employer', isSystem: true,
    description: 'Employer monitoring apprentice progress, signing reviews, and employer involvement.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'employer' },
      { permissionSlug: 'employer.view_assigned', level: 'view', scope: 'employer' },
      { permissionSlug: 'ksb.view_assigned', level: 'view', scope: 'assigned_learners_only' },
      { permissionSlug: 'reviews.sign', level: 'approve', scope: 'assigned_learners_only' },
      { permissionSlug: 'reviews.view_all', level: 'view', scope: 'employer' },
      { permissionSlug: 'attendance.view_all', level: 'view', scope: 'employer' },
      { permissionSlug: 'compliance.signatures', level: 'approve', scope: 'assigned_learners_only' },
      { permissionSlug: 'users.view', level: 'view', scope: 'employer' },
    ],
  },

  // ---- ENGAGEMENT MANAGER ----
  {
    id: 'role_engagement', slug: 'engagement', name: 'Engagement Manager', category: 'management', isSystem: true,
    description: 'Managing lead pipeline, employer accounts, attendance monitoring, and learner engagement.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'engagement.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'engagement.manage', level: 'edit', scope: 'organisation' },
      { permissionSlug: 'engagement.risk', level: 'view', scope: 'organisation' },
      { permissionSlug: 'attendance.view_all', level: 'view', scope: 'organisation' },
      { permissionSlug: 'attendance.export', level: 'export', scope: 'organisation' },
      { permissionSlug: 'employer.manage', level: 'edit', scope: 'organisation' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'organisation' },
      { permissionSlug: 'users.view', level: 'view', scope: 'organisation' },
    ],
  },

  // ---- COMPLIANCE OFFICER ----
  {
    id: 'role_compliance', slug: 'compliance', name: 'Compliance Officer', category: 'compliance', isSystem: true,
    description: 'Overseeing documents, DAS, ILR, signatures, and evidence packs.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.manage', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'compliance.das', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'compliance.ilr', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'compliance.ofsted', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.signatures', level: 'approve', scope: 'tenant' },
      { permissionSlug: 'compliance.documents', level: 'manage_settings', scope: 'tenant' },
      { permissionSlug: 'evidence.view_all', level: 'view', scope: 'tenant' },
      { permissionSlug: 'evidence.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'audit.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'audit.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'users.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'programmes.view', level: 'view', scope: 'tenant' },
    ],
  },

  // ---- QA OFFICER ----
  {
    id: 'role_qa', slug: 'qa', name: 'QA Officer', category: 'compliance', isSystem: true,
    description: 'Quality assurance reviews, spot checks, sampling, and approval gates.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'qa.review', level: 'validate', scope: 'tenant' },
      { permissionSlug: 'qa.spot_check', level: 'validate', scope: 'tenant' },
      { permissionSlug: 'qa.approve', level: 'approve', scope: 'tenant' },
      { permissionSlug: 'qa.reject', level: 'reject', scope: 'tenant' },
      { permissionSlug: 'evidence.view_all', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'users.view', level: 'view', scope: 'tenant' },
    ],
  },

  // ---- MIS USER ----
  {
    id: 'role_mis', slug: 'mis', name: 'MIS User', category: 'compliance', isSystem: true,
    description: 'Data management, cohort setup, ILR exports, and attendance mode management.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.ilr', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'cohorts.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'cohorts.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'cohorts.edit', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'cohorts.assign', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'attendance.view_all', level: 'view', scope: 'tenant' },
      { permissionSlug: 'attendance.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'users.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'programmes.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'audit.view', level: 'view', scope: 'tenant' },
    ],
  },

  // ---- CURRICULUM DEVELOPER ----
  {
    id: 'role_curriculum', slug: 'curriculum', name: 'Curriculum Developer', category: 'management', isSystem: true,
    description: 'Programme and curriculum design — should not see private learner compliance records.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'curriculum.view', level: 'view', scope: 'programme' },
      { permissionSlug: 'curriculum.build', level: 'edit', scope: 'programme' },
      { permissionSlug: 'curriculum.delete', level: 'archive', scope: 'programme' },
      { permissionSlug: 'programmes.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'programmes.create', level: 'create', scope: 'organisation' },
      { permissionSlug: 'programmes.edit', level: 'edit', scope: 'organisation' },
      { permissionSlug: 'learning.create', level: 'create', scope: 'programme' },
      { permissionSlug: 'learning.edit', level: 'edit', scope: 'programme' },
      { permissionSlug: 'ksb.view_all', level: 'view', scope: 'programme' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'programme' },
    ],
  },

  // ---- PROGRAMME MANAGER ----
  {
    id: 'role_programme_manager', slug: 'programme-manager', name: 'Programme Manager', category: 'management', isSystem: true,
    description: 'Programme and cohort management across the organisation.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'programmes.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'programmes.create', level: 'create', scope: 'organisation' },
      { permissionSlug: 'programmes.edit', level: 'edit', scope: 'organisation' },
      { permissionSlug: 'cohorts.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'cohorts.create', level: 'create', scope: 'organisation' },
      { permissionSlug: 'cohorts.edit', level: 'edit', scope: 'organisation' },
      { permissionSlug: 'cohorts.assign', level: 'edit', scope: 'organisation' },
      { permissionSlug: 'curriculum.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'curriculum.build', level: 'edit', scope: 'programme' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'organisation' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'organisation' },
      { permissionSlug: 'users.view', level: 'view', scope: 'organisation' },
    ],
  },

  // ---- SENIOR LEADER ----
  {
    id: 'role_leadership', slug: 'leadership', name: 'Senior Leader', category: 'management', isSystem: true,
    description: 'Organisation-wide overview with aggregated dashboards and drill-down reports.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'reports.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'attendance.view_all', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.ofsted', level: 'view', scope: 'tenant' },
      { permissionSlug: 'ksb.view_all', level: 'view', scope: 'tenant' },
      { permissionSlug: 'engagement.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'finance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'users.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'programmes.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'cohorts.view', level: 'view', scope: 'tenant' },
    ],
  },

  // ---- FINANCE USER ----
  {
    id: 'role_finance', slug: 'finance', name: 'Finance User', category: 'management', isSystem: true,
    description: 'Funding overview, employer contribution, co-investment, and payment records.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'finance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'finance.manage', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'finance.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'reports.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'employer.view_assigned', level: 'view', scope: 'tenant' },
    ],
  },

  // ---- AUDITOR ----
  {
    id: 'role_auditor', slug: 'auditor', name: 'Auditor', category: 'compliance', isSystem: true,
    description: 'Read-only access to evidence, reports, audit trails, and locked records.',
    permissions: [
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'audit.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'audit.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'evidence.view_all', level: 'view', scope: 'tenant' },
      { permissionSlug: 'evidence.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'compliance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'users.view', level: 'view', scope: 'tenant' },
    ],
  },

  // ---- TENANT ADMIN ----
  {
    id: 'role_tenant_admin', slug: 'tenant-admin', name: 'Tenant Admin', category: 'admin', isSystem: true,
    description: 'Full tenant-level administration incl. users, settings, programmes, compliance.',
    permissions: [
      { permissionSlug: 'tenant.admin', level: 'full_admin', scope: 'tenant' },
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'settings.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'settings.manage', level: 'manage_settings', scope: 'tenant' },
      { permissionSlug: 'users.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'users.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'users.edit', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'users.delete', level: 'archive', scope: 'tenant' },
      { permissionSlug: 'roles.manage', level: 'manage_settings', scope: 'tenant' },
      { permissionSlug: 'programmes.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'programmes.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'programmes.edit', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'programmes.archive', level: 'archive', scope: 'tenant' },
      { permissionSlug: 'curriculum.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'curriculum.build', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'curriculum.delete', level: 'archive', scope: 'tenant' },
      { permissionSlug: 'cohorts.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'cohorts.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'cohorts.edit', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'cohorts.assign', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'compliance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'compliance.manage', level: 'manage_settings', scope: 'tenant' },
      { permissionSlug: 'compliance.documents', level: 'manage_settings', scope: 'tenant' },
      { permissionSlug: 'evidence.view_all', level: 'view', scope: 'tenant' },
      { permissionSlug: 'evidence.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'tenant' },
      { permissionSlug: 'reports.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'audit.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'audit.export', level: 'export', scope: 'tenant' },
      { permissionSlug: 'finance.view', level: 'view', scope: 'tenant' },
      { permissionSlug: 'ai.toggle', level: 'edit', scope: 'tenant' },
      { permissionSlug: 'ai.manage', level: 'manage_settings', scope: 'tenant' },
      { permissionSlug: 'notifications.manage', level: 'manage_settings', scope: 'tenant' },
      { permissionSlug: 'integrations.manage', level: 'manage_settings', scope: 'tenant' },
    ],
  },

  // ---- SUPER ADMIN ----
  {
    id: 'role_super_admin', slug: 'super-admin', name: 'Super Admin', category: 'admin', isSystem: true,
    description: 'Cross-tenant super administration — manages all tenants and global system configuration.',
    permissions: [
      { permissionSlug: 'super.admin', level: 'full_admin', scope: 'global' },
      { permissionSlug: 'tenant.admin', level: 'full_admin', scope: 'global' },
      { permissionSlug: 'settings.view', level: 'view', scope: 'global' },
      { permissionSlug: 'settings.manage', level: 'manage_settings', scope: 'global' },
      { permissionSlug: 'users.view', level: 'view', scope: 'global' },
      { permissionSlug: 'users.create', level: 'create', scope: 'global' },
      { permissionSlug: 'users.edit', level: 'edit', scope: 'global' },
      { permissionSlug: 'users.delete', level: 'archive', scope: 'global' },
      { permissionSlug: 'roles.manage', level: 'manage_settings', scope: 'global' },
      { permissionSlug: 'dashboard.view', level: 'view', scope: 'global' },
      { permissionSlug: 'audit.view', level: 'view', scope: 'global' },
      { permissionSlug: 'audit.export', level: 'export', scope: 'global' },
      { permissionSlug: 'reports.view', level: 'view', scope: 'global' },
      { permissionSlug: 'reports.create', level: 'create', scope: 'global' },
      { permissionSlug: 'integrations.manage', level: 'manage_settings', scope: 'global' },
    ],
  },
];

// ============================================================
// Route-to-Permission Mapping
// ============================================================

export interface RoutePermissionMap {
  path: string;
  requiredPermission?: string;
  requiredLevel?: PermissionLevel;
  /** Roles that can access this route (used when no specific permission maps) */
  allowedRoles?: string[];
  /** Sub-routes that inherit or override */
  children?: RoutePermissionMap[];
}

export const ROUTE_PERMISSIONS: RoutePermissionMap[] = [
  { path: '/users/progress-transfers', allowedRoles: ['tenant-admin', 'super-admin'] },
  { path: '/curriculum/progress-transfers', allowedRoles: ['tenant-admin', 'super-admin'] },
  { path: '/', allowedRoles: ['*'] },
  { path: '/login', allowedRoles: ['*'] },
  { path: '/onboarding', allowedRoles: ['learner', 'compliance', 'qa', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/learner', allowedRoles: ['learner', 'coach', 'tutor', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/coach', allowedRoles: ['coach', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/tutor', allowedRoles: ['tutor', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/employer', allowedRoles: ['employer', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/engagement', allowedRoles: ['engagement', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/compliance', allowedRoles: ['compliance', 'qa', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/mis', allowedRoles: ['mis', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/qa', allowedRoles: ['qa', 'compliance', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/curriculum', allowedRoles: ['curriculum', 'programme-manager', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/leadership', allowedRoles: ['leadership', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/admin', allowedRoles: ['tenant-admin', 'super-admin'] },
  { path: '/workspace/finance', allowedRoles: ['finance', 'tenant-admin', 'super-admin'] },
  { path: '/workspace/auditor', allowedRoles: ['auditor', 'tenant-admin', 'super-admin'] },
  { path: '/admin/system', allowedRoles: ['tenant-admin', 'super-admin'] },
  { path: '/learning', allowedRoles: ['learner', 'coach', 'tutor', 'tenant-admin', 'super-admin'] },
  { path: '/coaching', allowedRoles: ['learner', 'coach', 'tenant-admin', 'super-admin'] },
  { path: '/users', allowedRoles: ['compliance', 'qa', 'tenant-admin', 'super-admin'] },
  { path: '/compliance', allowedRoles: ['compliance', 'qa', 'tenant-admin', 'super-admin'] },
  { path: '/reports', allowedRoles: ['coach', 'tutor', 'employer', 'engagement', 'compliance', 'qa', 'mis', 'leadership', 'finance', 'auditor', 'tenant-admin', 'super-admin'] },
  { path: '/admin', allowedRoles: ['tenant-admin', 'super-admin'] },
  { path: '/profile', allowedRoles: ['*'] },
  { path: '/employer', allowedRoles: ['employer', 'tenant-admin', 'super-admin'] },
  { path: '/coach', allowedRoles: ['coach', 'tenant-admin', 'super-admin'] },
  { path: '/tutor', allowedRoles: ['tutor', 'tenant-admin', 'super-admin'] },
  { path: '/curriculum', allowedRoles: ['curriculum', 'programme-manager', 'tenant-admin', 'super-admin'] },
  { path: '/curriculum/learner-module-progress', allowedRoles: ['curriculum', 'programme-manager', 'coach', 'tutor', 'tenant-admin', 'super-admin'] },
  { path: '/engagement', allowedRoles: ['engagement', 'tenant-admin', 'super-admin'] },
  { path: '/mis', allowedRoles: ['mis', 'tenant-admin', 'super-admin'] },
  { path: '/qa', allowedRoles: ['qa', 'compliance', 'tenant-admin', 'super-admin'] },
  { path: '/leadership', allowedRoles: ['leadership', 'tenant-admin', 'super-admin'] },
  { path: '/finance', allowedRoles: ['finance', 'tenant-admin', 'super-admin'] },
  { path: '/auditor', allowedRoles: ['auditor', 'tenant-admin', 'super-admin'] },
];

// ============================================================
// Navigation Section Permission Mapping
// ============================================================

export interface NavPermissionMap {
  navId: string;
  requiredSlug?: string;
  requiredLevel?: PermissionLevel;
  allowedRoles?: string[];
}

export const NAV_PERMISSIONS: NavPermissionMap[] = [
  { navId: 'enrolment-progress-transfers', allowedRoles: ['tenant-admin', 'super-admin'] },
  // Learner nav
  { navId: 'dashboard', allowedRoles: ['*'] },
  { navId: 'learning', allowedRoles: ['learner', 'coach', 'tutor', 'tenant-admin', 'super-admin'] },
  { navId: 'evidence', allowedRoles: ['learner', 'coach', 'tutor', 'tenant-admin', 'super-admin'] },
  { navId: 'coaching', allowedRoles: ['learner', 'coach', 'tenant-admin', 'super-admin'] },
  { navId: 'attendance', allowedRoles: ['learner', 'tutor', 'tenant-admin', 'super-admin'] },
  { navId: 'rewards', allowedRoles: ['learner', 'tenant-admin', 'super-admin'] },
  { navId: 'documents', allowedRoles: ['learner', 'tenant-admin', 'super-admin'] },
  // Coach nav
  { navId: 'learners', allowedRoles: ['coach', 'tenant-admin', 'super-admin'] },
  { navId: 'coaching-calendar', allowedRoles: ['coach', 'tenant-admin', 'super-admin'] },
  { navId: 'reviews', allowedRoles: ['coach', 'tutor', 'employer', 'tenant-admin', 'super-admin'] },
  { navId: 'evidence-review', allowedRoles: ['coach', 'tutor', 'tenant-admin', 'super-admin'] },
  { navId: 'employer-liaison', allowedRoles: ['coach', 'engagement', 'tenant-admin', 'super-admin'] },
  // Tutor nav
  { navId: 'delivery', allowedRoles: ['tutor', 'tenant-admin', 'super-admin'] },
  { navId: 'marking', allowedRoles: ['tutor', 'tenant-admin', 'super-admin'] },
  { navId: 'validation', allowedRoles: ['tutor', 'qa', 'tenant-admin', 'super-admin'] },
  // Employer nav
  { navId: 'apprentices', allowedRoles: ['employer', 'tenant-admin', 'super-admin'] },
  { navId: 'signatures', allowedRoles: ['employer', 'compliance', 'tenant-admin', 'super-admin'] },
  // Admin nav
  { navId: 'settings', allowedRoles: ['tenant-admin', 'super-admin'] },
  { navId: 'users', allowedRoles: ['tenant-admin', 'super-admin'] },
  { navId: 'programmes', allowedRoles: ['admin', 'curriculum', 'programme-manager', 'tenant-admin', 'super-admin'] },
  { navId: 'curriculum', allowedRoles: ['curriculum', 'programme-manager', 'tenant-admin', 'super-admin'] },
  { navId: 'cohorts', allowedRoles: ['mis', 'programme-manager', 'tenant-admin', 'super-admin'] },
  { navId: 'tenants', allowedRoles: ['super-admin'] },
  // Engagement nav
  { navId: 'campaigns', allowedRoles: ['engagement', 'tenant-admin', 'super-admin'] },
  { navId: 'leads', allowedRoles: ['engagement', 'tenant-admin', 'super-admin'] },
  { navId: 'employers', allowedRoles: ['engagement', 'tenant-admin', 'super-admin'] },
  // MIS nav
  { navId: 'data', allowedRoles: ['mis', 'tenant-admin', 'super-admin'] },
  { navId: 'ilr', allowedRoles: ['mis', 'compliance', 'tenant-admin', 'super-admin'] },
  { navId: 'funding', allowedRoles: ['mis', 'finance', 'tenant-admin', 'super-admin'] },
  // Finance nav
  { navId: 'invoices', allowedRoles: ['finance', 'tenant-admin', 'super-admin'] },
  // QA nav
  { navId: 'spot-checks', allowedRoles: ['qa', 'tenant-admin', 'super-admin'] },
  // Leadership nav
  { navId: 'overview', allowedRoles: ['leadership', 'tenant-admin', 'super-admin'] },
  { navId: 'performance', allowedRoles: ['leadership', 'tenant-admin', 'super-admin'] },
  // Auditor nav
  { navId: 'audit-trail', allowedRoles: ['auditor', 'compliance', 'tenant-admin', 'super-admin'] },
  // The system-wide Audit Trail. Same four roles as the scoped one above:
  // widening what a page can show is not a reason to widen who can open it,
  // and this one shows every workspace at once.
  { navId: 'admin-audit-trail', allowedRoles: ['auditor', 'compliance', 'tenant-admin', 'super-admin'] },
  // The Coach workspace's scoped door. Same four roles again: it reads every
  // coach's activity, including the notes and marking feedback they wrote, so
  // holding Coach access is not what entitles somebody to open it.
  { navId: 'coach-audit-trail', allowedRoles: ['auditor', 'compliance', 'tenant-admin', 'super-admin'] },
  // Reports
  { navId: 'reports', allowedRoles: ['coach', 'tutor', 'employer', 'engagement', 'compliance', 'qa', 'mis', 'leadership', 'finance', 'auditor', 'tenant-admin', 'super-admin'] },
];
