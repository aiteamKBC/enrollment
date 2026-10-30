export interface SearchResultItem {
  id: string;
  label: string;
  subtitle: string;
  icon: string;
  category: string;
  categoryLabel: string;
  href: string;
  statusBadge?: string;
}

export const SEARCH_CATEGORIES: { slug: string; label: string; icon: string }[] = [
  { slug: 'learners', label: 'Learners', icon: 'ri-user-line' },
  { slug: 'employers', label: 'Employers', icon: 'ri-building-line' },
  { slug: 'line-managers', label: 'Line Managers', icon: 'ri-user-star-line' },
  { slug: 'cohorts', label: 'Cohorts', icon: 'ri-group-line' },
  { slug: 'programmes', label: 'Programmes', icon: 'ri-stack-line' },
  { slug: 'modules', label: 'Modules', icon: 'ri-book-open-line' },
  { slug: 'weeks', label: 'Weeks', icon: 'ri-calendar-line' },
  { slug: 'components', label: 'Components', icon: 'ri-puzzle-line' },
  { slug: 'evidence', label: 'Evidence', icon: 'ri-folder-upload-line' },
  { slug: 'documents', label: 'Documents', icon: 'ri-file-text-line' },
  { slug: 'signatures', label: 'Signatures', icon: 'ri-pen-nib-line' },
  { slug: 'progress-reviews', label: 'Progress Reviews', icon: 'ri-file-chart-line' },
  { slug: 'coaching-meetings', label: 'Coaching Meetings', icon: 'ri-chat-smile-2-line' },
  { slug: 'otjh', label: 'OTJH Reports', icon: 'ri-time-line' },
  { slug: 'ksbs', label: 'KSBs', icon: 'ri-bar-chart-2-line' },
  { slug: 'quizzes', label: 'Quizzes', icon: 'ri-questionnaire-line' },
  { slug: 'messages', label: 'Messages', icon: 'ri-mail-line' },
  { slug: 'tasks', label: 'Tasks', icon: 'ri-task-line' },
  { slug: 'qa-findings', label: 'QA Findings', icon: 'ri-search-eye-line' },
  { slug: 'audit-records', label: 'Audit Records', icon: 'ri-history-line' },
];

export const ALL_SEARCH_RESULTS: SearchResultItem[] = [
  // Learners
  { id: 's_001', label: 'Sarah Mitchell', subtitle: 'Business Admin L3 — Cohort C · KCC Employer', icon: 'ri-user-line', category: 'learners', categoryLabel: 'Learners', href: '/learners/sarah-mitchell', statusBadge: 'On Track' },
  { id: 's_002', label: 'James Okonkwo', subtitle: 'Digital Marketing L3 — Cohort B · Medway NHS', icon: 'ri-user-line', category: 'learners', categoryLabel: 'Learners', href: '/learners/james-okonkwo', statusBadge: 'At Risk' },
  { id: 's_003', label: 'Emily Watson', subtitle: 'Business Admin L3 — Cohort A · Canterbury Council', icon: 'ri-user-line', category: 'learners', categoryLabel: 'Learners', href: '/learners/emily-watson', statusBadge: 'On Track' },
  { id: 's_004', label: 'David Chen', subtitle: 'Data Technician L4 — Cohort D · TechKent Ltd', icon: 'ri-user-line', category: 'learners', categoryLabel: 'Learners', href: '/learners/david-chen', statusBadge: 'High Performer' },
  { id: 's_005', label: 'Aisha Patel', subtitle: 'Leadership & Management L5 — Cohort E · Ashford Logistics', icon: 'ri-user-line', category: 'learners', categoryLabel: 'Learners', href: '/learners/aisha-patel', statusBadge: 'On Track' },

  // Employers
  { id: 's_010', label: 'Kent County Council', subtitle: 'Employer — 14 active apprentices · Primary contact: Mark Davies', icon: 'ri-building-line', category: 'employers', categoryLabel: 'Employers', href: '/employers/kcc', statusBadge: 'Active' },
  { id: 's_011', label: 'Medway NHS Trust', subtitle: 'Employer — 8 active apprentices · Primary contact: Susan Price', icon: 'ri-building-line', category: 'employers', categoryLabel: 'Employers', href: '/employers/medway-nhs', statusBadge: 'Active' },
  { id: 's_012', label: 'TechKent Ltd', subtitle: 'Employer — 6 active apprentices · Primary contact: Alex Turner', icon: 'ri-building-line', category: 'employers', categoryLabel: 'Employers', href: '/employers/techkent', statusBadge: 'Active' },

  // Line Managers
  { id: 's_015', label: 'Mark Davies', subtitle: 'Line Manager — KCC · 5 direct apprentices', icon: 'ri-user-star-line', category: 'line-managers', categoryLabel: 'Line Managers', href: '/line-managers/mark-davies' },
  { id: 's_016', label: 'Susan Price', subtitle: 'Line Manager — Medway NHS · 3 direct apprentices', icon: 'ri-user-star-line', category: 'line-managers', categoryLabel: 'Line Managers', href: '/line-managers/susan-price' },

  // Cohorts
  { id: 's_020', label: 'Cohort A — Business Admin L3', subtitle: 'Started Sept 2025 · 12 learners · EPA window: Jun 2027', icon: 'ri-group-line', category: 'cohorts', categoryLabel: 'Cohorts', href: '/cohorts/cohort-a', statusBadge: 'Active' },
  { id: 's_021', label: 'Cohort B — Digital Marketing L3', subtitle: 'Started Jan 2026 · 10 learners · EPA window: Oct 2027', icon: 'ri-group-line', category: 'cohorts', categoryLabel: 'Cohorts', href: '/cohorts/cohort-b', statusBadge: 'Active' },
  { id: 's_022', label: 'Cohort C — Business Admin L3', subtitle: 'Started Mar 2026 · 8 learners · EPA window: Dec 2027', icon: 'ri-group-line', category: 'cohorts', categoryLabel: 'Cohorts', href: '/cohorts/cohort-c', statusBadge: 'Onboarding' },

  // Programmes
  { id: 's_030', label: 'Business Administrator L3', subtitle: 'ST0070 · Level 3 · 18 months · Ofsted Outstanding', icon: 'ri-stack-line', category: 'programmes', categoryLabel: 'Programmes', href: '/programmes/business-admin-l3' },
  { id: 's_031', label: 'Digital Marketer L3', subtitle: 'ST0122 · Level 3 · 15 months · Active cohorts: 2', icon: 'ri-stack-line', category: 'programmes', categoryLabel: 'Programmes', href: '/programmes/digital-marketer-l3' },
  { id: 's_032', label: 'Data Technician L4', subtitle: 'ST0795 · Level 4 · 24 months · Active cohorts: 1', icon: 'ri-stack-line', category: 'programmes', categoryLabel: 'Programmes', href: '/programmes/data-technician-l4' },

  // Modules
  { id: 's_040', label: 'MOD-001: Workplace Communication', subtitle: 'Business Admin L3 · Week 1-4 · 4 components', icon: 'ri-book-open-line', category: 'modules', categoryLabel: 'Modules', href: '/modules/mod-001' },
  { id: 's_041', label: 'MOD-007: Managing Stakeholders', subtitle: 'Business Admin L3 · Week 12-15 · 3 components', icon: 'ri-book-open-line', category: 'modules', categoryLabel: 'Modules', href: '/modules/mod-007' },
  { id: 's_042', label: 'MOD-012: Data Analysis Fundamentals', subtitle: 'Data Technician L4 · Week 5-8 · 5 components', icon: 'ri-book-open-line', category: 'modules', categoryLabel: 'Modules', href: '/modules/mod-012' },

  // Weeks
  { id: 's_050', label: 'Week 6: Professional Standards', subtitle: 'Business Admin L3 — Sarah Mitchell · 20/06/2026', icon: 'ri-calendar-line', category: 'weeks', categoryLabel: 'Weeks', href: '/learning/weekly/week-6' },
  { id: 's_051', label: 'Week 14: Digital Campaigns', subtitle: 'Digital Marketing L3 — James Okonkwo · 15/06/2026', icon: 'ri-calendar-line', category: 'weeks', categoryLabel: 'Weeks', href: '/learning/weekly/week-14' },

  // Components
  { id: 's_060', label: 'COMP-025: Email Etiquette', subtitle: 'MOD-001 · Assessed · Linked KSBs: B1, C3', icon: 'ri-puzzle-line', category: 'components', categoryLabel: 'Components', href: '/components/comp-025' },
  { id: 's_061', label: 'COMP-089: Stakeholder Mapping', subtitle: 'MOD-007 · Portfolio Evidence · Linked KSBs: D1, D4, E2', icon: 'ri-puzzle-line', category: 'components', categoryLabel: 'Components', href: '/components/comp-089' },

  // Evidence
  { id: 's_070', label: 'EV-1245: Meeting Minutes — Board Presentation', subtitle: 'Sarah Mitchell · Submitted 08/06/2026 · Awaiting Validation', icon: 'ri-folder-upload-line', category: 'evidence', categoryLabel: 'Evidence', href: '/evidence/ev-1245', statusBadge: 'Pending' },
  { id: 's_071', label: 'EV-1238: Customer Feedback Log', subtitle: 'Emily Watson · Validated 05/06/2026 · KSBs: B3, C1', icon: 'ri-folder-upload-line', category: 'evidence', categoryLabel: 'Evidence', href: '/evidence/ev-1238', statusBadge: 'Validated' },
  { id: 's_072', label: 'EV-1230: Process Improvement Proposal', subtitle: 'James Okonkwo · Submitted 02/06/2026 · Rejected', icon: 'ri-folder-upload-line', category: 'evidence', categoryLabel: 'Evidence', href: '/evidence/ev-1230', statusBadge: 'Rejected' },

  // Documents
  { id: 's_080', label: 'DOC-567: Apprenticeship Agreement', subtitle: 'Sarah Mitchell · Signed 15/09/2025 · Expires: 14/03/2027', icon: 'ri-file-text-line', category: 'documents', categoryLabel: 'Documents', href: '/documents/doc-567', statusBadge: 'Signed' },
  { id: 's_081', label: 'DOC-612: Commitment Statement', subtitle: 'David Chen · Awaiting employer signature · Due: 25/06/2026', icon: 'ri-file-text-line', category: 'documents', categoryLabel: 'Documents', href: '/documents/doc-612', statusBadge: 'Pending' },

  // Signatures
  { id: 's_090', label: 'SIG-089: Progress Review Q2 — Employer', subtitle: 'Mark Davies (KCC) · Sarah Mitchell · Overdue', icon: 'ri-pen-nib-line', category: 'signatures', categoryLabel: 'Signatures', href: '/signatures/sig-089', statusBadge: 'Overdue' },
  { id: 's_091', label: 'SIG-092: Monthly Review May', subtitle: 'Susan Price (Medway NHS) · James Okonkwo · Awaiting', icon: 'ri-pen-nib-line', category: 'signatures', categoryLabel: 'Signatures', href: '/signatures/sig-092', statusBadge: 'Awaiting' },

  // Progress Reviews
  { id: 's_100', label: 'PR-2026-Q2: Sarah Mitchell', subtitle: 'Period: Apr–Jun 2026 · OTJH: 87/112 hrs · Coach: Martin Reeves', icon: 'ri-file-chart-line', category: 'progress-reviews', categoryLabel: 'Progress Reviews', href: '/reviews/pr-2026-q2-sm', statusBadge: 'In Progress' },
  { id: 's_101', label: 'PR-2026-Q2: Emily Watson', subtitle: 'Period: Apr–Jun 2026 · OTJH: 98/112 hrs · Coach: Martin Reeves', icon: 'ri-file-chart-line', category: 'progress-reviews', categoryLabel: 'Progress Reviews', href: '/reviews/pr-2026-q2-ew', statusBadge: 'Completed' },

  // Coaching Meetings
  { id: 's_110', label: 'CM-045: Monthly Coaching — Sarah Mitchell', subtitle: '18/06/2026 · 14:00 · Coach: Martin Reeves · Agenda pending', icon: 'ri-chat-smile-2-line', category: 'coaching-meetings', categoryLabel: 'Coaching Meetings', href: '/coaching/meetings/cm-045', statusBadge: 'Scheduled' },
  { id: 's_111', label: 'CM-042: Monthly Coaching — James Okonkwo', subtitle: '12/06/2026 · 10:00 · Completed · Actions: 3 overdue', icon: 'ri-chat-smile-2-line', category: 'coaching-meetings', categoryLabel: 'Coaching Meetings', href: '/coaching/meetings/cm-042', statusBadge: 'Completed' },

  // OTJH Reports
  { id: 's_120', label: 'OTJH-089: Sarah Mitchell — June Week 1', subtitle: '07/06/2026 · 14.5 hrs logged · Tutor validation pending', icon: 'ri-time-line', category: 'otjh', categoryLabel: 'OTJH Reports', href: '/otjh/otjh-089', statusBadge: 'Pending' },
  { id: 's_121', label: 'OTJH-092: Emily Watson — May Summary', subtitle: '31/05/2026 · 45/45 hrs target met · All validated', icon: 'ri-time-line', category: 'otjh', categoryLabel: 'OTJH Reports', href: '/otjh/otjh-092', statusBadge: 'Validated' },

  // KSBs
  { id: 's_130', label: 'KSB B1: Communication', subtitle: 'Business Admin L3 · Coverage: 78% · 12 evidence items linked', icon: 'ri-bar-chart-2-line', category: 'ksbs', categoryLabel: 'KSBs', href: '/ksb/b1-communication' },
  { id: 's_131', label: 'KSB D2: Stakeholder Management', subtitle: 'Business Admin L3 · Coverage: 45% · 6 evidence items linked', icon: 'ri-bar-chart-2-line', category: 'ksbs', categoryLabel: 'KSBs', href: '/ksb/d2-stakeholder' },

  // Quizzes
  { id: 's_140', label: 'QZ-012: Data Protection & GDPR', subtitle: 'Business Admin L3 · 20 questions · Last used: 05/06/2026', icon: 'ri-questionnaire-line', category: 'quizzes', categoryLabel: 'Quizzes', href: '/quizzes/qz-012' },
  { id: 's_141', label: 'QZ-034: Digital Marketing Ethics', subtitle: 'Digital Marketing L3 · 15 questions · Average score: 82%', icon: 'ri-questionnaire-line', category: 'quizzes', categoryLabel: 'Quizzes', href: '/quizzes/qz-034' },

  // Messages
  { id: 's_150', label: 'RE: Monthly Check-in Preparation', subtitle: 'From: Martin Reeves · Sarah Mitchell · 2h ago · 3 replies', icon: 'ri-mail-line', category: 'messages', categoryLabel: 'Messages', href: '/messages/thread-023', statusBadge: 'Unread' },
  { id: 's_151', label: 'Employer Review Scheduling', subtitle: 'From: Mark Davies · All KCC apprentices · Yesterday · 5 replies', icon: 'ri-mail-line', category: 'messages', categoryLabel: 'Messages', href: '/messages/thread-019' },

  // Tasks
  { id: 's_160', label: 'TASK-234: Validate OTJH entries — Cohort A', subtitle: 'Assigned to: Helen Curtis · Due: 14/06/2026 · Priority: High', icon: 'ri-task-line', category: 'tasks', categoryLabel: 'Tasks', href: '/tasks/task-234', statusBadge: 'High' },
  { id: 's_161', label: 'TASK-241: Complete Q2 progress reports', subtitle: 'Assigned to: Martin Reeves · Due: 20/06/2026 · Priority: Medium', icon: 'ri-task-line', category: 'tasks', categoryLabel: 'Tasks', href: '/tasks/task-241', statusBadge: 'Medium' },

  // QA Findings
  { id: 's_170', label: 'QA-FIND-012: Evidence validation inconsistency', subtitle: 'Found by: Tom Bradley · 04/06/2026 · Status: Open · Cohort C', icon: 'ri-search-eye-line', category: 'qa-findings', categoryLabel: 'QA Findings', href: '/qa/findings/qa-find-012', statusBadge: 'Open' },
  { id: 's_171', label: 'QA-FIND-009: KSB mapping gap in MOD-007', subtitle: 'Found by: Tom Bradley · 28/05/2026 · Status: Resolved', icon: 'ri-search-eye-line', category: 'qa-findings', categoryLabel: 'QA Findings', href: '/qa/findings/qa-find-009', statusBadge: 'Resolved' },

  // Audit Records
  { id: 's_180', label: 'AUD-5678: Evidence validation override', subtitle: 'User: Helen Curtis · 03/06/2026 · Entity: EV-1238 · Action: validate', icon: 'ri-history-line', category: 'audit-records', categoryLabel: 'Audit Records', href: '/audit/aud-5678' },
  { id: 's_181', label: 'AUD-5701: ILR data export', subtitle: 'User: Lisa Nguyen · 07/06/2026 · Entity: ILR-May-2026 · Action: export', icon: 'ri-history-line', category: 'audit-records', categoryLabel: 'Audit Records', href: '/audit/aud-5701' },
];

export const RECENT_SEARCHES: string[] = [
  'Sarah Mitchell evidence',
  'Cohort A OTJH',
  'Business Admin KSBs',
  'Martin Reeves coaching',
  'Q2 progress reviews',
];