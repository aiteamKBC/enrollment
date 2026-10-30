"""Dated assignment claims share one daily allowance, including saved drafts."""
import json
from datetime import date
from decimal import Decimal, InvalidOperation

DAILY_LIMIT = Decimal("8")


def daily_totals(entries):
    totals = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            day = date.fromisoformat(entry.get("date", "")).isoformat()
            hours = Decimal(str(entry.get("hours", "")))
        except (TypeError, ValueError, InvalidOperation):
            continue
        if hours.is_finite() and hours > 0:
            totals[day] = totals.get(day, Decimal(0)) + hours
    return totals


def lock_assignment_hours(cur, kind, learner_id):
    # All assignment writers take this lock before component/row locks. It also
    # protects concurrent first saves, for which no row exists to lock yet.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [json.dumps(["assignment-daily-hours", kind, str(learner_id)])])


def saved_daily_totals(cur, kind, learner_id, activity_id, assignment_topic=""):
    cur.execute("""
        SELECT full_submission #> '{monthlyAssignment,timeEntries}'
        FROM "Learner".learning_reflection_submissions
        WHERE learner_kind = %s AND learner_id = %s AND activity_type = 'assignment'
          AND NOT (activity_id = %s AND assignment_topic_id = %s)
        """, [kind, str(learner_id), activity_id, assignment_topic or ""])
    totals = {}
    for (entries,) in cur.fetchall():
        if isinstance(entries, str):
            entries = json.loads(entries)
        for day, hours in daily_totals(entries).items():
            totals[day] = totals.get(day, Decimal(0)) + hours
    return totals


def daily_limit_error(entries, existing=None):
    existing = existing or {}
    for day, hours in sorted(daily_totals(entries).items()):
        used = Decimal(str(existing.get(day, 0)))
        if used + hours > DAILY_LIMIT:
            remaining = max(Decimal(0), DAILY_LIMIT - used)
            return (
                f"{day}: maximum 8 hours per day across all your assignments, including saved drafts. "
                f"Other topics and assignments use {used.normalize():f} hours; "
                f"this topic can claim up to {remaining.normalize():f} hours on this date."
            )
    return ""


def validate_saved_daily_hours(cur, payload):
    monthly = payload.get("monthlyAssignment")
    entries = monthly.get("timeEntries") if isinstance(monthly, dict) else None
    if not daily_totals(entries):
        return
    existing = saved_daily_totals(cur, payload["learnerKind"], payload["learnerId"],
                                 payload["activityId"], payload.get("assignmentTopicId"))
    error = daily_limit_error(entries, existing)
    if error:
        raise ValueError(error)
