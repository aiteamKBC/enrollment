"""Read-time date/status overlay, without metrics, plans or source enrichment."""
from types import SimpleNamespace
from coach_api.selectors.otjh import PROFILE_WINDOW_FIELDS, SOURCE_WINDOW_FIELDS

from .timing import dashboard_stage


def fetch_dashboard_profile_dates(owner_email, profile_ids):
    from coach_api import views as domain

    with dashboard_stage("caseload_query") as stats:
        rows = list(domain.LearnerProfile.objects.annotate(
            coach_email_key=domain.Lower(domain.Trim("coach_email")),
            username_key=domain.Trim("full_name"),
        ).filter(coach_email_key=domain.normalize_email(owner_email), pk__in=profile_ids)
            .exclude(username_key__isnull=True).exclude(username_key="")
            .values("id", "programme_status", "enrolment_id", *PROFILE_WINDOW_FIELDS))
        stats["row_count"] = len(rows)
    source_ids = {row["enrolment_id"] for row in rows if row["enrolment_id"] is not None}
    sources = {}
    if source_ids:
        with dashboard_stage("source_schedule_query") as stats:
            sources = {row["id"]: SimpleNamespace(**row) for row in
                       domain.EnrolmentUser.all_learners.filter(pk__in=source_ids)
                       .values("id", *SOURCE_WINDOW_FIELDS)}
            stats["row_count"] = len(sources)
    return [SimpleNamespace(**row, _caseload_source=sources.get(row["enrolment_id"])) for row in rows]
