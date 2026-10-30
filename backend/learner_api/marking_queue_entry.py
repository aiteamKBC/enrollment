"""Put a completed activity in front of its coach.

Some components are authored with ``tutor_validation_required`` -- assignments,
mostly. The learner finishes them like any other activity, but finishing is a
hand-in rather than a completion: the work is only done once a coach has looked
at it and accepted it.

The marking queue reads ``Learner.learning_reflection_submissions``, so that is
what has to exist for the coach to see anything. A reflection flow writes one of
those when the learner fills it in, but an assignment authored with
``reflection_required = false`` has no such flow -- so completing it left the
learner with a green tick and the coach with an empty queue.

This writes the row directly from the completion, carrying the evidence the
learner uploaded. It is deliberately minimal: no reflection text, no KSB
explanations, because the learner was never asked for them. What the coach needs
is the work, which is the evidence file.

Upserted on the same key the reflection flow uses
(learner_kind, learner_id, activity_type, activity_id, assignment_topic_id), so a learner who
re-completes an activity updates their submission rather than creating a second
one -- and a coach who has already accepted it is not silently reset, since an
accepted submission is left alone.
"""
import json
import logging
import uuid
from contextlib import nullcontext

from django.db import DatabaseError, connections, transaction

from .assignment_hours import lock_assignment_hours
from .submission_audit import SUBMISSION_AUDIT_SQL, record_submission_row
from .submission_visibility import ACTIVITY_MARKING_REFLECTION

logger = logging.getLogger(__name__)

#: A coach decision that must not be undone by the learner re-completing the
#: activity. Anything else is reopened for review.
TERMINAL_STATUSES = ("accepted",)


def _actual_time_hours(value):
    """Normalise decimal hours and the legacy component ``MM:SS`` clock."""
    if value in (None, ""):
        return ""
    if isinstance(value, str) and ":" in value:
        parts = value.split(":")
        if len(parts) == 2:
            try:
                minutes, seconds = (int(part) for part in parts)
            except ValueError:
                return ""
            if minutes >= 0 and 0 <= seconds < 60:
                return (minutes * 60 + seconds) / 3600
            return ""
    try:
        return float(value)
    except (TypeError, ValueError):
        return ""


def requires_tutor_validation(component_id):
    """Whether the author said this activity must be validated by a coach."""
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                "select coalesce(tutor_validation_required, false) "
                "from curriculum.components where id = %s",
                [component_id],
            )
            row = cur.fetchone()
    except DatabaseError as exc:
        logger.warning("Could not read tutor validation flag for %s: %s", component_id, exc)
        return False
    return bool(row[0]) if row else False


def _evidence_filenames(kind, learner_id, component_id):
    """Names of the approved files the learner submitted for this activity."""
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                'select original_filename from "Learner"."evidence_files" '
                "where section_ref = %s and learner_kind = %s and learner_id = %s "
                "and status = 'approved' order by uploaded_at",
                [component_id, kind, str(learner_id)],
            )
            return [str(row[0]) for row in cur.fetchall() if row and row[0]]
    except DatabaseError as exc:
        logger.warning("Could not list evidence for %s: %s", component_id, exc)
        return []


def queue_for_marking(*, component_id, kind, learner_id, context):
    """Create or refresh this activity's marking submission. Best-effort.

    ``context`` carries what the completion already knows -- learner name,
    programme, titles, planned and actual time -- so the coach's queue row reads
    like any other. Returns the queued/existing submitted id, or None if no
    submitted work is available. Topic work is owned by the assignment form.

    Never raises: the learner has completed their work, and a queue row that
    could not be written must not turn that into an error they see.
    """
    assignment = context.get("activityType") == "assignment"
    try:
        with (transaction.atomic(using="enrolment") if assignment else nullcontext()), connections["enrolment"].cursor() as cur:
            if assignment:
                # Topic saves use the same transaction lock, including their
                # first insert. Completion/reward retries must not create an
                # empty legacy submission alongside the learner's topic work.
                lock_assignment_hours(cur, kind, learner_id)
                cur.execute(
                    '''SELECT id, status FROM "Learner".learning_reflection_submissions
                       WHERE learner_kind=%s AND learner_id=%s AND activity_type='assignment'
                         AND activity_id=%s AND assignment_topic_id <> ''
                       ORDER BY CASE status WHEN 'accepted' THEN 0
                           WHEN 'submitted_for_tutor_review' THEN 1 ELSE 2 END,
                           assignment_topic_id LIMIT 1''',
                    [kind, str(learner_id), component_id],
                )
                topic = cur.fetchone()
                if topic:
                    # A draft/revision still belongs to the assignment form;
                    # generic completion must not submit or reopen it.
                    return topic[0] if topic[1] in ("accepted", "submitted_for_tutor_review") else None
            # Only a committed, owner-bound registered mobile submission may
            # reopen this activity for a NEW explicit progress row. Other callers
            # keep the historical accepted-row behaviour.
            shared_progress = context.get("sharedRewardProgressId")
            scoped_refresh = False
            if type(shared_progress) is int and shared_progress > 0:
                cur.execute(
                    'SELECT 1 FROM "Engagement".shared_reward_outbox o '
                    'JOIN "Engagement".shared_reward_sessions s ON s.session_id=o.session_id '
                    'JOIN "Learner".learner_progress_entries p ON p.id=o.progress_id '
                    'WHERE o.progress_id=%s AND s.component_id=%s AND s.learner_id=%s '
                    'AND s.learner_kind=%s AND s.profile_id=p.learner_id '
                    'AND s.module_id=%s AND p.time_tracking_session_ref=s.session_id '
                    'UNION ALL SELECT 1 FROM "Engagement".shared_reward_saved_progress j '
                    'JOIN "Learner".learner_progress_entries p ON p.id=j.progress_id '
                    'JOIN "Learner".learners l ON l.id=p.learner_id '
                    'WHERE j.progress_id=%s AND j.module_id=%s AND j.component_id=%s '
                    'AND l.enrolment_id::text=%s AND j.state=%s',
                    [shared_progress, component_id, str(learner_id), kind, "MOD-KBC-PMP-M01-SMR",
                     shared_progress, "MOD-KBC-PMP-M01-SMR", component_id, str(learner_id), "waiting_review"])
                scoped_refresh = bool(cur.fetchone()) and context.get("progressEntryId")==shared_progress
            # An accepted submission is a coach's decision; re-completing the
            # activity must not quietly reopen it.
            cur.execute(
                'select id, status, progress_entry_id from "Learner"."learning_reflection_submissions" '
                "where learner_kind = %s and learner_id = %s and activity_type = %s "
                "and activity_id = %s and assignment_topic_id = ''",
                [kind, str(learner_id), context.get("activityType") or "", component_id],
            )
            existing = cur.fetchone()
            if existing and str(existing[1] or "") in TERMINAL_STATUSES:
                if not scoped_refresh or existing[2]==shared_progress:
                    return existing[0]

            evidence = _evidence_filenames(kind, learner_id, component_id)
            cur.execute(
                """
                insert into "Learner"."learning_reflection_submissions" (
                    id, learner_kind, learner_id, learner_name, programme_name,
                    activity_type, activity_id, activity_title, module_title,
                    week_title, planned_otjh, learning_reflection,
                    evidence_files, actual_time_hours, quality_score,
                    progress_entry_id, component_ref, status
                ) values (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s::jsonb, %s, %s,
                    %s, %s, 'submitted_for_tutor_review'
                )
                on conflict (learner_kind, learner_id, activity_type, activity_id, assignment_topic_id)
                do update set
                    activity_title = excluded.activity_title,
                    module_title = excluded.module_title,
                    week_title = excluded.week_title,
                    planned_otjh = excluded.planned_otjh,
                    evidence_files = excluded.evidence_files,
                    actual_time_hours = excluded.actual_time_hours,
                    progress_entry_id = coalesce(excluded.progress_entry_id,
                        "learning_reflection_submissions".progress_entry_id),
                    component_ref = excluded.component_ref,
                    status = 'submitted_for_tutor_review',
                    submitted_at = now()
                where "learning_reflection_submissions".status <> 'accepted'
                  or (%s and "learning_reflection_submissions".progress_entry_id is distinct from %s)
                returning """ + SUBMISSION_AUDIT_SQL + """
                """,
                [
                    str(uuid.uuid4()),
                    kind,
                    str(learner_id),
                    context.get("learnerName") or "",
                    context.get("programmeName") or "",
                    context.get("activityType") or "",
                    component_id,
                    context.get("activityTitle") or "",
                    context.get("moduleTitle") or "",
                    context.get("weekTitle") or "",
                    context.get("plannedOtjh") or "",
                    # The learner was not asked to write one; the evidence is
                    # the submission. Said plainly so the coach knows why the
                    # reflection panel is empty rather than assuming it was
                    # skipped.
                    ACTIVITY_MARKING_REFLECTION,
                    json.dumps(evidence),
                    _actual_time_hours(context.get("actualTimeHours")),
                    100,
                    context.get("progressEntryId"),
                    component_id,
                    scoped_refresh,
                    shared_progress,
                ],
            )
            row = cur.fetchone()
            # `id` is the first audited column, so the existing read is
            # unchanged by widening the clause.
            record_submission_row(row)
            return row[0] if row else None
    except DatabaseError as exc:
        logger.warning("Could not queue %s for marking: %s", component_id, exc)
        return None
