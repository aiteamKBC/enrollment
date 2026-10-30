"""Canonical explicit initialization, shared by the coach and reconciliation."""
from django.db import transaction

from .models import ImportedReviewInstance
from .migrated_reviews import initial_local_status, review_family, validate_definition
from .migrated_templates import resolve_template, snapshot_for
from .migrated_template_sync import lock_template_family
from .migrated_signature_rules import requirements_for_definition


def _status_for_existing_booking(owner_email, definition, family, status):
    """Resolve the booking this Review already has, under the learner lock.

    Returns `scheduled` only for a verified, synchronized appointment resolved
    through the durable source linkage; any unresolvable or conflicting
    association leaves the Aptem-derived status untouched, so the explicit
    "Retry linking meeting" action stays available for those.
    """
    from . import imported_booking_state
    from .migrated_reviews import booking_event_type
    from .review_sources import booking_for_imported_review, ReviewIdentityConflict
    from learner_api.models import LearnerProfile

    event_type = booking_event_type(family)
    profile = LearnerProfile.objects.filter(pk=definition["instance"]["learnerId"]).first()
    if not profile or not event_type:
        return status
    try:
        record = booking_for_imported_review(profile, definition["historicalReview"], lock=True)
    except ReviewIdentityConflict:
        return status
    return (ImportedReviewInstance.STATUS_SCHEDULED
            if imported_booking_state.verified_meeting(record, owner_email=owner_email, event_type=event_type)
            else status)


def initialize_imported_review(owner_email, definition):
    if not definition.get("canInitialize") or definition.get("readOnly") is False:
        raise ValueError("This imported review cannot be initialized.")
    family = review_family((definition.get("historicalReview") or {}).get("type"))
    source_id = int(definition["historicalReview"]["id"])
    with transaction.atomic():
        from .views import lock_learner_calendar
        from learner_api.models import LearnerProfile
        learner_id = definition['instance']['learnerId']
        lock_learner_calendar(learner_id)
        current_owner = LearnerProfile.objects.filter(pk=learner_id).values_list('coach_email', flat=True).first()
        if str(current_owner or '').strip().casefold() != owner_email.strip().casefold():
            raise ValueError('The learner assignment changed. Reload before initializing this review.')
        if ImportedReviewInstance.objects.filter(learner_id=learner_id, source_review_id=source_id).exclude(
                owner_email__iexact=owner_email.strip().casefold()).exists():
            raise ValueError('This review is still owned by a different Coach and requires handover.')
        lock_template_family(family)
        template = resolve_template(definition["migratedProgrammeKey"], family, lock=True)
        if template is None:
            raise ValueError("No approved migrated template is assigned.")
        validate_definition(template.definition_json)
        answers = {}
        status = initial_local_status(definition.get("sourceStatus"))
        if family == "PSP":
            from .personal_support_form import initial_answers
            answers = initial_answers(source_id, definition["instance"]["learnerId"], template.definition_json)
            if str(definition.get("sourceStatus", "")).casefold().replace(" ", "-") == "in-progress":
                status = "in-progress"
        elif status == ImportedReviewInstance.STATUS_NOT_SCHEDULED:
            # The meeting can be booked before this working copy exists -- the
            # learner can schedule it from their own calendar. Starting at
            # not-scheduled would then contradict the verified appointment the
            # Start/Submit gates are about to be asked about, so the overlay is
            # born on the booking that is already there. Booking state only:
            # nothing here starts, submits or completes the review.
            status = _status_for_existing_booking(owner_email, definition, family, status)
        overlay, created = ImportedReviewInstance.objects.get_or_create(
            owner_email=owner_email, event_key=definition["instance"]["id"],
            defaults={
                "learner_id": definition["instance"]["learnerId"], "source_review_id": source_id,
                "migrated_template": template, "template_snapshot": snapshot_for(template),
                "signature_requirements": requirements_for_definition(template.definition_json, family), "answers": answers,
                "status": status,
            },
        )
        if (overlay.learner_id != definition["instance"]["learnerId"]
                or overlay.source_review_id != source_id or not overlay.template_snapshot):
            raise ValueError("An existing overlay has a different source association.")
        return overlay, created
