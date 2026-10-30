"""Read-only resolution of explicitly applied review associations. No date matching."""
from functools import wraps

from django.db import transaction

from .models import ReviewSourceReconciliation
from .reconciliation_schema import CompatibleReconciliationQuerySet


def active_links(**filters):
    return CompatibleReconciliationQuerySet(model=ReviewSourceReconciliation, using="default").filter(
        status="APPLIED", **filters,
    )


def for_native(instance_id):
    return active_links(native_review_instance_id=instance_id).first() if instance_id else None


def for_calendar(record):
    if not record or not getattr(record, "review_instance_id", None):
        return None
    link = active_links(calendar_event_id=record.pk).select_related("overlay").first()
    if link:
        validate_record(link, record)
    return link


def validate_record(link, record):
    from .review_sources import ReviewIdentityConflict
    overlay = link.overlay
    if (record.pk != link.calendar_event_id or record.learner_id != link.learner_id
            or record.review_instance_id != link.native_review_instance_id
            or record.review_template_id != link.native_template_id
            or record.event_key != link.native_event_key or record.event_type != "mcr"
            or overlay.source_review_id != link.source_review_id or overlay.learner_id != link.learner_id
            or overlay.event_key != f"imported-review:{link.aptem_review_id}"
            or overlay.owner_email.casefold() != record.owner_email.casefold()):
        raise ReviewIdentityConflict("The reconciled Review association is inconsistent.")


def event_matches(link, event):
    return (str(link.source_review_id) == str(event.get("reviewId"))
            and str(link.learner_id) == str(event.get("learnerId"))
            and link.aptem_review_id == str(event.get("aptemReviewId"))
            and event.get("source") == "mcr")


def require_schedulable(record):
    """Caller holds the calendar row and transaction; imported status is authority."""
    link = for_calendar(record)
    if link:
        from .models import ImportedReviewInstance
        from .views import LearnerCalendarConflict
        from .review_reconciliation import read_source
        from .migrated_reviews import initial_local_status, review_family
        overlay = ImportedReviewInstance.objects.select_for_update().get(pk=link.overlay_id)
        source = read_source(link.source_review_id)
        if (overlay.status not in {"scheduled", "not-scheduled"} or source.get("completed_date")
                or not initial_local_status(source.get("status"))
                or source.get("learner_id") != link.learner_id
                or str(source.get("aptem_review_id")) != link.aptem_review_id
                or review_family(source.get("review_type")) != "MCM"):
            raise LearnerCalendarConflict("Only an unstarted imported Review can be rescheduled.")
    return link


def records_for_key(event_key, *, lock=False):
    from .models import CoachCalendarEvent
    if not event_key.startswith("imported-review:"):
        return []
    result = []
    for link in active_links(aptem_review_id=event_key.removeprefix("imported-review:")).select_related("overlay"):
        query = CoachCalendarEvent.objects.select_for_update() if lock else CoachCalendarEvent.objects
        record = query.get(pk=link.calendar_event_id)
        validate_record(link, record)
        result.append(record)
    return result


def native_write(func):
    """Serialize Native writers with reconciliation on the same parent row."""
    @wraps(func)
    def guarded(instance, *args, **kwargs):
        from curriculum_api.review_instances import get_review_instance
        identity = instance.get("id") if isinstance(instance, dict) else instance
        with transaction.atomic():
            get_review_instance(identity, for_update=True)
            if for_native(identity):
                raise ValueError("This legacy Native review is reconciled. Use its imported Aptem form.")
            return func(instance, *args, **kwargs)
    return guarded
