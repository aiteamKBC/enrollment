"""Live, slim Profile OTJH overlay for cached and durable Dashboard rows."""
from learner_api.canonical_learning import otjh_summary_bulk
from coach_api.selectors.otjh import learner_programme_window

from .timing import dashboard_stage


def refresh_otjh_rows(payload, rows, *, today):
    from coach_api import views as domain

    rows_by_id = {str(row.id): row for row in rows}
    with dashboard_stage("otjh_enrichment") as stats:
        totals = otjh_summary_bulk([getattr(row, "enrolment_id", None) for row in rows])
        stats["row_count"] = len(totals)
    for learner in payload.get("learners") or []:
        row = rows_by_id.get(str(learner.get("id")))
        facts = totals.get(getattr(row, "enrolment_id", None))
        # Never use another profile's facts through a stale enrolment link.
        if facts is not None and facts["profile_id"] != int(row.id):
            facts = None
        facts = facts or {}
        start, end = learner_programme_window(row, getattr(row, "_caseload_source", None))
        learner.update(
            otjhCompleted=facts.get("actual"), otjhPlanned=facts.get("planned"),
            otjhProgrammeStartDate=domain.format_date(start),
            plannedEndDate=domain.format_date(end),
        )
        domain.apply_otjh_to_date_metrics(learner, today=today)
        learner["otjhTarget"] = learner["otjhTargetAsOfToday"]
        # Missing actual hours must remain unavailable, not a fabricated zero.
        if facts.get("actual") is None:
            learner.update(otjhProgressAsOfToday=None, otjhShortfallHours=None,
                           otjhDeltaHours=None, otjhRagStatus="unavailable")
    return payload
