"""One audited shape for ``"Learner"."learning_reflection_submissions"``.

Three different places write that table and none of them is the ORM:

* ``reflection_submissions`` -- the learner submitting the seven-step reflection.
* ``marking_queue_entry`` -- a completed activity being put in the queue.
* ``coach_api.views`` -- the coach's accept/reject decision.

They must all record the *same* columns. ``versioning`` stores a column that a
write did not supply as ``None``, so if one path reported ``activity_type`` and
another did not, every alternation between them would read as that field being
removed and added again -- a history made entirely of edits nobody performed.
So the column list lives here, once, and each write path returns exactly it.

``id`` is deliberately first. Every one of those statements already ends in
``returning id`` and reads ``row[0]``, so widening the clause in front of the
existing reads would have moved them.

What is not collected, and why: ``learning_reflection``, ``application_text``,
``full_submission``, ``evidence_files`` and the KSB/confidence documents are the
learner's own work. They are large, they are rewritten wholesale on every save,
and the trail's job here is to say who submitted, who decided, and what the
decision was -- not to keep a second copy of the submission itself.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TABLE = 'learning_reflection_submissions'

#: The audited columns, in the order every write path must return them.
SUBMISSION_AUDIT_COLUMNS = (
    'id', 'learner_kind', 'learner_id', 'learner_name', 'programme_name',
    'activity_type', 'activity_id', 'activity_title', 'module_title', 'week_title',
    'planned_otjh', 'actual_time_hours', 'quality_score', 'status',
    'coach_feedback', 'reviewed_by', 'reviewed_at', 'submitted_at', 'component_ref',
)

#: The same list as a SQL fragment, for `returning`.
SUBMISSION_AUDIT_SQL = ', '.join(SUBMISSION_AUDIT_COLUMNS)


def record_submission_row(row):
    """Record one submission write in the Audit Trail. Never raises.

    ``row`` is the tuple a ``returning SUBMISSION_AUDIT_SQL`` clause produced.
    A failure here must never fail the save that caused it: a learner's
    submitted work is worth more than the line describing it.
    """
    if not row:
        return
    try:
        from system_audit.writes import record_table_rows

        record_table_rows(
            TABLE,
            [dict(zip(SUBMISSION_AUDIT_COLUMNS, row))],
            using='enrolment',
        )
    except Exception:
        logger.warning('Could not record a submission write.', exc_info=True)
