"""Read-only Review OTJ inputs used by frozen Progress Review snapshots.

This module deliberately projects only planned Review hours and the subset due
at the snapshot cutoff. Actual hours belong to ``LearnerProfile.completed_hours``
and are never calculated, refreshed, or persisted here.
"""
from __future__ import annotations

from datetime import date, datetime

from django.utils.dateparse import parse_date

from .training_plan_targets import as_of_date


def _text(value):
    return "" if value is None else str(value).strip()


def _date_value(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return parse_date(_text(value)) if _text(value) else None


def review_planned_target_hours(
    learner_profile, *, as_of, learner_start_date=None, learner_end_date=None,
):
    """Return ``(planned, target_due)`` qualifying Review OTJ hours.

    Native uses Curriculum recurrence; Aptem uses its imported review dates. The full
    learner programme window supplies planned hours; the same window capped at
    the UK business date of ``as_of`` supplies target-due hours.
    """
    if learner_profile is None:
        return 0.0, 0.0

    learner_id = getattr(learner_profile, "id", None)
    start_date = _date_value(
        learner_start_date
        or getattr(learner_profile, "learner_start_date", None)
        or getattr(learner_profile, "start_date", None)
    )
    end_date = _date_value(
        learner_end_date
        or getattr(learner_profile, "learner_end_date", None)
        or getattr(learner_profile, "end_date", None)
    )
    if not learner_id or not start_date or not end_date:
        return 0.0, 0.0

    from coach_api.review_sources import resolve_review_source
    if resolve_review_source(profile_aptem_id=getattr(learner_profile, "aptem_id", None)).kind == "aptem":
        from coach_api.migrated_otjh import planned_meetings
        planned = sum(row["hours"] for row in planned_meetings(learner_profile, start_date, end_date))
        cutoff = min(as_of_date(as_of), end_date)
        due = sum(row["hours"] for row in planned_meetings(learner_profile, start_date, cutoff)) if cutoff >= start_date else 0
        return planned, due

    from curriculum_api import review_otjh

    programme_id = _text(getattr(learner_profile, "programme_id", ""))
    programme_status = _text(getattr(learner_profile, "programme_status", ""))
    template_cache = {}
    planned = review_otjh.planned_hours(
        programme_id,
        learner_id,
        programme_status,
        start_date,
        start_date,
        end_date,
        template_cache=template_cache,
    )

    due = 0.0
    due_through = as_of_date(as_of)
    if due_through >= start_date:
        due = review_otjh.planned_hours(
            programme_id,
            learner_id,
            programme_status,
            start_date,
            start_date,
            min(due_through, end_date),
            template_cache=template_cache,
        )
    return planned, due
