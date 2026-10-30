"""Per-learner counts of what a coach has accepted and rejected.

Four columns on ``Learner.learners`` -- accepted/rejected assignments and
accepted/rejected reflections -- so a learner's marking record can be read
without aggregating the submissions table on every screen that wants it.

Recomputed, never incremented
-----------------------------
Each recount reads the current state of ``learning_reflection_submissions`` and
writes the totals. A coach can change a decision, a submission can be resubmitted
and marked again, and rows are edited outside this path; a counter that was
nudged up on each decision would drift away from the truth and there would be no
way to tell that it had. Recomputing is a few rows per learner and always agrees
with the submissions themselves.

Assignment or reflection is decided by the component's authored type, matching
``coach_api.views.ASSIGNMENT_ACTIVITY_TYPES`` and the SPA's
``frontend/src/lib/markingKind.ts`` -- keep the three in step.
"""
import logging

from django.db import DatabaseError, connections

logger = logging.getLogger(__name__)

#: Component types assessed as a work product. Mirrors
#: coach_api.views.ASSIGNMENT_ACTIVITY_TYPES.
ASSIGNMENT_TYPES = ("assignment",)

#: Coach decisions that count as accepted, and as rejected. 'partial' counts as
#: accepted because the learner was awarded something; 'escalated' and
#: 'submitted_for_tutor_review' are neither -- nobody has decided yet.
ACCEPTED_STATUSES = ("accepted", "partial")
REJECTED_STATUSES = ("rejected", "referred")

TALLY_COLUMNS = (
    "accepted_assignments",
    "rejected_assignments",
    "accepted_reflections",
    "rejected_reflections",
)


def _learner_ids_for_profile(cur, profile_id):
    """Every id a learner's submissions might be filed under.

    Submissions are keyed on the enrolment id the learner page uses, but older
    rows carry the profile id, so both are searched -- otherwise a learner's
    earlier marked work would not be counted.
    """
    cur.execute(
        'select id::text, enrolment_id::text from "Learner".learners where id = %s',
        [profile_id],
    )
    row = cur.fetchone()
    if not row:
        return []
    return sorted({value for value in row if value})


def compute_tally(profile_id):
    """The learner's marking counts, read from the submissions themselves.

    Returns a dict keyed by TALLY_COLUMNS, or None when the counts could not be
    read -- the caller then leaves the stored values alone rather than writing
    zeroes over a real record.
    """
    try:
        with connections["enrolment"].cursor() as cur:
            learner_ids = _learner_ids_for_profile(cur, profile_id)
            if not learner_ids:
                return None

            cur.execute(
                """
                select coalesce(lower(btrim(c.type)), lower(btrim(s.activity_type))) as kind,
                       s.status,
                       count(*)
                  from "Learner"."learning_reflection_submissions" s
                  left join curriculum.components c on c.id = s.activity_id
                 where s.learner_id::text = any(%s)
                 group by 1, 2
                """,
                [learner_ids],
            )
            rows = cur.fetchall()
    except DatabaseError as exc:
        logger.warning("Could not compute the marking tally for %s: %s", profile_id, exc)
        return None

    tally = {column: 0 for column in TALLY_COLUMNS}
    for activity_kind, status, count in rows:
        status = str(status or "").strip().lower()
        is_assignment = str(activity_kind or "").strip().lower() in ASSIGNMENT_TYPES
        if status in ACCEPTED_STATUSES:
            key = "accepted_assignments" if is_assignment else "accepted_reflections"
        elif status in REJECTED_STATUSES:
            key = "rejected_assignments" if is_assignment else "rejected_reflections"
        else:
            # Still waiting on a coach; counted in neither.
            continue
        tally[key] += int(count or 0)
    return tally


def refresh_tally(profile_id):
    """Recompute and store one learner's counts. Returns the tally, or None.

    Never raises: this runs after a coach has already saved their decision, and
    a counter that could not be updated must not turn a successful marking into
    an error the coach sees.
    """
    tally = compute_tally(profile_id)
    if tally is None:
        return None
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                'update "Learner".learners set '
                + ", ".join(f"{column} = %s" for column in TALLY_COLUMNS)
                + " where id = %s",
                [*(tally[column] for column in TALLY_COLUMNS), profile_id],
            )
    except DatabaseError as exc:
        logger.warning("Could not store the marking tally for %s: %s", profile_id, exc)
        return None
    return tally


def refresh_tally_for_submission(learner_kind, learner_id):
    """Recompute for whichever profile a submission's learner_id refers to.

    The marking queue holds the id the submission was filed under, which may be
    an enrolment id; the counters live on the profile, so it is resolved here
    rather than at each call site.
    """
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                'select id from "Learner".learners '
                "where id::text = %s or enrolment_id::text = %s "
                "order by id limit 1",
                [str(learner_id), str(learner_id)],
            )
            row = cur.fetchone()
    except DatabaseError as exc:
        logger.warning("Could not resolve learner %s for the tally: %s", learner_id, exc)
        return None
    if not row:
        return None
    return refresh_tally(row[0])
