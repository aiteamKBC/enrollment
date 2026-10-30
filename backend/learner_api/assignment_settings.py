"""Author settings that decide how an assignment is presented to learners.

``learnerTitle`` is the name the programme team uses with learners; the
curriculum title often still carries the imported legacy LMS name.

``isMonthlyAssignment`` marks the assignment learners submit through the
eight-step monthly submission. Until an author flags one, every assignment uses
that submission (the original behaviour). Once any assignment in a learner's
plan is flagged, only the flagged ones do and the rest complete like any other
activity. ``isMonthlyAssignment`` in frontend/src/utils/learnerJourney.ts
applies the same rule to the learner's page.
"""
import json
import logging

from django.db import DatabaseError, connections

from curriculum_api.week_visibility import visible_child_sql

logger = logging.getLogger(__name__)


def _settings(value):
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def learner_title(settings):
    """The title shown to learners, or '' when the curriculum title applies."""
    return " ".join(str(_settings(settings).get("learnerTitle") or "").split())


def flagged_monthly(settings):
    # Matches the SQL test below: JSON true (or the string "true"), nothing else.
    value = _settings(settings).get("isMonthlyAssignment")
    return value is True or value == "true"


def monthly_assignment_required(component_id, source):
    """Whether completing this assignment needs the eight-step submission.

    The plan is the learner's assigned modules, the same scope the learner page
    reads its components from, so the page and this check agree on which
    assignments are monthly. Any failure keeps the original requirement.
    """
    from .learning_plan import effective_training_plan
    try:
        module_ids = sorted({
            str(entry.get("moduleId") or "").strip()
            for entry in effective_training_plan(source) or [] if isinstance(entry, dict)
        } - {""})
        with connections["enrolment"].cursor() as cur:
            cur.execute("SELECT settings_json FROM curriculum.components WHERE id = %s", [component_id])
            row = cur.fetchone()
            if flagged_monthly(row[0] if row else None) or not module_ids:
                return True
            cur.execute(
                "SELECT 1 FROM curriculum.components "
                "WHERE module_catalogue_id = ANY(%s) AND type = 'assignment' "
                f"AND {visible_child_sql('components')} "
                "AND settings_json::jsonb ->> 'isMonthlyAssignment' = 'true' LIMIT 1",
                [module_ids],
            )
            return cur.fetchone() is None
    except DatabaseError as exc:
        logger.warning("Could not read monthly assignment flags for %s: %s", component_id, exc)
        return True
