"""Read-only completion-date repair for older dashboard projections."""


def saved_completed_review_dates(rows, *, owner_email, owner_name):
    """Bulk-read owned history without regenerating curriculum schedules.

    Aptem learners use the existing authoritative imported-history adapter;
    legacy native calendar rows must not override their imported occurrences.
    """
    from coach_api import views as domain
    from coach_api.models import CoachCalendarEvent

    rows = list(rows)
    if not rows:
        return {}
    imported = domain.dashboard_imported_completed_review_dates(
        [row.id for row in rows], owner_email=owner_email, owner_name=owner_name,
    )
    native = [row for row in rows if row.id not in imported]
    events = []
    if native:
        records = list(CoachCalendarEvent.objects.filter(
            owner_email=domain.normalize_email(owner_email),
            learner_id__in=[row.id for row in native], status="completed",
        ).exclude(event_key__startswith="imported-review:").values(
            "learner_id", "event_type", "review_template_id", "review_completed_at",
        ))
        types = domain.review_type_fields_by_template(record["review_template_id"] for record in records)
        events = [{
            "learnerId": str(record["learner_id"]), "source": record["event_type"],
            "status": "completed",
            "reviewCompletedAt": record["review_completed_at"].isoformat() if record["review_completed_at"] else None,
            **types.get(record["review_template_id"], {}),
        } for record in records]
    return {**domain.latest_completed_review_dates_from_events(native, events), **imported}


def apply_completed_review_dates(learner, dates):
    """Keep table, sorting, export and risk consumers on the same values."""
    from coach_api import views as domain

    # Keep the dashboard's ISO date contract; the shared selector returns labels.
    pr = domain.format_iso_date_value(dates.get("lastPr"))
    mcm = domain.format_iso_date_value(dates.get("lastMcm"))
    learner.update(
        lastPr=pr, lastMcm=mcm,
        lastProgressReview=pr or "--", lastReview=mcm or "--",
    )
