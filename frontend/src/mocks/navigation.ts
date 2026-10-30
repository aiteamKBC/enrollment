import type { SidebarNavItem } from '@/components/feature/Sidebar';
import { CHAT_ENABLED, LEARNER_FEEDBACK_ENABLED, LEARNER_POST_LECTURE_FEEDBACK_ENABLED } from '@/lib/featureFlags';
import { inclusionLoginUrl } from '@/features/inclusion/navigation';

const inclusionLoginHref = inclusionLoginUrl();

// ============================================================================
// Learner tools reached from the dashboard, with the same permission IDs as
// their former Community navigation entries. No sample counters or badges.
export const learnerDashboardLinks: (SidebarNavItem & { href: string; description: string })[] = [
  { id: 'learner-clubs', label: 'Clubs & meetings', icon: 'ri-team-line', href: '/learner/clubs', description: 'See your assigned clubs and their meeting details.' },
  { id: 'learner-events', label: 'Events & bookings', icon: 'ri-calendar-event-line', href: '/learner/clubs/events', description: 'Explore events and manage your bookings.' },
  { id: 'learner-rewards', label: 'Points & rewards', icon: 'ri-trophy-line', href: '/learner/rewards', description: 'View your earned points and available rewards.' },
  { id: 'learner-flash-cards', label: 'Flash cards', icon: 'ri-flashlight-line', href: '/learner/flash-cards', description: 'Practise with published revision cards.' },
];

// LEARNER WORKSPACE — Core learning and progress navigation
// ============================================================================
export const learnerNavItems: SidebarNavItem[] = [
  { id: 'learner-home', label: 'Home', icon: 'ri-home-line', href: '/workspace/learner' },
  { id: 'learner-overview', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/learner/dashboard', matchPaths: learnerDashboardLinks.map(item => item.href) },

  // My Learning — training plan, learning journey and quizzes merged into
  // Overview/Modules/Quizzes tabs on one page.
  { id: 'learner-my-learning', label: 'My Learning', icon: 'ri-book-open-line', href: '/learner/my-learning', matchPaths: ['/learner/learning-plan'], badge: 1 },

  // Published forms explicitly assigned to the signed-in learner.
  ...(LEARNER_FEEDBACK_ENABLED || LEARNER_POST_LECTURE_FEEDBACK_ENABLED
    ? [{ id: 'learner-feedback', label: 'Feedback', icon: 'ri-survey-line', href: '/learner/feedback' }]
    : []),

  // My Progress
  {
    id: 'learner-group-monthly',
    label: 'My Progress',
    icon: 'ri-loop-left-line',
    href: '',
    children: [
      { id: 'learner-monthly-submission', label: 'Monthly Submission', icon: 'ri-file-upload-line', href: '/learner/monthly-submission' },
      { id: 'learner-monthly-logs', label: 'Monthly Logs', icon: 'ri-file-list-3-line', href: '/learner/monthly-logs', matchPaths: ['/learner/monthly-cycle'] },
      { id: 'learner-monthly-coaching', label: 'Monthly Coaching Meeting', icon: 'ri-chat-smile-2-line', href: '/learner/monthly-coaching' },
      { id: 'learner-progress-reviews', label: 'Progress Review', icon: 'ri-file-chart-line', href: '/learner/progress-reviews' },
      { id: 'learner-reviews', label: 'Reviews', icon: 'ri-file-list-3-line', href: '/learner/reviews' },
    ],
  },

  // Attendance — single item; reporting an absence is an action inside the page.
  { id: 'learner-attendance', label: 'Attendance', icon: 'ri-calendar-check-line', href: '/learner/attendance' },

  // Evidence — the learner's own submissions and where each one is in review.
  { id: 'learner-evidence', label: 'Evidence', icon: 'ri-folder-upload-line', href: '/learner/evidence' },

  // Calendar
  { id: 'learner-calendar', label: 'Calendar', icon: 'ri-calendar-2-line', href: '/learner/calendar', statusDot: 'green' },

  // The learner's own enrolment wizard — relevant while onboarding, and stays
  // available afterwards as a record of what they submitted.
  { id: 'learner-onboarding', label: 'My Enrolment', icon: 'ri-file-user-line', href: '/learner/onboarding' },

  // Statutory paperwork the learner signs (Apprenticeship Agreement today, the
  // rest of DOC_TYPES as their generators land).
  { id: 'learner-compliance-documents', label: 'Compliance documents', icon: 'ri-shield-check-line', href: '/learner/compliance-documents' },

  // Community tools now live on the dashboard. Readiness and Help still use
  // demonstration records, so they are not advertised in learner navigation.

  // The learner's database-backed coach conversation.
  { id: 'learner-messages', label: 'Messages', icon: 'ri-message-3-line', href: '/learner/messages' },
];

// ============================================================================
// COACH WORKSPACE — Grouped sidebar
// ============================================================================
// Gated to the audit roles in `rbac.ts`, so a coach reading their own workspace
// never sees it: this door reads every coach's activity, not the signed-in
// one's.
//
// Named on its own because it is the one Coach destination that does not read
// the selected coach. That makes it the only item the picker at
// `/workspace/coach` can offer before a coach has been chosen.
export const coachAuditTrailNavItem: SidebarNavItem = {
  id: 'coach-audit-trail',
  label: 'Audit Trail',
  icon: 'ri-history-line',
  href: '/coach/audit-trail',
  matchPaths: ['/coach/audit-trail/people'],
};

export const coachNavItems: SidebarNavItem[] = [
  { id: 'coach-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/coach' },
  {
    id: 'coach-meetings',
    label: 'Meetings',
    icon: 'ri-calendar-event-line',
    href: '/coach/monthly-coaching',
    children: [
      { id: 'coach-monthly-coaching', label: 'Monthly Coaching Meeting', icon: 'ri-calendar-check-line', href: '/coach/monthly-coaching' },
      { id: 'coach-progress-reviews', label: 'Progress Reviews', icon: 'ri-file-chart-line', href: '/coach/progress-reviews' },
      { id: 'coach-reviews', label: 'Review', icon: 'ri-file-list-3-line', href: '/coach/reviews' },
      { id: 'coach-catchup-queue', label: 'Catch-up Queue', icon: 'ri-refresh-line', href: '/coach/catchup-queue' },
      { id: 'coach-timetable', label: 'Calendar', icon: 'ri-calendar-schedule-line', href: '/coach/timetable' },
      { id: 'coach-recordings', label: 'Meetings & Recordings', icon: 'ri-video-line', href: '/coach/recordings' },
    ],
  },
  {
    id: 'coach-attendance',
    label: 'Attendance',
    icon: 'ri-calendar-check-line',
    href: '/coach/attendance',
  },
  {
    id: 'coach-marking-queue',
    label: 'Marking',
    icon: 'ri-edit-line',
    href: '/coach/marking-queue',
  },
  {
    id: 'coach-monthly-logs',
    label: 'Monthly Logs',
    icon: 'ri-file-list-3-line',
    href: '/coach/monthly-logs',
    matchPaths: ['/coach/monthly-cycle'],
  },
  { id: 'coach-inclusion', label: 'Inclusion Ticket System', icon: 'ri-heart-pulse-line', href: inclusionLoginHref, external: true },
  { id: 'coach-support-tickets', label: 'Support Tickets', icon: 'ri-lifebuoy-line', href: '/coach/support-tickets' },
  { id: 'coach-info-source', label: 'Info Source', icon: 'ri-book-open-line', href: '/coach/info-source' },
];

// ============================================================================
// TUTOR WORKSPACE — one page.
//
// The rail used to carry Teaching, Marking & Evidence, Progress, Communication,
// Resources and Reports: sixteen items over eleven pages, every one of them
// rendering mock counters that read from nothing. A queue badge saying 14
// assignments await marking, when no assignment is behind it, is worse than an
// absent page — it looks like work owed.
//
// What the tutor workspace shows now is the two things there is real data for:
// the modules they are assigned to, and their next live session. So the rail is
// the one page plus the shared support links every workspace gets.
//
// The eleven page files and their /tutor/* routes still exist and still resolve
// by URL; they are simply no longer offered here. Nothing links to them.
// ============================================================================
export const tutorNavItems: SidebarNavItem[] = [
  { id: 'tutor-dashboard', label: 'My Teaching', icon: 'ri-presentation-line', href: '/workspace/tutor' },
  { id: 'tutor-messages', label: 'Messages', icon: 'ri-mail-line', href: '/messages' },
];

// ============================================================================
// EMPLOYER WORKSPACE — Grouped sidebar (7 groups)
// ============================================================================
export const employerNavItems: SidebarNavItem[] = [
  // Dashboard
  { id: 'employer-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/employer' },

  // My Apprentices
  {
    id: 'employer-group-apprentices',
    label: 'My Apprentices',
    icon: 'ri-star-line',
    children: [
      { id: 'employer-apprentice-overview', label: 'Apprentice Overview', icon: 'ri-group-line', href: '/employer/apprentices' },
      { id: 'employer-apprentice-progress', label: 'Apprentice Progress', icon: 'ri-bar-chart-line', href: '/employer/apprentice-progress' },
      { id: 'employer-apprentice-risk', label: 'Apprentice Risk', icon: 'ri-alert-line', href: '/employer/apprentice-risk', statusDot: 'red' },
    ],
  },

  // Actions Required
  {
    id: 'employer-group-actions',
    label: 'Actions Required',
    icon: 'ri-alert-line',
    children: [
      { id: 'employer-documents-sign', label: 'Documents to Sign', icon: 'ri-pen-nib-line', href: '/employer/documents', statusDot: 'red' },
      { id: 'employer-workplace-confirm', label: 'Workplace Confirmations', icon: 'ri-building-line', href: '/employer/workplace-confirm', statusDot: 'amber' },
      { id: 'employer-otjh-confirm', label: 'OTJH Confirmations', icon: 'ri-time-line', href: '/employer/otjh-confirm', statusDot: 'amber' },
      { id: 'employer-review-actions', label: 'Review Actions', icon: 'ri-file-chart-line', href: '/employer/review-actions', statusDot: 'red' },
    ],
  },

  // Learning & Progress
  {
    id: 'employer-group-learning',
    label: 'Learning & Progress',
    icon: 'ri-book-open-line',
    children: [
      { id: 'employer-progress-reviews', label: 'Progress Reviews', icon: 'ri-file-chart-line', href: '/employer/progress-reviews' },
      { id: 'employer-ksb-progress', label: 'KSB Progress', icon: 'ri-bar-chart-2-line', href: '/employer/ksb-progress' },
      { id: 'employer-evidence-summary', label: 'Evidence Summary', icon: 'ri-folder-upload-line', href: '/employer/evidence-summary' },
      { id: 'employer-gateway-epa', label: 'Gateway & EPA', icon: 'ri-flag-line', href: '/employer/gateway-epa', statusDot: 'amber' },
    ],
  },

  // Community
  {
    id: 'employer-group-community',
    label: 'Community',
    icon: 'ri-team-line',
    children: [
      { id: 'employer-employer-clubs', label: 'Employer Clubs', icon: 'ri-building-2-line', href: '/employer/employer-clubs' },
      { id: 'employer-learner-clubs', label: 'Learner Clubs', icon: 'ri-team-line', href: '/employer/learner-clubs', statusDot: 'blue' },
      { id: 'employer-events', label: 'Events', icon: 'ri-calendar-event-line', href: '/employer/events', statusDot: 'green' },
      { id: 'employer-community-activity', label: 'Community Activity', icon: 'ri-heart-line', href: '/employer/community-activity', statusDot: 'blue' },
    ],
  },

  // Communication
  {
    id: 'employer-group-communication',
    label: 'Communication',
    icon: 'ri-mail-line',
    children: [
      { id: 'employer-notifications', label: 'Notifications', icon: 'ri-notification-3-line', href: '/notifications' },
      { id: 'employer-messages', label: 'Messages', icon: 'ri-mail-line', href: '/messages', statusDot: 'blue' },
      { id: 'employer-support', label: 'Support Requests', icon: 'ri-question-line', href: '/employer/support' },
    ],
  },

  // Reporting
  { id: 'employer-reports', label: 'Reports', icon: 'ri-bar-chart-box-line', href: '/employer/reports' },
];

// ============================================================================
// CURRICULUM STUDIO — Grouped sidebar
// ============================================================================
export const curriculumNavItems: SidebarNavItem[] = [
  { id: 'curriculumNavItems-coach-directory', label: 'Coach directory', icon: 'ri-team-line', href: '/curriculum/coach-directory' },
  { id: 'curriculum-home', label: 'Home', icon: 'ri-home-5-line', href: '/workspace/curriculum' },
  {
    id: 'curriculum-programmes',
    label: 'Programmes',
    icon: 'ri-stack-line',
    href: '/curriculum/programmes',
  },
  {
    id: 'curriculum-delivery',
    label: 'Delivery',
    icon: 'ri-calendar-schedule-line',
    href: '/curriculum/delivery',
    matchPaths: [
      '/curriculum/module-builder',
      '/curriculum/cohorts',
      '/curriculum/groups',
      '/curriculum/modules',
      '/curriculum/teams-meetings',
      '/curriculum/recordings',
      '/curriculum/session-calendar',
      '/curriculum/england-holidays',
    ],
  },
  {
    id: 'curriculum-coaching-recordings',
    label: 'Coach Meetings & Recordings',
    icon: 'ri-video-line',
    href: '/curriculum/coaching-recordings',
  },
  {
    id: 'curriculum-bulk-attendance',
    label: 'Bulk Attendance',
    icon: 'ri-calendar-check-line',
    href: '/curriculum/bulk-attendance',
  },
  {
    id: 'curriculum-library',
    label: 'Library',
    icon: 'ri-folder-open-line',
    href: '/curriculum/library',
    matchPaths: [
      '/curriculum/week-builder',
      '/curriculum/free-courses',
      '/curriculum/standards',
      '/curriculum/quiz-xml',
      '/curriculum/knowledge-base',
      '/curriculum/question-bank',
      '/curriculum/checkpoints',
    ],
  },
  {
    id: 'curriculum-quality',
    label: 'Quality',
    icon: 'ri-shield-check-line',
    href: '/curriculum/quality',
    matchPaths: [
      '/curriculum/published',
      '/curriculum/reports',
      '/curriculum/audit-trail',
      '/curriculum/ksb-mapping',
      '/curriculum/qa',
      '/curriculum/version-control',
    ],
  },
  {
    // The archive, for the whole of Curriculum rather than per list. Every page
    // that archives something still has its own "View archived" toggle scoped to
    // that list; this is where the four of them are read together, and the only
    // place a record is put back or removed for good without first knowing which
    // list it left.
    id: 'curriculum-archive',
    label: 'Archive',
    icon: 'ri-archive-line',
    href: '/curriculum/archive',
  },
];

// ============================================================================
// ENGAGEMENT COMMAND CENTRE — Grouped sidebar (7 groups)
// ============================================================================
export const engagementNavItems: SidebarNavItem[] = [
  { id: 'engagement-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/engagement' },
  {
    id: 'engagement-group-monitoring',
    label: 'Learner Monitoring',
    icon: 'ri-radar-line',
    href: '',
    children: [
      { id: 'engagement-learner-engagement', label: 'Learner Engagement', icon: 'ri-heart-line', href: '/engagement/learner-engagement' },
      { id: 'engagement-attendance-risk', label: 'Attendance Risk', icon: 'ri-alert-line', href: '/engagement/attendance-risk', badge: 4 },
    ],
  },
  {
    id: 'engagement-group-rewards',
    label: 'Rewards & Recognition',
    icon: 'ri-trophy-line',
    href: '',
    children: [
      { id: 'engagement-points-rules', label: 'Points & Rules', icon: 'ri-gift-2-line', href: '/engagement/points-rules' },
      { id: 'engagement-rewards-shop', label: 'Rewards Shop', icon: 'ri-shopping-bag-3-line', href: '/engagement/rewards-shop' },
      { id: 'engagement-voucher-claims', label: 'Voucher Claims', icon: 'ri-coupon-line', href: '/engagement/voucher-claims', badge: 5 },
      { id: 'engagement-recognition', label: 'Recognition Pages', icon: 'ri-thumb-up-line', href: '/engagement/recognition' },
      { id: 'engagement-flash-cards', label: 'Flash Cards', icon: 'ri-flashlight-line', href: '/engagement/flash-cards' },
    ],
  },
  {
    id: 'engagement-group-community',
    label: 'Community & Events',
    icon: 'ri-team-line',
    href: '',
    children: [
      { id: 'engagement-events', label: 'Events', icon: 'ri-calendar-event-line', href: '/engagement/events' },
      { id: 'engagement-clubs', label: 'Learner Clubs', icon: 'ri-team-line', href: '/engagement/clubs' },
    ],
  },
  {
    id: 'engagement-group-reports',
    label: 'Reports',
    icon: 'ri-bar-chart-box-line',
    href: '',
    children: [
      { id: 'engagement-reports', label: 'Reports', icon: 'ri-bar-chart-box-line', href: '/engagement/reports' },
      { id: 'engagement-feedback', label: 'Feedback', icon: 'ri-survey-line', href: '/engagement/reports/feedback', matchPaths: ['/engagement/reports/feedback/create', '/engagement/reports/feedback/forms'] },
    ],
  },
];

// ============================================================================
// ENROLMENT WORKSPACES — two fully separate tracks.
//
// A single enrolment section — the apprentice track — whose lists cover both
// apprenticeship and commercial learners.
// ============================================================================

/**
 * The one enrolment track: the users directory (all learners) and the wizard.
 * The separate "Delivery / Enrolled learners" list was removed — it re-listed the
 * same learners as the directory, and its programme-status and coach controls now
 * live on the learner's own page (see BoardPage's Programme panel).
 */
export const apprenticeNavItems: SidebarNavItem[] = [
  { id: 'enrolment-progress-transfers', label: 'Transfer Progress', icon: 'ri-arrow-left-right-line', href: '/users/progress-transfers' },
  { id: 'apprenticeNavItems-coach-directory', label: 'Coach directory', icon: 'ri-team-line', href: '/users/coach-directory' },
  { id: 'apprenticeNavItems-first-sessions', label: 'First sessions', icon: 'ri-calendar-check-line', href: '/users/first-sessions' },
  { id: 'apprentice-users', label: 'Users', icon: 'ri-group-line', href: '/users', statusDot: 'blue' },
];

/**
 * Legacy sidebar. Still backs `roleNavMap.compliance`, which the wider
 * compliance workspace pages (ILR, evidence packs, funding risk, …) rely on.
 */
export const enrolmentNavItems: SidebarNavItem[] = [
  { id: 'enrolment-progress-transfers', label: 'Transfer Progress', icon: 'ri-arrow-left-right-line', href: '/users/progress-transfers' },
  { id: 'enrolmentNavItems-coach-directory', label: 'Coach directory', icon: 'ri-team-line', href: '/users/coach-directory' },
  { id: 'enrolmentNavItems-first-sessions', label: 'First sessions', icon: 'ri-calendar-check-line', href: '/users/first-sessions' },
  { id: 'enrolment-users', label: 'Users', icon: 'ri-group-line', href: '/users', statusDot: 'blue' },
];

// ============================================================================
// MIS OPERATIONS CENTRE — Grouped sidebar (8 groups)
// ============================================================================
export const misNavItems: SidebarNavItem[] = [
  { id: 'mis-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/mis' },
  {
    id: 'mis-group-cohort',
    label: 'Cohort Management',
    icon: 'ri-group-line',
    href: '',
    children: [
      { id: 'mis-cohorts', label: 'Cohorts', icon: 'ri-group-line', href: '/mis/cohorts' },
      { id: 'mis-calendar', label: 'Calendar', icon: 'ri-calendar-2-line', href: '/mis/calendar' },
      { id: 'mis-timetables', label: 'Timetables', icon: 'ri-calendar-line', href: '/mis/timetables' },
    ],
  },
  {
    id: 'mis-group-learner-setup',
    label: 'Learner Setup',
    icon: 'ri-user-add-line',
    href: '',
    children: [
      { id: 'mis-learner-allocation', label: 'Learner Allocation', icon: 'ri-user-add-line', href: '/mis/learner-allocation' },
      { id: 'mis-programme-allocation', label: 'Programme Allocation', icon: 'ri-stack-line', href: '/mis/programme-allocation' },
      { id: 'mis-module-allocation', label: 'Module Allocation', icon: 'ri-layout-4-line', href: '/mis/module-allocation' },
    ],
  },
  {
    id: 'mis-group-delivery',
    label: 'Delivery Planning',
    icon: 'ri-calendar-schedule-line',
    href: '',
    children: [
      { id: 'mis-delivery-timeline', label: 'Delivery Timeline', icon: 'ri-bar-chart-horizontal-line', href: '/mis/delivery-timeline' },
      { id: 'mis-delivery-dates', label: 'Delivery Dates', icon: 'ri-timer-line', href: '/mis/delivery-dates' },
      { id: 'mis-teams-sessions', label: 'Teams Sessions', icon: 'ri-video-line', href: '/mis/teams-sessions' },
      { id: 'mis-attendance-modes', label: 'Attendance Modes', icon: 'ri-check-double-line', href: '/mis/attendance-modes' },
    ],
  },
  {
    id: 'mis-group-staff',
    label: 'Staff Allocation',
    icon: 'ri-user-settings-line',
    href: '',
    children: [
      { id: 'mis-coach-assignment', label: 'Coach Assignment', icon: 'ri-heart-line', href: '/mis/coach-assignment' },
      { id: 'mis-tutor-assignment', label: 'Tutor Assignment', icon: 'ri-user-settings-line', href: '/mis/tutor-assignment' },
    ],
  },
  {
    id: 'mis-group-data',
    label: 'Data Operations',
    icon: 'ri-database-2-line',
    href: '',
    children: [
      { id: 'mis-data-quality', label: 'Data Quality', icon: 'ri-database-2-line', href: '/mis/data-quality' },
    ],
  },
  {
    id: 'mis-group-communication',
    label: 'Communication',
    icon: 'ri-mail-line',
    href: '',
    children: [
      { id: 'mis-messages', label: 'Messages', icon: 'ri-mail-line', href: '/messages', badge: 2 },
    ],
  },
  { id: 'mis-reports', label: 'Reporting & Exports', icon: 'ri-bar-chart-box-line', href: '/mis/reports' },
];

// ============================================================================
// QA REVIEW CENTRE — Grouped sidebar (8 groups)
// ============================================================================
export const qaNavItems: SidebarNavItem[] = [
  { id: 'qa-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/qa' },
  {
    id: 'qa-group-onboarding',
    label: 'Onboarding Quality Gates',
    icon: 'ri-user-received-line',
    href: '',
    children: [
      { id: 'qa-onboarding', label: 'Onboarding QA', icon: 'ri-user-received-line', href: '/qa/pre-active', badge: 3 },
      { id: 'qa-employer-contracting', label: 'Employer Contracting QA', icon: 'ri-file-text-line', href: '/qa/employer-contracting', badge: 5 },
      { id: 'qa-eligibility', label: 'Eligibility QA', icon: 'ri-checkbox-circle-line', href: '/qa/eligibility', badge: 6 },
      { id: 'qa-initial-assessment', label: 'Initial Assessment QA', icon: 'ri-clipboard-line', href: '/qa/initial-assessment', badge: 4 },
      { id: 'qa-rpl', label: 'RPL QA', icon: 'ri-file-search-line', href: '/qa/rpl', badge: 3 },
    ],
  },
  {
    id: 'qa-group-curriculum',
    label: 'Curriculum & Delivery QA',
    icon: 'ri-stack-line',
    href: '',
    children: [
      { id: 'qa-module', label: 'Module QA', icon: 'ri-stack-line', href: '/qa/module' },
      { id: 'qa-curriculum', label: 'Curriculum QA', icon: 'ri-shield-check-line', href: '/curriculum/curriculum-qa' },
      { id: 'qa-delivery-setup', label: 'Delivery Setup QA', icon: 'ri-calendar-schedule-line', href: '/qa/delivery-setup' },
    ],
  },
  {
    id: 'qa-group-evidence',
    label: 'Learner Evidence QA',
    icon: 'ri-folder-upload-line',
    href: '',
    children: [
      { id: 'qa-evidence', label: 'Evidence QA', icon: 'ri-folder-upload-line', href: '/qa/evidence', badge: 8 },
      { id: 'qa-otjh', label: 'OTJH QA', icon: 'ri-time-line', href: '/qa/otjh' },
      { id: 'qa-ksb', label: 'KSB QA', icon: 'ri-bar-chart-2-line', href: '/qa/ksb' },
    ],
  },
  {
    id: 'qa-group-reviews',
    label: 'Reviews & Reports QA',
    icon: 'ri-file-chart-line',
    href: '',
    children: [
      { id: 'qa-progress-review', label: 'Progress Review QA', icon: 'ri-file-chart-line', href: '/qa/progress-review', badge: 2 },
      { id: 'qa-report', label: 'Report QA', icon: 'ri-bar-chart-box-line', href: '/qa/report' },
      { id: 'qa-gateway-epa', label: 'Gateway & EPA QA', icon: 'ri-flag-line', href: '/qa/gateway-epa', badge: 4 },
    ],
  },
  {
    id: 'qa-group-operations',
    label: 'QA Operations',
    icon: 'ri-settings-4-line',
    href: '',
    children: [
      { id: 'qa-rejected', label: 'Rejected Items', icon: 'ri-close-circle-line', href: '/qa/rejected', badge: 5 },
      { id: 'qa-escalations', label: 'Escalations', icon: 'ri-alert-line', href: '/qa/escalations', badge: 1 },
      { id: 'qa-sampling', label: 'Sampling', icon: 'ri-pie-chart-2-line', href: '/qa/sampling' },
      { id: 'qa-findings', label: 'QA Findings', icon: 'ri-search-eye-line', href: '/qa/findings' },
    ],
  },
  {
    id: 'qa-group-communication',
    label: 'Communication',
    icon: 'ri-mail-line',
    href: '',
    children: [
      { id: 'qa-messages', label: 'Messages', icon: 'ri-mail-line', href: '/messages', badge: 1 },
    ],
  },
  { id: 'qa-reports', label: 'Reporting & Insights', icon: 'ri-bar-chart-box-line', href: '/qa/reports' },
];

// ============================================================================
// LEADERSHIP INTELLIGENCE CENTRE — 7 grouped sections, 18 items, status dots only
// ============================================================================
export const leadershipNavItems: SidebarNavItem[] = [
  // Dashboard
  { id: 'leadership-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/leadership' },

  // Performance Overview
  {
    id: 'leadership-group-performance',
    label: 'Performance Overview',
    icon: 'ri-bar-chart-box-line',
    children: [
      { id: 'leadership-cohort-performance', label: 'Cohort Performance', icon: 'ri-group-line', href: '/leadership/cohort-performance', statusDot: 'amber' },
      { id: 'leadership-programme-performance', label: 'Programme Performance', icon: 'ri-stack-line', href: '/leadership/programme-performance', statusDot: 'green' },
      { id: 'leadership-learner-progress', label: 'Learner Progress', icon: 'ri-user-line', href: '/leadership/learner-progress', statusDot: 'amber' },
      { id: 'leadership-achievement-pipeline', label: 'Achievement Pipeline', icon: 'ri-flag-line', href: '/leadership/achievement-pipeline', statusDot: 'blue' },
    ],
  },

  // Engagement & Attendance
  {
    id: 'leadership-group-engagement',
    label: 'Engagement & Attendance',
    icon: 'ri-heart-pulse-line',
    children: [
      { id: 'leadership-attendance-trends', label: 'Attendance Trends', icon: 'ri-calendar-check-line', href: '/leadership/attendance-trends', statusDot: 'amber' },
      { id: 'leadership-engagement-trends', label: 'Engagement Trends', icon: 'ri-line-chart-line', href: '/leadership/engagement-trends', statusDot: 'green' },
      { id: 'leadership-employer-engagement', label: 'Employer Engagement', icon: 'ri-building-2-line', href: '/leadership/employer-engagement', statusDot: 'green' },
    ],
  },

  // Learning & Progress
  {
    id: 'leadership-group-learning',
    label: 'Learning & Progress',
    icon: 'ri-book-open-line',
    children: [
      { id: 'leadership-otjh-trends', label: 'OTJH Trends', icon: 'ri-time-line', href: '/leadership/otjh-trends', statusDot: 'amber' },
      { id: 'leadership-ksb-progress', label: 'KSB Progress', icon: 'ri-bar-chart-2-line', href: '/leadership/ksb-progress', statusDot: 'amber' },
      { id: 'leadership-gateway-epa-progress', label: 'Gateway & EPA Progress', icon: 'ri-flag-line', href: '/leadership/gateway-epa-progress', statusDot: 'blue' },
    ],
  },

  // Staff & Delivery
  {
    id: 'leadership-group-staff',
    label: 'Staff & Delivery',
    icon: 'ri-team-line',
    children: [
      { id: 'leadership-tutor-sla', label: 'Tutor SLA', icon: 'ri-user-settings-line', href: '/leadership/tutor-sla', statusDot: 'green' },
      { id: 'leadership-coach-workload', label: 'Coach Workload', icon: 'ri-heart-line', href: '/leadership/coach-workload', statusDot: 'amber' },
      { id: 'leadership-delivery-performance', label: 'Delivery Performance', icon: 'ri-presentation-line', href: '/leadership/delivery-performance', statusDot: 'green' },
    ],
  },

  // Quality & Compliance
  {
    id: 'leadership-group-quality',
    label: 'Quality & Compliance',
    icon: 'ri-shield-check-line',
    children: [
      { id: 'leadership-compliance-risk', label: 'Compliance Risk', icon: 'ri-shield-line', href: '/leadership/compliance-risk', statusDot: 'red' },
      { id: 'leadership-qa-sampling', label: 'QA Sampling', icon: 'ri-pie-chart-2-line', href: '/leadership/qa-sampling', statusDot: 'amber' },
      { id: 'leadership-ofsted', label: 'Ofsted Evidence', icon: 'ri-government-line', href: '/leadership/ofsted', statusDot: 'blue' },
      { id: 'leadership-sar-qip', label: 'SAR/QIP Evidence', icon: 'ri-file-text-line', href: '/leadership/sar-qip', statusDot: 'blue' },
    ],
  },

  // Communication
  {
    id: 'leadership-group-communication',
    label: 'Communication',
    icon: 'ri-mail-line',
    children: [
      { id: 'leadership-messages', label: 'Messages', icon: 'ri-mail-line', href: '/messages', statusDot: 'blue' },
    ],
  },

  // Reporting & Insights
  { id: 'leadership-reports', label: 'Reports', icon: 'ri-bar-chart-box-line', href: '/leadership/reports' },
];

// ============================================================================
// SUPER ADMIN WORKSPACE — 8 grouped sections, 20 items, status dots only
// ============================================================================
// Every entry below points at a screen backed by a real table. Screens for
// things this platform does not have — a tenant estate, a rules engine behind
// "automations", a form builder, an AI mode store — were removed rather than
// left rendering fixtures, so the sidebar is now a map of what exists.
export const adminNavItems: SidebarNavItem[] = [
  // Dashboard
  { id: 'admin-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/admin' },
  { id: 'admin-coach-directory', label: 'Coach directory', icon: 'ri-team-line', href: '/admin/coach_directory' },

  // User & Access Control — login."Login_accounts" and the four real roles
  {
    id: 'admin-group-users',
    label: 'User & Access Control',
    icon: 'ri-shield-user-line',
    children: [
      { id: 'admin-users', label: 'Accounts', icon: 'ri-user-settings-line', href: '/admin/users' },
      { id: 'admin-roles', label: 'Roles', icon: 'ri-shield-check-line', href: '/admin/roles' },
      { id: 'admin-permissions', label: 'Permissions', icon: 'ri-key-2-line', href: '/admin/permissions' },
      { id: 'admin-access-logs', label: 'Access Logs', icon: 'ri-door-lock-line', href: '/admin/access-logs' },
      // The system-wide Audit Trail. Access Logs above answers "who signed in";
      // this answers "who was here, what did they open, and what did they change".
      { id: 'admin-audit-trail', label: 'Audit Trail', icon: 'ri-history-line', href: '/admin/audit-trail', matchPaths: ['/admin/audit-trail/people'] },
    ],
  },

  // Platform — documents, outbound email, subsystem readiness
  {
    id: 'admin-group-platform',
    label: 'Platform',
    icon: 'ri-settings-4-line',
    children: [
      { id: 'admin-documents', label: 'Documents', icon: 'ri-folder-line', href: '/admin/documents' },
      { id: 'admin-evidence', label: 'Evidence', icon: 'ri-folder-shield-2-line', href: '/admin/evidence' },
      { id: 'admin-certificates', label: 'Certificates', icon: 'ri-award-line', href: '/workspace/admin/certificates' },
      { id: 'admin-notifications', label: 'Email Delivery', icon: 'ri-mail-send-line', href: '/admin/notifications' },
      { id: 'admin-system', label: 'System Status', icon: 'ri-pulse-line', href: '/admin/system' },
    ],
  },

  // Every headline count in one place, each row naming its source table.
  { id: 'admin-platform-report', label: 'Platform Report', icon: 'ri-bar-chart-box-line', href: '/admin/platform-report' },

  // College sites outside the LMS — open in a new tab.
  {
    id: 'admin-group-external',
    label: 'External tools',
    icon: 'ri-external-link-line',
    children: [
      { id: 'admin-positive-moments', label: 'Positive Moments', icon: 'ri-heart-line', href: 'https://positive-moments.kentbusinesscollege.net', external: true },
      { id: 'admin-tutor-dashboard', label: 'Tutor Dashboard', icon: 'ri-dashboard-line', href: 'https://tutordashboard.kentbusinesscollege.net', external: true },
    ],
  },
  { id: 'admin-inclusion', label: 'Inclusion System', icon: 'ri-heart-pulse-line', href: inclusionLoginHref, external: true },
];

// ============================================================================
// FINANCE WORKSPACE — 6 items
// ============================================================================
export const financeNavItems: SidebarNavItem[] = [
  { id: 'finance-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/finance' },
  { id: 'finance-funding', label: 'Funding Overview', icon: 'ri-money-pound-circle-line', href: '/finance/funding' },
  { id: 'finance-invoices', label: 'Invoicing', icon: 'ri-bill-line', href: '/finance/invoices' },
  { id: 'finance-payments', label: 'Payments', icon: 'ri-money-pound-circle-line', href: '/finance/payments' },
  { id: 'finance-budgets', label: 'Budgets', icon: 'ri-pie-chart-2-line', href: '/finance/budgets' },
  { id: 'finance-messages', label: 'Messages', icon: 'ri-mail-line', href: '/messages' },
  { id: 'finance-reports', label: 'Reports', icon: 'ri-bar-chart-box-line', href: '/finance/reports' },
];

// ============================================================================
// AUDITOR WORKSPACE — 6 items
// ============================================================================
export const auditorNavItems: SidebarNavItem[] = [
  { id: 'auditor-live-audit', label: 'Audit', icon: 'ri-file-search-line', href: '/workspace/auditor' },
];

// ============================================================================
// ROLE-TO-NAVIGATION MAP — 13 workspaces
// ============================================================================
// ============================================================================
// SUPPORT CENTRE — 6 items, grouped
// ============================================================================
export const supportNavItems: SidebarNavItem[] = [
  { id: 'support-dashboard', label: 'Dashboard', icon: 'ri-dashboard-line', href: '/workspace/support' },
  {
    id: 'support-group-tickets',
    label: 'Ticket Management',
    icon: 'ri-ticket-line',
    children: [
      { id: 'support-ticket-queue', label: 'Ticket Queue', icon: 'ri-ticket-line', href: '/support/ticket-queue', statusDot: 'red' },
      { id: 'support-my-tickets', label: 'My Tickets', icon: 'ri-user-line', href: '/support/my-tickets', statusDot: 'amber' },
      { id: 'support-resolved', label: 'Resolved', icon: 'ri-check-double-line', href: '/support/resolved', statusDot: 'green' },
    ],
  },
  {
    id: 'support-group-escalations',
    label: 'Escalations',
    icon: 'ri-alert-line',
    children: [
      { id: 'support-escalations', label: 'Escalations', icon: 'ri-alert-line', href: '/support/escalations', statusDot: 'red' },
    ],
  },
  {
    id: 'support-group-communication',
    label: 'Communication',
    icon: 'ri-mail-line',
    children: [
      { id: 'support-messages', label: 'Messages', icon: 'ri-mail-line', href: '/messages', statusDot: 'blue' },
    ],
  },
  { id: 'support-reports', label: 'Reports', icon: 'ri-bar-chart-box-line', href: '/support/reports' },
  { id: 'support-knowledge-base', label: 'Knowledge Base', icon: 'ri-book-read-line', href: '/support/knowledge-base', statusDot: 'green' },
];

// ============================================================================
// SAFEGUARDING WORKSPACE — Restricted | DSL, Deputy DSL, Safeguarding Officers
// ============================================================================
export const safeguardingNavItems: SidebarNavItem[] = [
  { id: 'sg-dashboard', label: 'Dashboard', icon: 'ri-shield-line', href: '/workspace/safeguarding', statusDot: 'red' },
  {
    id: 'sg-group-cases',
    label: 'Safeguarding Cases',
    icon: 'ri-folder-shield-2-line',
    href: '',
    children: [
      { id: 'sg-open-cases', label: 'Open Cases', icon: 'ri-folder-open-line', href: '/safeguarding/open-cases', statusDot: 'red' },
      { id: 'sg-new-concerns', label: 'New Concerns', icon: 'ri-alert-line', href: '/safeguarding/new-concerns', statusDot: 'amber' },
      { id: 'sg-high-risk-cases', label: 'High-Risk Cases', icon: 'ri-error-warning-line', href: '/safeguarding/high-risk-cases', statusDot: 'red' },
      { id: 'sg-closed-cases', label: 'Closed Cases', icon: 'ri-check-double-line', href: '/safeguarding/closed-cases', statusDot: 'green' },
    ],
  },
  {
    id: 'sg-group-wellbeing',
    label: 'Learner Wellbeing',
    icon: 'ri-heart-pulse-line',
    href: '',
    children: [
      { id: 'sg-wellbeing-concerns', label: 'Wellbeing Concerns', icon: 'ri-heart-line', href: '/safeguarding/learner-wellbeing', statusDot: 'amber' },
      { id: 'sg-support-needs', label: 'Support Needs', icon: 'ri-hand-heart-line', href: '/safeguarding/learner-wellbeing', statusDot: 'blue' },
      { id: 'sg-vulnerable-learners', label: 'Vulnerable Learners', icon: 'ri-user-heart-line', href: '/safeguarding/learner-wellbeing', statusDot: 'red' },
      { id: 'sg-follow-up-actions', label: 'Follow-up Actions', icon: 'ri-task-line', href: '/safeguarding/learner-wellbeing', statusDot: 'amber' },
    ],
  },
  {
    id: 'sg-group-referrals',
    label: 'Referrals & Escalations',
    icon: 'ri-share-forward-line',
    href: '',
    children: [
      { id: 'sg-internal-escalations', label: 'Internal Escalations', icon: 'ri-arrow-up-circle-line', href: '/safeguarding/referrals', statusDot: 'red' },
      { id: 'sg-external-referrals', label: 'External Referrals', icon: 'ri-external-link-line', href: '/safeguarding/referrals', statusDot: 'amber' },
      { id: 'sg-employer-concerns', label: 'Employer Concerns', icon: 'ri-building-2-line', href: '/safeguarding/referrals', statusDot: 'blue' },
      { id: 'sg-emergency-actions', label: 'Emergency Actions', icon: 'ri-alarm-warning-line', href: '/safeguarding/referrals', statusDot: 'red' },
    ],
  },
  {
    id: 'sg-group-prevent',
    label: 'Prevent & Risk',
    icon: 'ri-radar-line',
    href: '',
    children: [
      { id: 'sg-prevent-concerns', label: 'Prevent Concerns', icon: 'ri-eye-line', href: '/safeguarding/prevent-risk', statusDot: 'red' },
      { id: 'sg-risk-assessments', label: 'Risk Assessments', icon: 'ri-file-warning-line', href: '/safeguarding/prevent-risk', statusDot: 'amber' },
      { id: 'sg-safety-plans', label: 'Safety Plans', icon: 'ri-shield-check-line', href: '/safeguarding/prevent-risk', statusDot: 'blue' },
    ],
  },
  {
    id: 'sg-group-communication',
    label: 'Communication',
    icon: 'ri-lock-line',
    href: '',
    children: [
      { id: 'sg-secure-notes', label: 'Secure Notes', icon: 'ri-file-lock-line', href: '/safeguarding/communication', statusDot: 'amber' },
      { id: 'sg-messages', label: 'Messages', icon: 'ri-mail-line', href: '/safeguarding/communication', statusDot: 'blue' },
      { id: 'sg-contact-log', label: 'Contact Log', icon: 'ri-phone-line', href: '/safeguarding/communication', statusDot: 'green' },
    ],
  },
  {
    id: 'sg-group-qa',
    label: 'QA & Audit',
    icon: 'ri-file-search-line',
    href: '',
    children: [
      { id: 'sg-case-reviews', label: 'Case Reviews', icon: 'ri-search-eye-line', href: '/safeguarding/qa-audit', statusDot: 'blue' },
      { id: 'sg-safeguarding-audit', label: 'Safeguarding Audit', icon: 'ri-history-line', href: '/safeguarding/qa-audit', statusDot: 'amber' },
      { id: 'sg-policy-records', label: 'Policy Records', icon: 'ri-file-text-line', href: '/safeguarding/qa-audit', statusDot: 'green' },
    ],
  },
  { id: 'sg-reports', label: 'Reporting', icon: 'ri-bar-chart-box-line', href: '/safeguarding/reports', statusDot: 'blue' },
];

// The chat-backed "Messages" pages. When CHAT_ENABLED is false these entry
// points are stripped from every role's sidebar (audit A10 — chat disabled).
// Note: /safeguarding/communication is a separate feature and is NOT listed.
const CHAT_NAV_HREFS = new Set(['/messages', '/learner/messages']);

function stripChatNavItems(items: SidebarNavItem[]): SidebarNavItem[] {
  if (CHAT_ENABLED) return items;
  return items
    .filter(item => !(item.href && CHAT_NAV_HREFS.has(item.href)))
    .map(item => (item.children ? { ...item, children: stripChatNavItems(item.children) } : item));
}

const baseRoleNavMap: Record<string, { items: SidebarNavItem[]; label: string; workspaceLabel: string }> = {
  'advanced-admin': { items: [{ id: 'advanced-admin-learners', label: 'Learners', href: '/workspace/advanced-admin', icon: 'ri-team-line' }], label: 'Advanced Admin', workspaceLabel: 'Learner Review' },
  'record-monitor': { items: [{ id: 'record-monitor', label: 'Record monitoring', href: '/old-otjh/monitor', icon: 'ri-dashboard-line' }], label: 'Record monitor', workspaceLabel: 'Learning record monitoring' },
  learner: { items: learnerNavItems, label: 'Learner', workspaceLabel: 'Learner Workspace' },
  coach: { items: coachNavItems, label: 'Coach', workspaceLabel: 'Coach Workspace' },
  tutor: { items: tutorNavItems, label: 'Tutor', workspaceLabel: 'Tutor Workspace' },
  employer: { items: employerNavItems, label: 'Employer', workspaceLabel: 'Employer Workspace' },
  curriculum: { items: curriculumNavItems, label: 'Curriculum Designer', workspaceLabel: 'Curriculum Studio' },
  engagement: { items: engagementNavItems, label: 'Engagement Manager', workspaceLabel: 'Engagement Command Centre' },
  compliance: { items: enrolmentNavItems, label: 'Enrolment Officer', workspaceLabel: 'Enrolment Workspace' },
  apprentice: { items: apprenticeNavItems, label: 'Enrolment Officer', workspaceLabel: 'Enrolment' },
  mis: { items: misNavItems, label: 'MIS Operations', workspaceLabel: 'MIS Operations Centre' },
  qa: { items: qaNavItems, label: 'QA Officer', workspaceLabel: 'QA Review Centre' },
  leadership: { items: leadershipNavItems, label: 'Senior Leadership', workspaceLabel: 'Leadership Intelligence Centre' },
  admin: { items: adminNavItems, label: 'Super Admin', workspaceLabel: 'Super Admin Workspace' },
  finance: { items: financeNavItems, label: 'Finance', workspaceLabel: 'Finance Workspace' },
  auditor: { items: auditorNavItems, label: 'Auditor', workspaceLabel: 'Auditor Workspace' },
  support: { items: supportNavItems, label: 'Support', workspaceLabel: 'Support Centre' },
  safeguarding: { items: safeguardingNavItems, label: 'Safeguarding', workspaceLabel: 'Safeguarding Workspace' },
};

export const roleNavMap: Record<string, { items: SidebarNavItem[]; label: string; workspaceLabel: string }> =
  Object.fromEntries(
    Object.entries(baseRoleNavMap).map(([role, cfg]) => [role, { ...cfg, items: stripChatNavItems(cfg.items) }]),
  );
