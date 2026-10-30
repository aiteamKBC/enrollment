"""Every SPA route in the LMS, as the page it is and the workspace it belongs to.

Why this is a table and not a guess
-----------------------------------
The audit trail's page names are resolved here, on the server, from the URL
alone. The browser reports the route it navigated to and nothing else: it does
not get to name the page, because a client that could would be able to write
whatever it liked into an audit record. It also does not get to name the record
id -- that is read out of the URL by the matcher below, so "opened cohort 412"
means the URL said 412.

An unrecognised path is still recorded. It is labelled from its last segment and
attributed to the workspace its first segment names, so a page that ships
tomorrow appears in the trail tomorrow rather than on the day somebody remembers
to add it here. The table is what makes the label *readable*, not what makes the
row exist.

Excluded routes
---------------
``EXCLUDED`` is the short list of routes that are deliberately never recorded:

* The signed-out pages. There is no account to attribute them to, and
  ``login."Login_audit"`` already records every sign-in, sign-out, reset and
  failed attempt properly, including the ones against addresses with no account.
* The learner content runner -- a quiz, a video, a component, a historical
  assignment, a monthly submission step. A learner working through material
  would produce one page open per item and swamp the table, and it would say
  less about their learning than their own progress records already say. Their
  reading of the rest of the workspace is still recorded; it is the runner
  itself that is not.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------- workspaces

# key -> label. The key is what the audit trail filters on and what is stored
# beside each row; the label is what a reader sees.
WORKSPACES = {
    'curriculum': 'Curriculum Studio',
    'coach': 'Coach',
    'tutor': 'Tutor',
    'learner': 'Learner',
    'employer': 'Employer',
    'engagement': 'Engagement',
    'mis': 'MIS',
    'leadership': 'Leadership',
    'qa': 'Quality Assurance',
    'safeguarding': 'Safeguarding',
    'support': 'Support',
    'finance': 'Finance',
    'admin': 'Administration',
    'enrolment': 'Enrolment',
    'audit': 'Audit',
    'record-monitor': 'Record Monitor',
    'platform': 'Platform',
}

# First URL segment -> workspace, where the two differ. Anything whose first
# segment is already a workspace key needs no entry.
SEGMENT_WORKSPACE = {
    'employers': 'employer',
    'users': 'enrolment',
    'internal-panel': 'admin',
    'activity-categories': 'audit',
    'old-otjh': 'record-monitor',
    'my-courses': 'learner',
    'training-plan': 'learner',
}

# Paths with no workspace of their own: the shell around all of them.
PLATFORM_ROOTS = {
    '', 'home', 'messages', 'starred-messages', 'notifications', 'tasks',
    'communication', 'user-guide', 'choose-workspace', 'access-required',
}

# ----------------------------------------------------------------- excluded

# Prefixes that are never recorded. See the module docstring for why each is
# here; this is the whole list, and adding to it is a decision about what the
# audit trail stops being able to answer.
EXCLUDED = (
    # Signed out. Login_audit owns this half properly.
    '/login',
    '/forgot-password',
    '/reset-password',
    '/set-password',
    '/verify-certificate',
    '/verify-personal-certificate',
    # Public coach booking pages have no signed-in account to attribute.
    '/coach-booking/',
    # The learner content runner. One row per quiz question or video is noise,
    # and the learner's own progress records say it better.
    '/learner/quiz/',
    '/learner/video/',
    '/learner/component/',
    '/learner/historical-assignment/',
    '/learner/monthly-submission/',
    '/old-otjh',
)


def excluded(path):
    """True when this route is deliberately not recorded anywhere."""
    path = clean_path(path)
    for prefix in EXCLUDED:
        if prefix.endswith('/'):
            if path.startswith(prefix.rstrip('/') + '/'):
                return True
            continue
        if path == prefix or path.startswith(f'{prefix}/'):
            return True
    return False


# -------------------------------------------------------------- page tables

# (template, page_key, label, target_type, target_param)
#
# `{name}` is a wildcard segment. `target_param` names the one that holds the
# record id -- empty where the route names no single record. Order inside a
# table does not matter: the matcher prefers the template that pins down the
# most literal segments, so `/curriculum/quiz-xml/manual` beats
# `/curriculum/quiz-xml/{id}/edit` without anybody having to sort the list.

CURRICULUM = (
    ('/curriculum', 'overview', 'Curriculum overview', '', ''),
    ('/curriculum/programmes', 'programmes', 'Programmes', '', ''),
    ('/curriculum/programmes/{id}', 'programme-workspace', 'Programme workspace', 'programme', 'id'),
    ('/curriculum/cohorts', 'cohorts', 'Cohorts', '', ''),
    ('/curriculum/cohorts/{id}', 'cohort-workspace', 'Cohort workspace', 'cohort', 'id'),
    ('/curriculum/cohorts/{id}/allocate', 'cohort-allocate', 'Cohort allocation', 'cohort', 'id'),
    ('/curriculum/groups', 'groups', 'Groups', '', ''),
    ('/curriculum/groups/{id}', 'group-workspace', 'Group workspace', 'group', 'id'),
    ('/curriculum/modules', 'modules', 'Modules', '', ''),
    ('/curriculum/modules/{id}', 'module-workspace', 'Module workspace', 'module', 'id'),
    ('/curriculum/module-builder', 'module-builder', 'Module builder', '', ''),
    ('/curriculum/week-builder', 'week-builder', 'Week builder', '', ''),
    ('/curriculum/library', 'library', 'Content library', '', ''),
    ('/curriculum/delivery', 'delivery', 'Delivery', '', ''),
    ('/curriculum/free-courses', 'free-courses', 'Free courses', '', ''),
    ('/curriculum/standards', 'standards', 'Standards', '', ''),
    ('/curriculum/standards/{id}', 'standard-detail', 'Standard', 'standard', 'id'),
    ('/curriculum/ksb-frameworks', 'ksb-frameworks', 'KSB frameworks', '', ''),
    ('/curriculum/ksb-mapping', 'ksb-mapping', 'KSB mapping', '', ''),
    ('/curriculum/quiz-xml', 'quiz-xml', 'Quiz XML', '', ''),
    ('/curriculum/quiz-xml/manual', 'quiz-xml-manual', 'Quiz XML (manual)', '', ''),
    ('/curriculum/quiz-xml/{id}/edit', 'quiz-edit', 'Quiz editor', 'quiz', 'id'),
    ('/curriculum/question-bank', 'question-bank', 'Question bank', '', ''),
    ('/curriculum/checkpoints', 'checkpoints', 'Checkpoints', '', ''),
    ('/curriculum/quality', 'quality', 'Quality', '', ''),
    ('/curriculum/qa', 'qa', 'QA', '', ''),
    ('/curriculum/published', 'published', 'Published', '', ''),
    ('/curriculum/reports', 'reports', 'Reports', '', ''),
    ('/curriculum/version-control', 'version-control', 'Version control', '', ''),
    ('/curriculum/audit-trail', 'audit-trail', 'Audit trail', '', ''),
    ('/curriculum/audit-trail/people/{id}', 'audit-trail-person', 'Audit trail: one person', 'person', 'id'),
    ('/curriculum/teams-meetings', 'teams-meetings', 'Teams calendar', '', ''),
    ('/curriculum/session-calendar', 'session-calendar', 'Session calendar', '', ''),
    ('/curriculum/england-holidays', 'england-holidays', 'England holidays', '', ''),
    ('/curriculum/hubs', 'hubs', 'Curriculum hubs', '', ''),
)

COACH = (
    ('/coach', 'coach-home', 'Coach home', '', ''),
    ('/coach/caseload', 'caseload', 'Caseload', '', ''),
    ('/coach/case-files', 'case-files', 'Case files', '', ''),
    ('/coach/learner-case-file', 'learner-case-file', 'Learner case file', '', ''),
    ('/coach/timetable', 'timetable', 'Timetable', '', ''),
    ('/coach/attendance', 'attendance', 'Attendance', '', ''),
    ('/coach/attendance/{learnerId}', 'attendance-learner', 'Attendance: one learner', 'learner', 'learnerId'),
    ('/coach/absence-reports', 'absence-reports', 'Absence reports', '', ''),
    ('/coach/marking-queue', 'marking-queue', 'Marking queue', '', ''),
    ('/coach/marking-queue/{submissionId}', 'marking-review', 'Marking review', 'submission', 'submissionId'),
    ('/coach/ai-marking', 'ai-marking', 'AI marking', '', ''),
    ('/coach/catchup-queue', 'catchup-queue', 'Catch-up queue', '', ''),
    ('/coach/monthly-cycle', 'monthly-cycle', 'Monthly cycle', '', ''),
    ('/coach/monthly-logs', 'monthly-logs', 'Monthly logs', '', ''),
    ('/coach/monthly-logs/{learnerId}', 'monthly-log-learner', 'Monthly log: one learner', 'learner', 'learnerId'),
    ('/coach/monthly-logs/{learnerId}/{month}', 'monthly-log-month', 'Monthly log: one month', 'learner', 'learnerId'),
    ('/coach/monthly-reports', 'monthly-reports', 'Monthly reports', '', ''),
    ('/coach/meetings', 'meetings', 'Meetings', '', ''),
    ('/coach/meetings/{eventKey}', 'meeting-detail', 'Meeting', 'meeting', 'eventKey'),
    ('/coach/progress-reviews', 'progress-reviews', 'Progress reviews', '', ''),
    ('/coach/progress-reviews/{eventKey}', 'progress-review-detail', 'Progress review', 'review', 'eventKey'),
    ('/coach/reviews/{eventKey}', 'review-detail', 'Review', 'review', 'eventKey'),
    ('/coach/evidence-validation', 'evidence-validation', 'Evidence validation', '', ''),
    ('/coach/ksb-impact', 'ksb-impact', 'KSB impact', '', ''),
    ('/coach/otjh-reports', 'otjh-reports', 'OTJH reports', '', ''),
    ('/coach/reports', 'coach-reports', 'Coach reports', '', ''),
    ('/coach/audit-trail', 'coach-audit-trail', 'Audit trail', '', ''),
    ('/coach/audit-trail/people/{id}', 'coach-audit-trail-person', 'Audit trail: one person', 'person', 'id'),
)

TUTOR = (
    ('/tutor/learners', 'tutor-learners', 'Learners', '', ''),
    ('/tutor/sessions', 'tutor-sessions', 'Sessions', '', ''),
    ('/tutor/assignment-marking', 'assignment-marking', 'Assignment marking', '', ''),
    ('/tutor/ai-marking', 'tutor-ai-marking', 'AI marking', '', ''),
    ('/tutor/feedback-queue', 'feedback-queue', 'Feedback queue', '', ''),
    ('/tutor/evidence-review', 'evidence-review', 'Evidence review', '', ''),
    ('/tutor/ksb-validation', 'ksb-validation', 'KSB validation', '', ''),
    ('/tutor/otjh-validation', 'otjh-validation', 'OTJH validation', '', ''),
    ('/tutor/quiz-results', 'quiz-results', 'Quiz results', '', ''),
    ('/tutor/resources', 'tutor-resources', 'Resources', '', ''),
    ('/tutor/reports', 'tutor-reports', 'Tutor reports', '', ''),
)

LEARNER = (
    ('/learner', 'learner-home', 'Learner home', '', ''),
    ('/learner/home', 'learner-home', 'Learner home', '', ''),
    ('/learner/my-learning', 'my-learning', 'My learning', '', ''),
    ('/learner/my-learning/{kind}/{id}', 'my-learning-course', 'My learning: one course', 'course', 'id'),
    ('/learner/modules', 'learner-modules', 'Modules', '', ''),
    ('/learner/modules/{kind}/{id}', 'learner-module', 'Module', 'course', 'id'),
    ('/learner/learning-plan', 'learning-plan', 'Learning plan', '', ''),
    ('/learner/learning-plan/{kind}/{id}', 'learning-plan-course', 'Learning plan: one course', 'course', 'id'),
    ('/learner/learning-plan/modules', 'learning-plan-modules', 'Learning plan modules', '', ''),
    ('/learner/learning-plan/modules/{kind}/{id}', 'learning-plan-module', 'Learning plan module', 'course', 'id'),
    ('/learner/training-plan', 'training-plan', 'Training plan', '', ''),
    ('/learner/training-plan/{kind}/{id}', 'training-plan-course', 'Training plan: one course', 'course', 'id'),
    ('/learner/training-plan-timeline', 'training-plan-timeline', 'Training plan timeline', '', ''),
    ('/learner/training-plan-timeline/{kind}/{id}', 'training-plan-timeline-course', 'Training plan timeline: one course', 'course', 'id'),
    ('/learner/quizzes', 'learner-quizzes', 'Quizzes', '', ''),
    ('/learner/quizzes/{kind}/{id}', 'learner-quizzes-course', 'Quizzes: one course', 'course', 'id'),
    ('/learner/evidence', 'learner-evidence', 'Evidence', '', ''),
    ('/learner/evidence/{kind}/{id}', 'learner-evidence-course', 'Evidence: one course', 'course', 'id'),
    ('/learner/ksbs', 'learner-ksbs', 'KSBs', '', ''),
    ('/learner/ksbs/{kind}/{id}', 'learner-ksbs-course', 'KSBs: one course', 'course', 'id'),
    ('/learner/otjh', 'learner-otjh', 'Off-the-job hours', '', ''),
    ('/learner/otjh/{kind}/{id}', 'learner-otjh-course', 'Off-the-job hours: one course', 'course', 'id'),
    ('/learner/monthly-cycle', 'learner-monthly-cycle', 'Monthly cycle', '', ''),
    ('/learner/monthly-cycle/{kind}/{id}', 'learner-monthly-cycle-course', 'Monthly cycle: one course', 'course', 'id'),
    ('/learner/monthly-logs', 'learner-monthly-logs', 'Monthly logs', '', ''),
    ('/learner/monthly-logs/{month}', 'learner-monthly-log-month', 'Monthly log: one month', '', ''),
    ('/learner/monthly-logs/{kind}/{id}', 'learner-monthly-log-course', 'Monthly log: one course', 'course', 'id'),
    ('/learner/monthly-logs/{kind}/{id}/{month}', 'learner-monthly-log-course-month', 'Monthly log: one month', 'course', 'id'),
    ('/learner/monthly-coaching', 'monthly-coaching', 'Monthly coaching', '', ''),
    ('/learner/monthly-coaching/{sessionId}', 'monthly-coaching-session', 'Monthly coaching session', 'session', 'sessionId'),
    ('/learner/monthly-submission', 'monthly-submission', 'Monthly submission', '', ''),
    ('/learner/progress-reviews', 'learner-progress-reviews', 'Progress reviews', '', ''),
    ('/learner/progress-reviews/{reviewId}', 'learner-progress-review', 'Progress review', 'review', 'reviewId'),
    ('/learner/onboarding', 'learner-onboarding', 'Onboarding', '', ''),
    ('/learner/onboarding/{stepSlug}', 'learner-onboarding-step', 'Onboarding step', '', ''),
    ('/learner/onboarding/reviews', 'learner-onboarding-reviews', 'Onboarding reviews', '', ''),
    ('/learner/onboarding/reviews/{eventKey}', 'learner-onboarding-review', 'Onboarding review', 'review', 'eventKey'),
    ('/learner/attendance', 'learner-attendance', 'Attendance', '', ''),
    ('/learner/report-absence', 'report-absence', 'Report an absence', '', ''),
    ('/learner/catchup', 'learner-catchup', 'Catch-up', '', ''),
    ('/learner/calendar', 'learner-calendar', 'Calendar', '', ''),
    ('/learner/week/{weekNumber}', 'learner-week', 'One week', 'week', 'weekNumber'),
    ('/learner/gateway', 'learner-gateway', 'Gateway', '', ''),
    ('/learner/flash-cards', 'learner-flash-cards', 'Flash cards', '', ''),
    ('/learner/knowledge-base', 'learner-knowledge-base', 'Knowledge base', '', ''),
    ('/learner/clubs', 'learner-clubs', 'Clubs', '', ''),
    ('/learner/clubs/events', 'learner-club-events', 'Club events', '', ''),
    ('/learner/clubs/events/schedule', 'learner-club-events-schedule', 'Club event schedule', '', ''),
    ('/learner/clubs/events/{eventId}', 'learner-club-event', 'Club event', 'event', 'eventId'),
    ('/learner/clubs/badge/{badgeId}', 'learner-club-badge', 'Club badge', 'badge', 'badgeId'),
    ('/learner/clubs/discussion/{discussionId}', 'learner-club-discussion', 'Club discussion', 'discussion', 'discussionId'),
    ('/learner/clubs/{clubId}', 'learner-club', 'Club', 'club', 'clubId'),
    ('/learner/rewards', 'learner-rewards', 'Rewards', '', ''),
    ('/learner/rewards/badge/{badgeId}', 'learner-reward-badge', 'Reward badge', 'badge', 'badgeId'),
    ('/learner/compliance-documents', 'compliance-documents', 'Compliance documents', '', ''),
    ('/learner/messages', 'learner-messages', 'Messages', '', ''),
    ('/learner/support', 'learner-support', 'Support', '', ''),
    ('/learner/profile', 'learner-profile', 'Profile', '', ''),
)

RECORD_MONITOR = (
    ('/old-otjh', 'record-monitor-home', 'Record Monitor', '', ''),
    ('/old-otjh/months', 'record-monitor-months', 'Record Monitor months', '', ''),
    ('/old-otjh/months/{month}', 'record-monitor-month', 'Record Monitor month', 'month', 'month'),
    ('/old-otjh/coach', 'record-monitor-coaches', 'Record Monitor coaches', '', ''),
    ('/old-otjh/monitor', 'record-monitor-dashboard', 'Record Monitor dashboard', '', ''),
    ('/old-otjh/coach/{aptemId}', 'record-monitor-learner', 'Record Monitor learner', 'learner', 'aptemId'),
    ('/old-otjh/coach/{aptemId}/months', 'record-monitor-learner-months', 'Record Monitor learner months', 'learner', 'aptemId'),
    ('/old-otjh/coach/{aptemId}/months/{month}', 'record-monitor-learner-month', 'Record Monitor learner month', 'learner', 'aptemId'),
)

EMPLOYER = (
    ('/employer/apprentices', 'employer-apprentices', 'Apprentices', '', ''),
    ('/employer/apprentice-progress', 'apprentice-progress', 'Apprentice progress', '', ''),
    ('/employer/apprentice-risk', 'apprentice-risk', 'Apprentice risk', '', ''),
    ('/employer/progress-reviews', 'employer-progress-reviews', 'Progress reviews', '', ''),
    ('/employer/review-actions', 'review-actions', 'Review actions', '', ''),
    ('/employer/otjh-confirm', 'otjh-confirm', 'Confirm off-the-job hours', '', ''),
    ('/employer/workplace-confirm', 'workplace-confirm', 'Workplace confirmation', '', ''),
    ('/employer/evidence-summary', 'evidence-summary', 'Evidence summary', '', ''),
    ('/employer/ksb-progress', 'employer-ksb-progress', 'KSB progress', '', ''),
    ('/employer/gateway-epa', 'employer-gateway-epa', 'Gateway and EPA', '', ''),
    ('/employer/documents', 'employer-documents', 'Documents', '', ''),
    ('/employer/events', 'employer-events', 'Events', '', ''),
    ('/employer/employer-clubs', 'employer-clubs', 'Employer clubs', '', ''),
    ('/employer/learner-clubs', 'employer-learner-clubs', 'Learner clubs', '', ''),
    ('/employer/community-activity', 'community-activity', 'Community activity', '', ''),
    ('/employer/reports', 'employer-reports', 'Employer reports', '', ''),
    ('/employer/support', 'employer-support', 'Support', '', ''),
    ('/employers/{employerId}', 'employer-workspace', 'Employer workspace', 'employer', 'employerId'),
    ('/employers/{employerId}/learner/{kind}/{learnerId}', 'employer-learner', 'Employer: one learner', 'learner', 'learnerId'),
)

ENGAGEMENT = (
    ('/engagement/learner-engagement', 'learner-engagement', 'Learner engagement', '', ''),
    ('/engagement/attendance-risk', 'attendance-risk', 'Attendance risk', '', ''),
    ('/engagement/call-logs', 'call-logs', 'Call logs', '', ''),
    ('/engagement/email-logs', 'email-logs', 'Email logs', '', ''),
    ('/engagement/whatsapp-logs', 'whatsapp-logs', 'WhatsApp logs', '', ''),
    ('/engagement/clubs', 'engagement-clubs', 'Clubs', '', ''),
    ('/engagement/events', 'engagement-events', 'Events', '', ''),
    ('/engagement/flash-cards', 'engagement-flash-cards', 'Flash cards', '', ''),
    ('/engagement/points-rules', 'points-rules', 'Points rules', '', ''),
    ('/engagement/recognition', 'recognition', 'Recognition', '', ''),
    ('/engagement/rewards-shop', 'rewards-shop', 'Rewards shop', '', ''),
    ('/engagement/voucher-claims', 'voucher-claims', 'Voucher claims', '', ''),
    ('/engagement/reports', 'engagement-reports', 'Engagement reports', '', ''),
)

MIS = (
    ('/mis/cohorts', 'mis-cohorts', 'Cohorts', '', ''),
    ('/mis/calendar', 'mis-calendar', 'Calendar', '', ''),
    ('/mis/timetables', 'timetables', 'Timetables', '', ''),
    ('/mis/delivery-dates', 'delivery-dates', 'Delivery dates', '', ''),
    ('/mis/delivery-timeline', 'delivery-timeline', 'Delivery timeline', '', ''),
    ('/mis/teams-sessions', 'teams-sessions', 'Teams sessions', '', ''),
    ('/mis/attendance-modes', 'attendance-modes', 'Attendance modes', '', ''),
    ('/mis/learner-allocation', 'learner-allocation', 'Learner allocation', '', ''),
    ('/mis/module-allocation', 'module-allocation', 'Module allocation', '', ''),
    ('/mis/programme-allocation', 'programme-allocation', 'Programme allocation', '', ''),
    ('/mis/coach-assignment', 'coach-assignment', 'Coach assignment', '', ''),
    ('/mis/tutor-assignment', 'tutor-assignment', 'Tutor assignment', '', ''),
    ('/mis/data-quality', 'data-quality', 'Data quality', '', ''),
    ('/mis/reports', 'mis-reports', 'MIS reports', '', ''),
)

LEADERSHIP = (
    ('/leadership/learner-progress', 'learner-progress', 'Learner progress', '', ''),
    ('/leadership/cohort-performance', 'cohort-performance', 'Cohort performance', '', ''),
    ('/leadership/programme-performance', 'programme-performance', 'Programme performance', '', ''),
    ('/leadership/delivery-performance', 'delivery-performance', 'Delivery performance', '', ''),
    ('/leadership/achievement-pipeline', 'achievement-pipeline', 'Achievement pipeline', '', ''),
    ('/leadership/attendance-trends', 'attendance-trends', 'Attendance trends', '', ''),
    ('/leadership/engagement-trends', 'engagement-trends', 'Engagement trends', '', ''),
    ('/leadership/otjh-trends', 'otjh-trends', 'OTJH trends', '', ''),
    ('/leadership/ksb-progress', 'leadership-ksb-progress', 'KSB progress', '', ''),
    ('/leadership/gateway-epa-progress', 'gateway-epa-progress', 'Gateway and EPA progress', '', ''),
    ('/leadership/coach-workload', 'coach-workload', 'Coach workload', '', ''),
    ('/leadership/tutor-sla', 'tutor-sla', 'Tutor SLA', '', ''),
    ('/leadership/employer-engagement', 'employer-engagement', 'Employer engagement', '', ''),
    ('/leadership/compliance-risk', 'compliance-risk', 'Compliance risk', '', ''),
    ('/leadership/qa-sampling', 'leadership-qa-sampling', 'QA sampling', '', ''),
    ('/leadership/sar-qip', 'sar-qip', 'SAR and QIP', '', ''),
    ('/leadership/ofsted', 'ofsted', 'Ofsted', '', ''),
    ('/leadership/reports', 'leadership-reports', 'Leadership reports', '', ''),
)

QA = (
    ('/qa/pre-active', 'qa-pre-active', 'Pre-active', '', ''),
    ('/qa/eligibility', 'qa-eligibility', 'Eligibility', '', ''),
    ('/qa/initial-assessment', 'qa-initial-assessment', 'Initial assessment', '', ''),
    ('/qa/rpl', 'qa-rpl', 'RPL', '', ''),
    ('/qa/employer-contracting', 'qa-employer-contracting', 'Employer contracting', '', ''),
    ('/qa/delivery-setup', 'qa-delivery-setup', 'Delivery setup', '', ''),
    ('/qa/module', 'qa-module', 'Module QA', '', ''),
    ('/qa/ksb', 'qa-ksb', 'KSB QA', '', ''),
    ('/qa/otjh', 'qa-otjh', 'OTJH QA', '', ''),
    ('/qa/evidence', 'qa-evidence', 'Evidence QA', '', ''),
    ('/qa/progress-review', 'qa-progress-review', 'Progress review QA', '', ''),
    ('/qa/gateway-epa', 'qa-gateway-epa', 'Gateway and EPA QA', '', ''),
    ('/qa/sampling', 'qa-sampling', 'Sampling', '', ''),
    ('/qa/findings', 'qa-findings', 'Findings', '', ''),
    ('/qa/escalations', 'qa-escalations', 'Escalations', '', ''),
    ('/qa/rejected', 'qa-rejected', 'Rejected', '', ''),
    ('/qa/report', 'qa-report', 'QA report', '', ''),
    ('/qa/reports', 'qa-reports', 'QA reports', '', ''),
)

SAFEGUARDING = (
    ('/safeguarding/new-concerns', 'new-concerns', 'New concerns', '', ''),
    ('/safeguarding/open-cases', 'open-cases', 'Open cases', '', ''),
    ('/safeguarding/closed-cases', 'closed-cases', 'Closed cases', '', ''),
    ('/safeguarding/high-risk-cases', 'high-risk-cases', 'High-risk cases', '', ''),
    ('/safeguarding/prevent-risk', 'prevent-risk', 'Prevent risk', '', ''),
    ('/safeguarding/referrals', 'referrals', 'Referrals', '', ''),
    ('/safeguarding/learner-wellbeing', 'learner-wellbeing', 'Learner wellbeing', '', ''),
    ('/safeguarding/communication', 'safeguarding-communication', 'Communication', '', ''),
    ('/safeguarding/qa-audit', 'safeguarding-qa-audit', 'QA audit', '', ''),
    ('/safeguarding/reports', 'safeguarding-reports', 'Safeguarding reports', '', ''),
)

SUPPORT = (
    ('/support/ticket-queue', 'ticket-queue', 'Ticket queue', '', ''),
    ('/support/my-tickets', 'my-tickets', 'My tickets', '', ''),
    ('/support/resolved', 'support-resolved', 'Resolved tickets', '', ''),
    ('/support/escalations', 'support-escalations', 'Escalations', '', ''),
    ('/support/knowledge-base', 'support-knowledge-base', 'Knowledge base', '', ''),
    ('/support/reports', 'support-reports', 'Support reports', '', ''),
)

FINANCE = (
    ('/finance/funding', 'funding', 'Funding', '', ''),
    ('/finance/invoices', 'invoices', 'Invoices', '', ''),
    ('/finance/payments', 'payments', 'Payments', '', ''),
    ('/finance/budgets', 'budgets', 'Budgets', '', ''),
    ('/finance/reports', 'finance-reports', 'Finance reports', '', ''),
)

ADMIN = (
    ('/admin/users', 'admin-users', 'Accounts', '', ''),
    ('/admin/roles', 'admin-roles', 'Roles', '', ''),
    ('/admin/permissions', 'admin-permissions', 'Permissions', '', ''),
    ('/admin/access-logs', 'access-logs', 'Access logs', '', ''),
    ('/admin/documents', 'admin-documents', 'Documents', '', ''),
    ('/admin/evidence', 'admin-evidence', 'Evidence', '', ''),
    ('/admin/evidence/{learnerId}', 'admin-evidence-learner', 'Evidence: one learner', 'learner', 'learnerId'),
    ('/admin/certificates', 'admin-certificates', 'Certificates', '', ''),
    ('/admin/notifications', 'admin-notifications', 'Email delivery', '', ''),
    ('/admin/system', 'admin-system', 'System status', '', ''),
    ('/admin/platform-report', 'platform-report', 'Platform report', '', ''),
    ('/admin/audit-trail', 'system-audit-trail', 'Audit trail', '', ''),
    ('/admin/audit-trail/people/{id}', 'system-audit-trail-person', 'Audit trail: one person', 'person', 'id'),
    ('/users', 'users-board', 'User board', '', ''),
    ('/users/{userId}', 'user-record', 'User record', 'user', 'userId'),
    ('/users/{userId}/wizard', 'user-wizard', 'User setup wizard', 'user', 'userId'),
    ('/users/{userId}/wizard/{stepSlug}', 'user-wizard-step', 'User setup wizard step', 'user', 'userId'),
    ('/internal-panel', 'internal-panel', 'Internal panel', '', ''),
)

AUDIT = (
    ('/audit/activity-categories', 'activity-categories', 'Activity categories', '', ''),
    ('/audit/activity-categories/{kind}/{id}', 'activity-category', 'Activity category', 'category', 'id'),
    ('/activity-categories', 'activity-categories', 'Activity categories', '', ''),
    ('/activity-categories/{kind}/{id}', 'activity-category', 'Activity category', 'category', 'id'),
    ('/workspace/auditor', 'auditor-workspace', 'Auditor workspace', '', ''),
    ('/workspace/auditor/learner/{auditLearnerId}', 'auditor-learner', 'Auditor: one learner', 'learner', 'auditLearnerId'),
    ('/workspace/auditor-copy', 'auditor-workspace-copy', 'Auditor workspace (copy)', '', ''),
    ('/workspace/auditor-manual', 'auditor-workspace-manual', 'Auditor workspace (manual)', '', ''),
    ('/workspace/auditor-hours-test', 'auditor-workspace-hours-test', 'Auditor workspace (hours test)', '', ''),
    ('/workspace/audit', 'audit-workspace', 'Audit workspace', '', ''),
)

PLATFORM = (
    ('/', 'root', 'Entry', '', ''),
    ('/home', 'home', 'Home', '', ''),
    ('/messages', 'messages', 'Messages', '', ''),
    ('/starred-messages', 'starred-messages', 'Starred messages', '', ''),
    ('/notifications', 'notifications', 'Notifications', '', ''),
    ('/tasks', 'tasks', 'Tasks', '', ''),
    ('/communication', 'communication', 'Communication', '', ''),
    ('/user-guide', 'user-guide', 'User guide', '', ''),
    ('/choose-workspace', 'choose-workspace', 'Choose workspace', '', ''),
    ('/access-required', 'access-required', 'Access required', '', ''),
    ('/my-courses', 'my-courses', 'My courses', '', ''),
    ('/training-plan/{kind}/{userId}', 'training-plan-user', 'Training plan: one person', 'user', 'userId'),
)

# The workspace dashboards. One route each, all the same shape, so they are
# built rather than typed out -- and built from WORKSPACES so a workspace added
# above cannot end up with a dashboard the trail calls by its slug.
WORKSPACE_HOMES = tuple(
    (f'/workspace/{key}', f'{key}-workspace-home', f'{label} dashboard', '', '')
    for key, label in WORKSPACES.items()
    if key != 'platform'
) + (
    ('/workspace/admin/certificates', 'admin-workspace-certificates', 'Certificates', '', ''),
)

# workspace key -> its routes. Used both by the matcher and by the audit trail's
# workspace filter, which is why it is one structure rather than a flat list.
PAGES_BY_WORKSPACE = {
    'curriculum': CURRICULUM,
    'coach': COACH,
    'tutor': TUTOR,
    'learner': LEARNER,
    'employer': EMPLOYER,
    'engagement': ENGAGEMENT,
    'mis': MIS,
    'leadership': LEADERSHIP,
    'qa': QA,
    'safeguarding': SAFEGUARDING,
    'support': SUPPORT,
    'finance': FINANCE,
    'admin': tuple(route for route in ADMIN if not route[0].startswith('/users')),
    'enrolment': tuple(route for route in ADMIN if route[0].startswith('/users')),
    'audit': AUDIT,
    'record-monitor': RECORD_MONITOR,
    'platform': PLATFORM,
}


def _compile():
    """One flat list of (segments, workspace, key, label, target_type, target_param)."""
    compiled = []
    for workspace, routes in PAGES_BY_WORKSPACE.items():
        for template, key, label, target_type, target_param in routes:
            segments = tuple(part for part in template.split('/') if part)
            compiled.append((segments, workspace, key, label, target_type, target_param))
    for template, key, label, target_type, target_param in WORKSPACE_HOMES:
        segments = tuple(part for part in template.split('/') if part)
        compiled.append((segments, workspace_for(template), key, label, target_type, target_param))
    # Most literal segments first, so a fixed path always beats a wildcard of the
    # same length: `/curriculum/quiz-xml/manual` is matched before
    # `/curriculum/quiz-xml/{id}/edit` without anybody having to order the lists.
    compiled.sort(key=lambda item: -sum(1 for part in item[0] if not part.startswith('{')))
    return tuple(compiled)


_SAFE_SEGMENT = re.compile(r'^[A-Za-z0-9@._:%+-]{1,120}$')


def clean_path(value):
    """A reported route as a bare, leading-slash path with no query or fragment."""
    text = str(value or '').strip()[:300]
    text = text.split('?')[0].split('#')[0]
    if text and not text.startswith('/'):
        text = f'/{text}'
    text = text.rstrip('/')
    return text or '/'


def workspace_for(path):
    """Which workspace a path belongs to, from its first segment alone."""
    segments = [part for part in clean_path(path).split('/') if part]
    if not segments:
        return 'platform'
    head = segments[0].lower()
    if head in PLATFORM_ROOTS:
        return 'platform'
    # `/workspace/coach` is the coach's own dashboard, not a page of some
    # "workspace" area. Its second segment names the workspace it opens.
    if head == 'workspace' and len(segments) > 1:
        role = segments[1].lower()
        if role.startswith('auditor'):
            role = 'audit'
        return role if role in WORKSPACES else 'platform'
    if head in SEGMENT_WORKSPACE:
        return SEGMENT_WORKSPACE[head]
    return head if head in WORKSPACES else 'platform'


_COMPILED = _compile()


def resolve(path):
    """A URL as the page it is, the workspace it is in, and the record it names.

    Everything in the answer is read from the URL here on the server. An
    unrecognised path still resolves -- to its own workspace and a label built
    from its last segment -- because a page that has not been added to the table
    above is still a page somebody opened.
    """
    path = clean_path(path)
    segments = [part for part in path.split('/') if part]
    for template, workspace, key, label, target_type, target_param in _COMPILED:
        if len(template) != len(segments):
            continue
        captured = {}
        matched = True
        for expected, actual in zip(template, segments):
            if expected.startswith('{') and expected.endswith('}'):
                captured[expected[1:-1]] = actual
                continue
            if expected.lower() != actual.lower():
                matched = False
                break
        if not matched:
            continue
        target_id = captured.get(target_param, '') if target_param else ''
        if target_id and not _SAFE_SEGMENT.match(target_id):
            target_id = ''
        return {
            'workspace': workspace,
            'workspaceLabel': WORKSPACES.get(workspace, workspace.title()),
            'pageKey': key,
            'pageLabel': label,
            'targetType': target_type if target_id else '',
            'targetId': target_id[:120],
            'path': path,
            'routePattern': '/' + '/'.join(template),
            'known': True,
        }
    workspace = workspace_for(path)
    tail = segments[-1] if segments else 'home'
    return {
        'workspace': workspace,
        'workspaceLabel': WORKSPACES.get(workspace, workspace.title()),
        'pageKey': tail[:60],
        'pageLabel': tail.replace('-', ' ').replace('_', ' ').title(),
        'targetType': '',
        'targetId': '',
        'path': path,
        'routePattern': '',
        'known': False,
    }


# Auditors need every workspace, including ones they do not personally use.
FILTERABLE_WORKSPACES = tuple(WORKSPACES)


def workspace_options():
    """The workspaces, for the audit trail's filter."""
    return [{'value': key, 'label': WORKSPACES[key]} for key in FILTERABLE_WORKSPACES]
