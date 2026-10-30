"""Bulk marking reads for the Coach Dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import connections

if TYPE_CHECKING:
    from coach_api.services.dashboard.context import CoachDashboardContext


def dashboard_marking_projection(context: CoachDashboardContext) -> dict:
    """Return one marking summary row per learner."""
    if not context.enrolment_ids:
        return {"summary": {"pendingItems": 0}, "items": []}

    sql = """
        SELECT learner_id,
               MIN(learner_name) AS learner_name,
               COUNT(*) AS pending_count,
               MIN(submitted_at) AS oldest_pending,
               MAX(submitted_at) AS latest_pending
          FROM "Learner".learning_reflection_submissions
         WHERE learner_id = ANY(%s)
           AND status IN ('submitted_for_tutor_review', 'escalated')
         GROUP BY learner_id
         ORDER BY oldest_pending, learner_id
    """
    with connections["enrolment"].cursor() as cursor:
        cursor.execute(sql, [context.enrolment_ids])
        rows = cursor.fetchall()

    profile_by_enrolment = {
        str(row.enrolment_id): row
        for row in context.rows
        if getattr(row, "enrolment_id", None)
    }
    items = []
    total = 0
    for learner_id, learner_name, count, oldest, latest in rows:
        profile = profile_by_enrolment.get(str(learner_id))
        if profile is None:
            continue
        count = int(count or 0)
        total += count
        items.append({
            "id": str(profile.id),
            "learnerId": str(profile.id),
            "learner": learner_name or getattr(profile, "full_name", "") or "Learner",
            "programme": getattr(profile, "programme", "") or "--",
            "group": getattr(profile, "group_name", "") or "--",
            "pendingEvidence": count,
            "submittedAt": oldest.isoformat() if oldest else None,
            "lastSubmissionIso": latest.isoformat() if latest else None,
        })
    return {"summary": {"pendingItems": total}, "items": items}
