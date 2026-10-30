"""Learner Aptem reservations, serialized before the shared Graph sync step."""
from django.db import transaction
from django.db.models import Q

from coach_api.models import CoachCalendarEvent, ImportedReviewInstance


def booking_slot(record):
    return record.scheduled_date, record.scheduled_time, record.duration_minutes


def expected_booking(payload):
    from coach_api.validation import ObjectValidator
    # Use the same validators as the coach's explicit Aptem reschedule action.
    value = payload.get("expectedBooking")
    if not isinstance(value, dict):
        raise ValueError("Refresh this booking before rescheduling.")
    validator = ObjectValidator(value)
    slot = (
        validator.iso_date("scheduledDate", required=True),
        validator.clock_time("scheduledTime", required=True),
        validator.integer("durationMinutes", required=True, minimum=15, maximum=480),
    )
    validator.check()
    return slot


def schedule_overlay_for_booking(record_id, event):
    """Put the owned working copy on the meeting Graph has just confirmed.

    The Coach booking endpoint ends with this same transition; without it here
    a learner-booked Review stays not-scheduled while its calendar row is
    booked, and the Start/Submit gates -- which read the lifecycle, not the
    calendar -- hide actions the booking has already earned.

    Runs after the Graph call, never during it, and takes the established
    learner -> calendar -> overlay lock order. Booking state only: an overlay
    that is started, submitted, signed or completed is left untouched.
    """
    from coach_api import views, imported_booking_state
    from coach_api.migrated_reviews import booking_event_type, review_family
    from coach_api.review_sources import linked_booking, linked_overlay, ReviewIdentityConflict

    event_type = booking_event_type(review_family(event.get("importedReviewType")))
    if not event_type:
        return False
    with transaction.atomic():
        views.lock_learner_calendar(event["learnerId"])
        record = CoachCalendarEvent.objects.select_for_update().get(pk=record_id)
        overlays = list(ImportedReviewInstance.objects.select_for_update().filter(
            learner_id=event["learnerId"],
        ).filter(Q(source_review_id=event["reviewId"]) | Q(event_key=event["eventKey"])))
        try:
            overlay = linked_overlay(event, overlays)
            if overlay is None or linked_booking(event, [record], overlay) is None:
                return False
        except ReviewIdentityConflict:
            return False
        return imported_booking_state.schedule_for_verified_meeting(
            overlay, record, owner_email=record.owner_email, event_type=event_type,
        )


def reserve_imported_booking(candidate, event, expected_slot, *, notes=None, expected_notes=None):
    """Re-read identity/lifecycle and compare the caller's snapshot under locks.

    Only the requested slot and explicitly supplied notes may change. Returning
    False means an already-synced slot needs no Graph request (including a retry
    or a notes-only edit). No source review, answer or signature is written.

    A candidate with no primary key is this Review's first local appointment:
    the row is created here, inside the same transaction and learner lock that
    validates it, so a refused attempt commits nothing for the next booking to
    find. A row a concurrent request created first is adopted and revalidated
    rather than duplicated.
    """
    from coach_api import views
    from coach_api.migrated_reviews import initial_local_status, review_family, booking_event_type
    from coach_api.review_reconciliation import read_source, ReconciliationConflict
    from coach_api.review_sources import linked_booking, linked_overlay, ReviewIdentityConflict
    from coach_api import imported_meeting_ownership as meeting_ownership

    desired = booking_slot(candidate)
    conflict = views.LearnerCalendarConflict
    ownership_active = meeting_ownership.enabled_for(event)
    if ownership_active:
        try:
            meeting_ownership.require_activation_ready()
        except meeting_ownership.MeetingOwnershipError:
            raise conflict("This imported meeting needs ownership reconciliation.") from None
    with transaction.atomic():
        views.lock_learner_calendar(candidate.learner_id)
        if candidate.pk is None:
            record, _created = CoachCalendarEvent.objects.get_or_create(
                event_key=candidate.event_key,
                defaults={name: getattr(candidate, name) for name in (
                    "owner_email", "owner_name", "learner_id", "learner_name", "learner_email",
                    "event_type", "sequence", "target_date",
                )},
            )
        else:
            record = CoachCalendarEvent.objects.select_for_update().get(pk=candidate.pk)
        overlays = list(ImportedReviewInstance.objects.select_for_update().filter(
            learner_id=event["learnerId"],
        ).filter(Q(source_review_id=event["reviewId"]) | Q(event_key=event["eventKey"])))
        try:
            overlay = linked_overlay(event, overlays)
            source = read_source(event["reviewId"], lock=True)
            if linked_booking(event, [record], overlay) is None:
                raise conflict("The imported review booking changed. Refresh before trying again.")
        except (ReviewIdentityConflict, ReconciliationConflict) as exc:
            raise conflict(str(exc)) from exc
        if (
            str(source.get("learner_id")) != str(event["learnerId"])
            or f"imported-review:{source.get('aptem_review_id')}" != event["eventKey"]
            or booking_event_type(review_family(source.get("review_type"))) != record.event_type
            or source.get("completed_date")
            or not initial_local_status(source.get("status"))
            or record.owner_email.casefold() != candidate.owner_email.casefold()
            or record.status not in {"not-scheduled", "scheduled"}
            or (overlay and (overlay.status not in {"not-scheduled", "scheduled"} or overlay.completed_at))
        ):
            raise conflict("Only an unstarted imported review can be booked or rescheduled. Refresh this review.")
        current = booking_slot(record)
        if current != expected_slot and current != desired:
            raise conflict("This booking was changed by another request. Refresh before rescheduling.")
        if notes is not None and expected_notes is not None and record.notes not in {expected_notes, notes}:
            raise conflict("The booking notes were changed by another request. Refresh before saving.")
        if ownership_active and record.sync_state == CoachCalendarEvent.SYNC_SYNCED:
            try:
                meeting_ownership.require_synced_owner(record, event)
            except meeting_ownership.MeetingOwnershipError:
                raise conflict("This imported meeting ownership changed. Refresh before rescheduling.") from None
        if current == desired and record.sync_state == CoachCalendarEvent.SYNC_SYNCED:
            if notes is not None and record.notes != notes:
                record.notes = notes
                record.save(update_fields=["notes", "updated_at"])
            return record, False
        record.scheduled_date, record.scheduled_time, record.duration_minutes = desired
        record.status = CoachCalendarEvent.STATUS_SCHEDULED
        if notes is not None:
            record.notes = notes
        return views.persist_calendar_sync_reservation(record, review_event=event), True
