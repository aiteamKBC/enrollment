"""Canonical option lists for enrolment users.

These are the authoritative server-side choices. The frontend mirrors them in its
create form; the API validates against them so bad values can't be written even if
a client bypasses the UI.
"""

STATUS_CHOICES = [
    "FullUser",
    "Invited",
    "Prospect",
    "Expired",
    "Cancelled",
    "Archived",
]

TYPE_CHOICES = [
    "User",
    "Employer",
    "Referrer",
    "Admin",
    "Caseowner",
]

# Which kind of learner a row in enrolment."Created_users" is. Both kinds share
# that one table, and this column is the discriminator.
LEARNER_TYPE_CHOICES = [
    "apprenticeship",
    "commercial",
]

# Whether a learner's sessions are recorded, asked on the create/edit learner
# form and stored on enrolment."Created_users"."Attendance_type". Optional, so
# blank is allowed; a CHECK constraint on the column holds the same two values
# (see apply_created_users_attendance_type).
ATTENDANCE_TYPE_CHOICES = [
    "Recorded",
    "Not recorded",
]

# Staff positions, asked for at the foot of the "Create admin" form and stored on
# enrolment."Staff_users"."Position". Validated in the API rather than by a DB
# check constraint, so this list can grow without DDL.
POSITION_CHOICES = [
    "Caseowner",
    "Admin",
    "Enrolment",
    "Curriculum team",
    "Operations team",
    "Tutor",
]

# --------------------------------------------------------------------------- #
# staff access levels                                                          #
# --------------------------------------------------------------------------- #
# What a staff account may reach. Stored in enrolment."Staff_users"."Access"
# (see apply_staff_access_column) and enforced by
# ``login.permissions.require_access`` — not merely used to pick a landing page.
#
# This exists because Position no longer decides permissions. Every account the
# console creates is Position='Admin', so without a separate grant every staff
# member would hold full platform access. Access is that grant, and it is the
# only thing narrowing what an account can do.
#
# An account may hold MORE THAN ONE, so somebody who both coaches a caseload and
# teaches a group reaches both workspaces from one sign-in. 'super-admin' is the
# exception: it already means "everything", so it is held alone.
#
# "Access" holds the PRIMARY grant — where the account lands at sign-in, and the
# value every pre-existing reader compares against. "Access_extra" holds any
# others, comma-separated. Splitting them this way keeps a single-access row
# byte-for-byte what it was: turning "Access" itself into a list would make
# every `access == "coach"` check in the codebase false for a dual-access
# account, locking them out of the very workspace they were granted.
#
# `identity.accesses_for_staff` is the one place that unions the two; nothing
# else should read "Access_extra" directly.
ACCESS_ENROLMENT = "enrolment"
ACCESS_CURRICULUM = "curriculum"
ACCESS_COACH = "coach"
ACCESS_TUTOR = "tutor"
ACCESS_SUPER_ADMIN = "super-admin"
ACCESS_ADVANCED_ADMIN = "advanced-admin"
ACCESS_RECORD_MONITOR = "record-monitor"
ACCESS_SUPPORT = "support"

#: Canonical order — also the order the console offers them in.
ACCESS_CHOICES = [
    ACCESS_SUPPORT,
    ACCESS_ENROLMENT,
    ACCESS_CURRICULUM,
    ACCESS_COACH,
    ACCESS_TUTOR,
    ACCESS_RECORD_MONITOR,
    ACCESS_ADVANCED_ADMIN,
    ACCESS_SUPER_ADMIN,
]

#: Human labels for the console. Kept beside the values so the two cannot drift.
ACCESS_LABELS = {
    ACCESS_SUPPORT: "Support access",
    ACCESS_ENROLMENT: "Enrolment access",
    ACCESS_CURRICULUM: "Curriculum access",
    ACCESS_COACH: "Coach access",
    ACCESS_TUTOR: "Tutor access",
    ACCESS_RECORD_MONITOR: "Learning record monitoring",
    ACCESS_ADVANCED_ADMIN: "Advanced Admin access",
    ACCESS_SUPER_ADMIN: "Super Admin access",
}

#: What each access is for, shown in the console's access picker.
ACCESS_DESCRIPTIONS = {
    ACCESS_SUPPORT: "Review and respond to learner support tickets in the LMS.",
    ACCESS_ENROLMENT: (
        "The enrolment workspace and every learner record. Together with Super "
        "Admin, the only access that can change enrolment data."
    ),
    ACCESS_CURRICULUM: "The curriculum workspace — programmes, modules and their content.",
    ACCESS_COACH: "The coach workspace — their caseload, reviews and evidence validation.",
    ACCESS_TUTOR: "The tutor workspace — their teaching groups and session delivery.",
    ACCESS_RECORD_MONITOR: "Read-only monitoring of active enrolled learners, previous records and signatures.",
    ACCESS_ADVANCED_ADMIN: "The assigned PCP and ME learner review workspace and assessment marking.",
    ACCESS_SUPER_ADMIN: "Everything, including this console. Can edit any data on the platform.",
}

#: Where each access lands after signing in. The SPA reads this from the account
#: payload rather than keeping its own copy, so one edit here moves the landing
#: page and the permission together.
#:
#: Enrolment lands on /users, not a /workspace/* route: the enrolment console is
#: the user directory (its whole sidebar is the single "Users" item), so that
#: page *is* the workspace.
#: Where a staff account with **no** access lands. Not a workspace: it explains
#: that an administrator has to grant them one, and offers a button that asks.
NO_ACCESS_ROUTE = "/access-required"

ACCESS_HOME_ROUTES = {
    ACCESS_SUPPORT: "/support/ticket-queue",
    ACCESS_ENROLMENT: "/users",
    ACCESS_CURRICULUM: "/workspace/curriculum",
    ACCESS_COACH: "/workspace/coach",
    ACCESS_TUTOR: "/workspace/tutor",
    ACCESS_RECORD_MONITOR: "/old-otjh/monitor",
    ACCESS_ADVANCED_ADMIN: "/workspace/advanced-admin",
    ACCESS_SUPER_ADMIN: "/workspace/admin",
}

#: Which SPA sidebar (``roleNavMap`` key) each access gets. 'compliance' is the
#: enrolment officer's nav — the key predates the rename and is what the map
#: still uses.
ACCESS_NAV_ROLES = {
    ACCESS_SUPPORT: "support",
    ACCESS_ENROLMENT: "compliance",
    ACCESS_CURRICULUM: "curriculum",
    ACCESS_COACH: "coach",
    ACCESS_TUTOR: "tutor",
    ACCESS_RECORD_MONITOR: "record-monitor",
    ACCESS_ADVANCED_ADMIN: "advanced-admin",
    ACCESS_SUPER_ADMIN: "admin",
}

# Organisation record status, from the organisation form's Status dropdown.
# Validated in the API rather than by a DB check constraint, as above.
ORGANISATION_STATUS_CHOICES = [
    "Confirmed",
    "Opportunity",
    "Archived",
]

# Whether the organisation pays the apprenticeship levy. "Not selected" is the
# form's own default and is stored as-is rather than as NULL, so an untouched
# dropdown is distinguishable from a deliberate blank.
LEVY_PAYER_CHOICES = [
    "Not selected",
    "Yes",
    "No",
]

# The organisation's health & safety risk rating, not a pass/fail outcome.
HEALTH_SAFETY_CHOICES = [
    "Not known",
    "Low",
    "Medium",
    "High",
]

# What kind of group an organisation is. The Employer Group picker shows this in
# its own column; every row in the reference screenshots is an Employer.
ORGANISATION_GROUP_TYPE_CHOICES = [
    "Employer",
    "Provider",
    "Sub-contractor",
    "Other",
]

# The programme lifecycle, in the order a learner moves through it. Only these
# values can be written; rows created before this list was trimmed may still
# hold older statuses, which display as-is but can no longer be selected.
PROGRAMME_STATUS_CHOICES = [
    # Account exists but the learner hasn't been put into the enrolment flow
    # yet. Also the display fallback when no status has been set at all.
    "Fresh user",
    # Learner is filling in their own enrolment wizard. While a learner sits at
    # this status their /workspace/learner landing page redirects them to
    # /learner/onboarding instead of the normal overview.
    "Onboarding",
    "Delivery",
    "Ready to enrol",
    "Active",
    "Withdrawn",
    "On break",
    "Completed",
]

# Shown for a learner whose programme status has never been set.
DEFAULT_PROGRAMME_STATUS = "Fresh user"

# Reached automatically once all three onboarding reviews are signed by every
# party (see learning_plan.promote_to_delivery_if_ready). A learner at this
# status has a learning plan to build, so the users table offers it there.
DELIVERY_PROGRAMME_STATUS = "Delivery"
